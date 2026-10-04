"""Run generic extraction, evidence validation, independent review and export."""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from .model import EXTRACTION_SCHEMA, REVIEW_SCHEMA
from .prompts import PROMPT_VERSION, extraction_prompt, review_prompt
from .sources import load_sources, chunk_document, source_inventory
from .validation import locate_quote, validate_rule, validate_export


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_capture_attempts(supplemental_dir, sources):
    """Read optional acquisition metadata for reporting, never source evidence."""
    if supplemental_dir is None:
        return {}, []
    path = Path(supplemental_dir) / "fetch_log.json"
    diagnostics = []

    def diagnose(code, reason, **details):
        diagnostics.append(dict(type="capture_log_diagnostic", code=code,
                                reason=reason, path=str(path), **details))

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, []
    except (OSError, UnicodeError, ValueError) as exc:
        diagnose("unreadable_capture_log", "Capture log ignored: {}".format(exc))
        return {}, diagnostics
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        diagnose("invalid_capture_log", "Capture log must contain an entries array; metadata ignored.")
        return {}, diagnostics

    attempts, seen = {}, set()
    for index, entry in enumerate(payload["entries"]):
        if not isinstance(entry, dict):
            diagnose("invalid_capture_entry", "Capture entry must be an object; entry ignored.", entry=index)
            continue
        doc_id = entry.get("doc_id")
        if not isinstance(doc_id, str) or doc_id not in sources:
            diagnose("unknown_capture_source", "Capture entry must identify a manifest source; entry ignored.",
                     entry=index)
            continue
        if doc_id in seen:
            attempts.pop(doc_id, None)
            diagnose("duplicate_capture_source", "Repeated document ID; all capture metadata for this source ignored.",
                     entry=index, doc_id=doc_id)
            continue
        seen.add(doc_id)
        state, reason = entry.get("state"), entry.get("reason")
        final_url, timestamp = entry.get("finalurl"), entry.get("timestamp")
        if (not isinstance(state, str) or not state.strip() or not isinstance(reason, str)
                or (final_url is not None and not isinstance(final_url, str))
                or (timestamp is not None and not isinstance(timestamp, str))):
            diagnose("invalid_capture_entry", "Capture status/reason must be strings; final URL/timestamp must be strings or null; entry ignored.",
                     entry=index, doc_id=doc_id)
            continue
        attempts[doc_id] = {"status": state, "reason": reason, "final_url": final_url}
        if timestamp is not None:
            attempts[doc_id]["timestamp"] = timestamp
    return attempts, diagnostics


def normalized(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def stable_id(prefix, value):
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return prefix + digest


def build_batches(sources, selected=None, max_chars=180000, batch_chars=48000):
    chunks = []
    for doc_id, source in sorted(sources.items()):
        if not source.text or (selected and doc_id not in selected):
            continue
        for chunk in chunk_document(source, max_chars=max_chars):
            chunks.append(dict(chunk, source_url=source.url, source_type=source.source_type,
                               manifest_jurisdictions=source.jurisdictions,
                               retrieved_at=source.retrieved_at, source_sha256=source.sha256,
                               total_document_characters=len(source.text)))
    batches, current, size = [], [], 0
    for chunk in chunks:
        if current and size + len(chunk["text"]) > batch_chars:
            batches.append(current)
            current, size = [], 0
        current.append(chunk)
        size += len(chunk["text"])
    if current:
        batches.append(current)
    return batches


def prepare_candidate(raw, source, chunk_id, index, as_of, schema, sources):
    candidate_id = stable_id("c-", [source.doc_id, chunk_id, index, raw])
    record = {key: raw.get(key) for key in [
        "jurisdiction", "level", "category", "status", "title", "requirement", "key_value",
        "coverage_conditions", "exemptions", "effective_date", "citation", "quoted_span",
        "confidence", "conflict_flag", "conflict_note", "interaction"]}
    record.update(team_rule_id=candidate_id, source_doc_id=source.doc_id, source_url=source.url,
                  retrieved_at=source.retrieved_at, as_of=as_of, overrides=[])
    record.update({key: raw.get(key) for key in ["conditions", "end_date", "temporal_notes",
                                              "penalty", "limitations", "related_citations"]})
    evidence, problems = [], []
    for field, quote in [("requirement", raw.get("quoted_span", ""))] + [
            (e["field"], e["quote"]) for e in raw.get("evidence", [])]:
        match = locate_quote(source.text, quote)
        if not match:
            problems.append({"code": "unsupported_quote", "message": "No source match for " + field})
            continue
        header_separator = re.search(r"(?:\r\n|\n|\r)[ \t]*(?:\r\n|\n|\r)", source.text)
        if header_separator and match["start"] < header_separator.end():
            problems.append({"code": "header_quote", "message": "A source header is not legal evidence."})
            continue
        if field == "requirement" and not evidence:
            record["quoted_span"] = match["quote"]
        evidence.append(dict(match, field=field, source_doc_id=source.doc_id, source_url=source.url))
    if raw.get("status") == "unverified":
        problems.append({"code": "unverified_status", "message": "Legal status is not established for the query date."})
    else:
        problems.extend(validate_rule(record, schema, sources))
    if raw.get("end_date"):
        try:
            if date.fromisoformat(raw["end_date"]) < date.fromisoformat(as_of):
                problems.append({"code": "historical_rule", "message": "The explicit validity interval ended before the query date."})
        except ValueError:
            problems.append({"code": "invalid_end_date", "message": "end_date must be a full valid date."})
    # The source must establish the relation between a date and the clause. A model
    # is not allowed to supply a date without an associated evidence passage.
    if record.get("effective_date") and not any(
            e["field"].strip().lower().replace(" ", "_") in
            {"effective_date", "effective_date_calculation", "temporal_notes"} for e in evidence):
        problems.append({"code": "date_evidence_missing", "message": "An effective date requires explicit field evidence."})
    return {"candidate_id": candidate_id, "doc_id": source.doc_id, "chunk_id": chunk_id,
            "rule": record, "evidence": evidence, "problems": problems,
            "raw_extraction": raw}


def checked_generate(model, prompt, schema, stage, check):
    """Retry one logically incomplete response without replaying a poisoned cache."""
    rejected = []
    for attempt in range(2):
        output, metadata = model.generate(prompt, schema, stage)
        try:
            check(output)
        except ValueError as exc:
            invalidate = getattr(model, "invalidate_response", None)
            if not callable(invalidate) or getattr(model, "offline", False):
                raise
            invalidate(metadata, str(exc))
            rejected.append(dict(metadata, rejection_reason=str(exc)))
            if attempt:
                raise
        else:
            if rejected:
                metadata = dict(metadata, rejected_responses=rejected)
            return output, metadata


def extraction_blocked(document):
    """Recognize explicit service/refusal failures in extraction diagnostics.

    This never classifies the source law itself and never triggers a workaround
    or retry. A model's refusal-shaped empty result is an evidence gap.
    """
    from .repair import _refusal_reason
    diagnostics = "\n".join([document.get("summary", "")] + document.get("issues", []))
    return bool(_refusal_reason({"reason": diagnostics}) or re.search(
        r"\bcontent filter(?:ing)?\b|\bextraction (?:could not|cannot|can't) be completed\b"
        r"|\b(?:response|request) (?:was|is|has been) (?:blocked|filtered)\b"
        r"|\b(?:unable|cannot|can't) to (?:perform|complete) (?:the )?extraction\b",
        diagnostics, flags=re.IGNORECASE))


def process_batch(batch, as_of, schema, sources, model, checkpoint=None):
    from .repair import _refusal_reason, repair_candidates

    expected = {item["chunk_id"]: item for item in batch}
    def check_documents(output):
        actual = [item["chunk_id"] for item in output["documents"]]
        if len(actual) != len(set(actual)) or set(actual) != set(expected):
            raise ValueError("Model response does not cover every requested chunk exactly once.")
        for document in output["documents"]:
            if document["doc_id"] != expected[document["chunk_id"]]["doc_id"]:
                raise ValueError("Model returned a mismatched document ID.")

    if checkpoint is None:
        output, extraction_meta = checked_generate(
            model, extraction_prompt(batch, as_of), EXTRACTION_SCHEMA, "extract", check_documents)
    else:
        output, extraction_meta = checkpoint
        check_documents(output)
    candidates, docs = [], []
    for document in output["documents"]:
        chunk = expected[document["chunk_id"]]
        if document["doc_id"] != chunk["doc_id"]:
            raise ValueError("Model returned a mismatched document ID.")
        finding = {key: value for key, value in document.items() if key != "rules"}
        finding["extraction_status"] = "blocked" if extraction_blocked(document) else "completed"
        docs.append(finding)
        for i, raw in enumerate(document["rules"]):
            candidate = prepare_candidate(raw, sources[document["doc_id"]],
                                          document["chunk_id"], i, as_of, schema, sources)
            candidate["document_type"] = document["document_type"]
            candidate["extraction_status"] = finding["extraction_status"]
            if finding["extraction_status"] == "blocked":
                candidate["problems"].append({
                    "code": "extraction_blocked",
                    "message": "The source extraction explicitly reported a filter or refusal; partial candidates are withheld.",
                })
            candidates.append(candidate)
    blocked_documents = [d for d in docs if d.get("extraction_status") == "blocked"]
    if blocked_documents:
        extraction_meta = dict(extraction_meta, status="blocked",
                               blocked_doc_ids=sorted({d["doc_id"] for d in blocked_documents}))
    eligible = [c for c in candidates if not c["problems"]]
    review_meta = None
    if eligible:
        review_input = [{"candidate_id": c["candidate_id"], "doc_id": c["doc_id"],
                         "rule": c["rule"], "evidence": c["evidence"]} for c in eligible]
        def check_reviews(output):
            actual = [r["candidate_id"] for r in output["reviews"]]
            if set(actual) != {c["candidate_id"] for c in eligible} or len(actual) != len(set(actual)):
                raise ValueError("Review did not cover every candidate exactly once.")

        review, review_meta = checked_generate(
            model, review_prompt(batch, review_input, as_of), REVIEW_SCHEMA, "review", check_reviews)
        reviews = {r["candidate_id"]: r for r in review["reviews"]}
        blocked_reviews = []
        for candidate in eligible:
            candidate["review"] = reviews[candidate["candidate_id"]]
            refusal = _refusal_reason(candidate["review"])
            if refusal:
                candidate["review_status"] = "blocked"
                candidate["problems"].append({"code": "review_blocked", "message": refusal})
                blocked_reviews.append(candidate["candidate_id"])
                for finding in docs:
                    if finding["chunk_id"] == candidate["chunk_id"]:
                        finding["review_status"] = "blocked"
                        finding.setdefault("review_issues", []).append({
                            "candidate_id": candidate["candidate_id"], "reason": refusal,
                        })
        if blocked_reviews:
            review_meta = dict(review_meta, status="blocked", blocked_candidate_ids=sorted(blocked_reviews))
    candidates, repair_calls, repair_issues = repair_candidates(
        batch, candidates, model, sources, as_of, schema)
    return {"documents": docs, "candidates": candidates,
            "model_calls": [m for m in [extraction_meta, review_meta] if m] + repair_calls,
            "repair_issues": repair_issues}


def consolidate(candidates):
    """Conservatively combine exact claims. Semantic overlaps stay visible for review.

    Changing prose cannot silently overwrite a materially different rule. This is
    deliberately conservative: a later review can merge semantically equal claims.
    """
    groups = defaultdict(list)
    for c in candidates:
        r = c["rule"]
        key = tuple(normalized(r.get(k)) for k in ["jurisdiction", "category", "citation", "requirement",
                                                  "key_value", "coverage_conditions", "conditions", "exemptions",
                                                  "effective_date", "end_date", "status", "penalty",
                                                  "temporal_notes", "interaction"])
        groups[key].append(c)
    rules, audit, overlaps = [], [], []
    potential = defaultdict(list)
    for key, group in sorted(groups.items()):
        chosen = max(group, key=lambda c: (c["rule"].get("confidence", 0), len(c["evidence"])))
        rule = dict(chosen["rule"])
        rule["team_rule_id"] = stable_id("r-", key)
        rule["evidence"] = [e for c in group for e in c["evidence"]]
        rule["source_doc_ids"] = sorted({c["doc_id"] for c in group})
        rules.append(rule)
        audit.append({"team_rule_id": rule["team_rule_id"],
                      "candidate_ids": [c["candidate_id"] for c in group],
                      "source_doc_ids": rule["source_doc_ids"],
                      "evidence": rule["evidence"], "review": chosen.get("review")})
        potential[(normalized(rule["jurisdiction"]), rule["category"], normalized(rule["citation"]))].append(rule)
    for key, group in potential.items():
        if len(group) > 1:
            overlaps.append({"type": "possible_semantic_overlap", "jurisdiction": group[0]["jurisdiction"],
                             "citation": group[0]["citation"],
                             "team_rule_ids": [r["team_rule_id"] for r in group],
                             "reason": "Same citation/category may contain distinct obligations or duplicate summaries; review before merging."})
    return rules, audit, overlaps


def run(pack, output_dir, model, as_of="2026-10-01", supplemental_dir=None,
        selected=None, workers=3, max_chars=180000, batch_chars=48000, resume=False):
    date.fromisoformat(as_of)
    pack, output_dir = Path(pack), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = load_sources(pack, supplemental_dir=supplemental_dir)
    if selected and set(selected) - set(sources):
        raise ValueError("Unknown document IDs: " + ", ".join(sorted(set(selected) - set(sources))))
    schema = json.loads((pack / "schema/rule_record.schema.json").read_text(encoding="utf-8"))
    batches = build_batches(sources, selected=selected, max_chars=max_chars, batch_chars=batch_chars)
    write_json(output_dir / "source_inventory.json", source_inventory(sources))
    candidates, docs, calls, failures, repair_issues = [], [], [], [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, batch in enumerate(batches):
            checkpoint = None
            if resume:
                from .resume import load_extraction_checkpoint
                checkpoint = load_extraction_checkpoint(
                    output_dir / "batches" / ("{:03d}.json".format(i)), model.cache_dir,
                    extraction_prompt(batch, as_of), EXTRACTION_SCHEMA)
            futures[pool.submit(process_batch, batch, as_of, schema, sources, model, checkpoint)] = (i, batch)
        for future in as_completed(futures):
            i, batch = futures[future]
            try:
                result = future.result()
                candidates.extend(result["candidates"])
                docs.extend(result["documents"])
                calls.extend(result["model_calls"])
                repair_issues.extend(result.get("repair_issues", []))
                blocked = [d for d in result["documents"] if d.get("extraction_status") == "blocked"]
                if blocked:
                    failures.append({"batch": i, "doc_ids": sorted({d["doc_id"] for d in blocked}),
                                     "type": "extraction_blocked",
                                     "reason": "The model explicitly reported an incomplete or filtered extraction; no workaround attempted.",
                                     "document_findings": blocked})
                blocked_review = [d for d in result["documents"] if d.get("review_status") == "blocked"]
                if blocked_review:
                    failures.append({"batch": i, "doc_ids": sorted({d["doc_id"] for d in blocked_review}),
                                     "type": "review_blocked",
                                     "reason": "The model explicitly reported a filtered or refused independent review; affected candidates were withheld without repair.",
                                     "document_findings": blocked_review})
                write_json(output_dir / "batches" / ("{:03d}.json".format(i)), result)
                print("Batch {}/{} complete: {} candidate rules ({})".format(
                    i + 1, len(batches), len(result["candidates"]),
                    ", ".join(c["doc_id"] for c in batch)), flush=True)
            except Exception as exc:
                failure = {"batch": i, "doc_ids": sorted({b["doc_id"] for b in batch}),
                           "type": "extraction_failure", "reason": str(exc)}
                failures.append(failure)
                print("Batch {}/{} failed: {}".format(i + 1, len(batches), exc), flush=True)
    candidates.sort(key=lambda c: c["candidate_id"])
    accepted = [c for c in candidates if not c["problems"] and c.get("review", {}).get("decision") == "accept"]
    from .consolidation import consolidate_candidates
    rules, audit, overlaps, consolidation_calls = consolidate_candidates(accepted, model, sources, as_of)
    calls.extend(consolidation_calls)
    queue = list(failures) + overlaps + repair_issues
    for c in candidates:
        if c not in accepted and not c.get("resolved_by"):
            queue.append({"type": "candidate_review", "candidate_id": c["candidate_id"], "doc_id": c["doc_id"],
                          "rule": c["rule"], "problems": c["problems"], "review": c.get("review")})
    for d in docs:
        if d["issues"]:
            queue.append({"type": "document_issues", "doc_id": d["doc_id"], "issues": d["issues"]})
    # Load the advisory log only after model work. It cannot supply source text,
    # change availability, or influence extraction and consolidation decisions.
    capture_attempts, capture_diagnostics = load_capture_attempts(supplemental_dir, sources)
    queue.extend(capture_diagnostics)
    for source in sources.values():
        if not source.text:
            queue.append({"type": "missing_source", "doc_id": source.doc_id, "source_url": source.url,
                          "reason": "No local source body. A URL is not evidence of its contents."})
            if source.doc_id in capture_attempts:
                queue[-1]["capture_attempt"] = capture_attempts[source.doc_id]
    counts = Counter(c["doc_id"] for c in candidates)
    accepted_counts = Counter(c["doc_id"] for c in accepted)
    failed_docs = {doc for f in failures for doc in f["doc_ids"]}
    processed = {d["doc_id"] for d in docs if d.get("extraction_status") != "blocked"
                 and d.get("review_status") != "blocked"} - failed_docs
    coverage = []
    for source in sources.values():
        status = ("failed" if source.doc_id in failed_docs else "processed" if source.doc_id in processed
                  else "not_selected" if source.text else "missing_text")
        coverage.append({"doc_id": source.doc_id, "status": status,
                         "candidate_count": counts[source.doc_id], "accepted_candidate_count": accepted_counts[source.doc_id],
                         "document_findings": [d for d in docs if d["doc_id"] == source.doc_id]})
        if source.doc_id in capture_attempts:
            coverage[-1]["capture_attempt"] = capture_attempts[source.doc_id]
    payload = {"rules": rules}
    report = validate_export(payload, schema, sources)
    report.update({"as_of": as_of, "prompt_version": PROMPT_VERSION,
                   "generated_at": datetime.now(timezone.utc).isoformat(), "source_pack": str(pack),
                   "disclaimer": "Not legal advice. Automated extraction with unresolved evidence gaps.",
                   "source_count": len(sources), "available_source_count": sum(bool(s.text) for s in sources.values()),
                   "processed_source_count": len(processed), "failed_batch_count": len({f["batch"] for f in failures}),
                   "candidate_count": len(candidates), "accepted_candidate_count": len(accepted),
                   "repaired_candidate_count": sum(bool(c.get("repair_of")) for c in accepted),
                   "model_failure_count": sum(m.get("status") in {"failed", "blocked"} for m in calls),
                   "review_item_count": len(queue), "category_counts": dict(Counter(r["category"] for r in rules)),
                   "status_counts": dict(Counter(r["status"] for r in rules)),
                   "scope_complete": not selected and not failures and len(processed) == len(sources),
                   "quality_note": "Schema and quote validation do not certify legal completeness or correctness.",
                   "model_calls": calls})
    if capture_diagnostics:
        report["capture_log_diagnostics"] = capture_diagnostics
    write_json(output_dir / "rules.json", payload)
    write_json(output_dir / "candidates.json", {"candidates": candidates})
    write_json(output_dir / "review_queue.json", {"items": queue})
    coverage_payload = {"sources": coverage}
    if capture_diagnostics:
        coverage_payload["diagnostics"] = capture_diagnostics
    write_json(output_dir / "source_coverage.json", coverage_payload)
    write_json(output_dir / "validation_report.json", report)
    with (output_dir / "extraction_audit.jsonl").open("w", encoding="utf-8") as f:
        for entry in audit:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return report
