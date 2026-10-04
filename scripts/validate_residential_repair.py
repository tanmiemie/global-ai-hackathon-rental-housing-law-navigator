#!/usr/bin/env python3
"""Compare a residential-use binding repair with the preserved workflow output.

This validates artifacts and recorded observations, without evaluating a rule or
calling a model. The earlier single-fact experiment supplies the exact expected
decision delta; it is a regression reference, not independent legal authority.
"""

import argparse
from collections import Counter
import csv
import gc
import hashlib
import json
from pathlib import Path
import time


PUBLIC_PAIRS = (("lookups.json", "lookups_sha256"),
                ("rules.json", "exported_rules_sha256"),
                ("rule_id_map.json", "rule_id_map_sha256"))
MERGED_PAIRS = PUBLIC_PAIRS + (
    ("address_diagnostics.json", "address_diagnostics_sha256"),
    ("evaluation_audit_index.json", "evaluation_audit_index_sha256"),
    ("address_summary.csv", "address_summary_sha256"))
FIELD_CONFIG = {"year_built": ("year_built_validation_status", "year_built_explanation"),
                "units": ("unit_validation_status", "unit_validation_explanation")}
RESIDENTIAL_SUBTYPES = {"subsidized_multifamily", "mixed_use_multifamily",
                       "specialized_residential", "residential_tic"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def check_artifacts(directory, report, pairs):
    for filename, field in pairs:
        require(digest(directory / filename) == report[field],
                "Artifact hash mismatch: " + str(directory / filename))


def citation_check(entry, rule, address_id):
    label = address_id + "/" + entry["team_rule_id"]
    require(set(entry) == {"team_rule_id", "result", "explanation", "conflict_flag"},
            "Lookup entry schema changed: " + label)
    require(type(entry["conflict_flag"]) is bool, "Conflict flag is not Boolean: " + label)
    fields = ("citation", "source_doc_id", "source_url", "retrieved_at")
    require(all(isinstance(rule.get(key), str) and rule[key].strip() for key in fields),
            "Incomplete rule citation: " + label)
    citation = " Citation: {} [document {}; URL {}; retrieved {}].".format(
        *(rule[key] for key in fields))
    if entry["result"] == "unknown":
        require(entry["explanation"].startswith("Unknown because "),
                "Unknown lacks explicit cause: " + label)
        require("The branch descriptions below remain conditional on resolving this uncertainty."
                in entry["explanation"], "Unknown lacks conditional qualification: " + label)
        citation += " This citation identifies the candidate rule; it does not establish applicability to this address."
    require(entry["explanation"].endswith(citation), "Citation differs from paired rule: " + label)


def compare_observations(before, after, row, affected):
    aid = row["address_id"]
    require(before["address"] == after["address"] == row,
            "Bound address input differs from CSV: " + aid)
    require(set(before) == set(after), "Bound address envelope changed: " + aid)
    for key in before:
        if key != "facts":
            require(before[key] == after[key], "Non-fact binding metadata changed: " + aid + "/" + key)
    old, new = before["facts"], after["facts"]
    require(set(old) == set(new), "Bound fact inventory changed: " + aid)
    changed = [key for key in old if old[key] != new[key]]
    require(changed == (["property.residential_use"] if affected else []),
            "Unexpected bound fact changes: " + aid + "/" + repr(changed))
    if not affected:
        return
    previous, current = old["property.residential_use"], new["property.residential_use"]
    require(previous["status"] == "unknown" and current["status"] == "known"
            and current.get("value") is True, "Residential observation was not resolved true: " + aid)
    require(current["subject_scope"] == previous["subject_scope"] == "address_property",
            "Residential subject scope changed: " + aid)
    require(current["observed_at"] == previous["observed_at"], "Historical observation time changed: " + aid)
    require(current["evidence"] == previous["evidence"] == new["property.use"]["evidence"],
            "Residential source evidence changed: " + aid)
    require(new["property.use"]["value"] == row["resolved_property_use"]
            and new["property.use"]["status"] == "known"
            and row["property_use_validation_status"] == "verified"
            and row["resolved_property_use"] in RESIDENTIAL_SUBTYPES,
            "Resolved residential use lacks verified subtype support: " + aid)
    require(row["resolved_property_use"] in current["reason"],
            "Residential binding lacks its original subtype: " + aid)


def check_field_notes(record, entry, bound, row, dependencies):
    expected = {}
    for key in sorted(set(record.get("missing_facts", []))):
        dependency = dependencies.get(key)
        if dependency is None or record["team_rule_id"] not in dependency["rule_ids"]:
            continue
        group = dependency["input_group"]
        status_field = FIELD_CONFIG[group][0]
        if bound["facts"].get(key, {}).get("status") != "known" and row[status_field] != "verified":
            expected.setdefault(group, []).append(key)
    notes = record.get("address_field_diagnostics", [])
    require(len(notes) == len({note["input_group"] for note in notes}) == len(expected),
            "Missing/duplicate field explanation group: " + row["address_id"])
    require({note["input_group"] for note in notes} == set(expected),
            "Field notes exceed decisive missing inputs: " + row["address_id"])
    for note in notes:
        group = note["input_group"]
        status_field, explanation_field = FIELD_CONFIG[group]
        require(note["status_field"] == status_field and note["explanation_field"] == explanation_field,
                "Field note input mapping changed")
        require(note["status"] == row[status_field] and note["source_explanation"] == row[explanation_field],
                "Field note does not retain exact CSV evidence")
        require(note["explanation"] in entry["explanation"], "Field note absent from public explanation")
        if note["source_explanation"].strip():
            require(note["source_explanation"] in note["explanation"], "Field source explanation altered")
        require([item["key"] for item in note["affected_facts"]] == expected[group],
                "Field note lists unrelated/missing inputs")
        for item in note["affected_facts"]:
            dependency = dependencies[item["key"]]
            require(all(item[key] == dependency[key] for key in ("description", "subject_scope")),
                    "Field note changed fact definition/scope")
    return Counter(note["input_group"] for note in notes)


def validate(output, baseline, experiment_path):
    started = time.perf_counter()
    report, prior_report = read(output / "run_report.json"), read(baseline / "run_report.json")
    identity = read(output / "run_identity.json")
    experiment = read(experiment_path)
    cases = {item["address_id"]: item for item in experiment["addresses"]}
    require(len(cases) == experiment["address_count"], "Diagnostic address inventory mismatch")
    require(report["input_sha256"] == prior_report["input_sha256"], "Repair changed original inputs")
    for path, expected in report["input_sha256"].items():
        require(digest(path) == expected == experiment["input_code_and_production_sha256_before"][path],
                "Input differs from diagnostic/baseline snapshot: " + path)
    for path, expected in experiment["input_code_and_production_sha256_before"].items():
        if baseline.resolve() in Path(path).resolve().parents:
            require(digest(path) == expected, "Preserved baseline artifact changed: " + path)
    for name, expected in report["evaluator_code_sha256"].items():
        require(digest(Path("src/rent_rules") / name) == expected, "Evaluator code hash mismatch: " + name)
    require(digest("scripts/evaluate_all_addresses.py") == identity["runner_sha256"], "Runner hash mismatch")
    for key in ("as_of", "mode", "input_sha256", "evaluator_code_sha256"):
        require(report[key] == identity[key], "Run identity mismatch: " + key)
    require(report["as_of"] == prior_report["as_of"] == experiment["as_of"] == "2026-10-01",
            "Legal query date changed")
    require(report["mode"] == prior_report["mode"] == experiment["mode"] == "coverage", "Evaluation mode changed")
    require(report["runtime_model_calls"] == 0, "Unexpected runtime model calls")
    check_artifacts(output, report, MERGED_PAIRS)
    check_artifacts(baseline, prior_report, MERGED_PAIRS)
    for filename in ("rules.json", "rule_id_map.json", "address_diagnostics.json"):
        require(digest(output / filename) == digest(baseline / filename),
                "Paired rules/map or address diagnostics changed: " + filename)
    with Path("outputs/address_facts/address_enrichment.csv").open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    ids = [row["address_id"] for row in rows]
    by_id = {row["address_id"]: row for row in rows}
    require(len(ids) == len(set(ids)) == 500, "Expected 500 unique addresses")
    affected = {row["address_id"] for row in rows if row["jurisdiction_status"] == "verified"
                and row["property_use_validation_status"] == "verified"
                and row["resolved_property_use"] in RESIDENTIAL_SUBTYPES}
    require(affected == set(cases), "Verified residential subtype cohort differs from prior diagnostic")
    payload, old_payload = read(output / "lookups.json"), read(baseline / "lookups.json")
    diagnostics_payload = read(output / "address_diagnostics.json")
    require(payload["as_of"] == old_payload["as_of"] == diagnostics_payload["as_of"] == report["as_of"],
            "Public query dates differ")
    lookups, previous = payload["lookups"], old_payload["lookups"]
    diagnostics = diagnostics_payload["addresses"]
    require(list(lookups) == list(previous) == list(diagnostics) == list(report["addresses"])
            == identity["address_ids"] == ids, "Address order/coverage mismatch")
    rules = read(output / "rules.json")["rules"]
    rule_by_id = {rule["team_rule_id"]: rule for rule in rules}
    require(len(rules) == len(rule_by_id) == 466, "Expected 466 uniquely identified rules")
    registry = read(output / "rule_id_map.json")["internal_to_submission"]
    internal_ids = [rule["team_rule_id"] for rule in read("outputs/module_a/rules.json")["rules"]]
    manifest = read("data/evaluation/address_field_dependencies.json")
    require(manifest["rules_sha256"] == digest("outputs/module_a/rules.json")
            and manifest["plans_sha256"] == digest("data/evaluation/rule_plans.json"),
            "Dependency manifest source identity mismatch")
    totals, before_totals, statuses = Counter(), Counter(), Counter()
    comparisons, changed_rules, skipped = [], {}, []
    for aid in ids:
        row, entries, old_entries, detail = by_id[aid], lookups[aid], previous[aid], diagnostics[aid]
        eligible = row["jurisdiction_status"] == "verified"
        state = "evaluated" if eligible else "skipped"
        statuses[state] += 1
        require(detail["status"] == state and detail["jurisdiction_status"] == row["jurisdiction_status"],
                "Address gate mismatch: " + aid)
        require(detail["jurisdiction_evidence_ref"] == row["jurisdiction_evidence_ref"],
                "Jurisdiction evidence changed: " + aid)
        require(detail["rules_evaluated"] == (len(rules) if eligible else 0), "Rule evaluation count mismatch: " + aid)
        require([entry["team_rule_id"] for entry in entries] == [entry["team_rule_id"] for entry in old_entries],
                "Returned rule membership/order changed: " + aid)
        require(len({entry["team_rule_id"] for entry in entries}) == len(entries), "Duplicate returned rule: " + aid)
        if not eligible:
            skipped.append(aid)
            require(entries == [] and detail["reason"] == row["jurisdiction_evidence_ref"]
                    and detail["reason_code"] == "jurisdiction_not_verified", "Skipped address result/reason changed: " + aid)
        if aid not in affected:
            require(entries == old_entries, "Unrelated address public entry changed: " + aid)
        changed = []
        for entry, prior in zip(entries, old_entries):
            citation_check(entry, rule_by_id[entry["team_rule_id"]], aid)
            require(entry["conflict_flag"] == prior["conflict_flag"], "Conflict flag changed: " + aid)
            if entry["result"] != prior["result"]:
                require(prior["result"] == "unknown" and entry["result"] == "applies", "Unexpected decision transition: " + aid)
                changed.append(entry["team_rule_id"])
        if aid in affected:
            expected = cases[aid]["unknown_to_hypothetical_applies_rule_ids"]
            require(changed == expected, "Decision delta differs from single-fact diagnostic: " + aid)
            require(len(changed) == cases[aid]["unknown_to_hypothetical_applies_count"], "Diagnostic delta count mismatch: " + aid)
            changed_rules[aid] = changed
        else:
            require(not changed, "Unrelated address decision changed: " + aid)
        counts, prior_counts = Counter(entry["result"] for entry in entries), Counter(entry["result"] for entry in old_entries)
        totals.update(counts)
        before_totals.update(prior_counts)
        summary = report["addresses"][aid]
        require(summary["results"] == dict(counts) and summary["returned_count"] == len(entries), "Address report totals differ: " + aid)
        require(summary["evaluation_status"] == state and summary["evaluated_rule_count"] == detail["rules_evaluated"],
                "Address summary gate mismatch: " + aid)
        comparisons.append({"address_id": aid, "street_address": row["street_address"],
                            "legal_city": row["legal_city"], "legal_state": row["legal_state"],
                            "property_use": row["resolved_property_use"], "evaluation_status": state,
                            "residential_binding_repaired": aid in affected,
                            "before_returned_count": len(old_entries), "after_returned_count": len(entries),
                            "before_applies": prior_counts["applies"], "after_applies": counts["applies"],
                            "before_unknown": prior_counts["unknown"], "after_unknown": counts["unknown"],
                            "unknown_to_applies": len(changed),
                            "conflict_flag_count": sum(entry["conflict_flag"] for entry in entries)})
    require(statuses == {"evaluated": 477, "skipped": 23}, "Address gate population changed")
    require(report["address_count"] == len(ids) and report["rule_count"] == len(rules)
            and report["evaluated_address_count"] == statuses["evaluated"]
            and report["skipped_address_count"] == statuses["skipped"], "Merged report population mismatch")
    require(dict(totals) == report["results"] and sum(totals.values()) == report["returned_count"], "Merged totals mismatch")
    require(dict(before_totals) == prior_report["results"], "Baseline totals mismatch")
    transition_count = sum(map(len, changed_rules.values()))
    require(transition_count == experiment["hypothetical_totals"]["applies"] - experiment["baseline_totals"]["applies"],
            "Total decision delta differs from isolated experiment")
    with (output / "address_summary.csv").open(newline="", encoding="utf-8") as handle:
        summary_rows = list(csv.DictReader(handle))
    require([row["address_id"] for row in summary_rows] == ids, "Summary CSV address coverage mismatch")
    for row in summary_rows:
        aid, summary = row["address_id"], report["addresses"][row["address_id"]]
        for key in ("street_address", "legal_city", "legal_state", "jurisdiction_status"):
            require(row[key] == by_id[aid][key], "Summary CSV address field mismatch: " + aid)
        for key in ("applies", "unknown", "pending", "not_yet_effective", "superseded"):
            require(int(row[key]) == summary["results"].get(key, 0), "Summary CSV decision count mismatch: " + aid)
        require(int(row["returned_count"]) == len(lookups[aid])
                and int(row["conflict_flag_count"]) == sum(entry["conflict_flag"] for entry in lookups[aid]),
                "Summary CSV returned/conflict count mismatch: " + aid)
        require(row["evaluation_status"] == diagnostics[aid]["status"]
                and int(row["evaluated_rule_count"]) == diagnostics[aid]["rules_evaluated"], "Summary CSV gate mismatch: " + aid)
        require(row["skip_reason"] == (diagnostics[aid]["reason"] if diagnostics[aid]["status"] == "skipped" else ""),
                "Summary CSV skip reason mismatch: " + aid)
    del old_payload, previous
    gc.collect()
    index, old_index = read(output / "evaluation_audit_index.json"), read(baseline / "evaluation_audit_index.json")
    require(index["batches"] == report["batches"] and old_index["batches"] == prior_report["batches"], "Audit index/report mismatch")
    require([aid for batch in index["batches"] for aid in batch["address_ids"]] == ids, "Audit address coverage mismatch")
    require([batch["address_ids"] for batch in index["batches"]] == [batch["address_ids"] for batch in old_index["batches"]],
            "Baseline and repair use different audit batches")
    note_counts, audits_checked, scope_notes_checked = Counter(), 0, 0
    for number, (batch, old_batch) in enumerate(zip(index["batches"], old_index["batches"]), 1):
        require(digest(old_batch["audit"]) == old_batch["evaluation_audit_sha256"], "Baseline audit hash mismatch")
        old_audit = read(old_batch["audit"])
        old_facts = old_audit["facts"]
        old_records = {aid: [{"team_rule_id": record["team_rule_id"], "result": record["result"],
                             "conflict_flag": record["conflict_flag"], "sha256": fingerprint(record)}
                            for record in records] for aid, records in old_audit["decisions"].items()}
        old_metadata = {key: fingerprint(value) for key, value in old_audit.items()
                        if key not in ("facts", "decisions")}
        del old_audit
        gc.collect()
        directory = Path(batch["directory"])
        batch_report = read(directory / "run_report.json")
        for key in ("as_of", "mode", "input_sha256", "evaluator_code_sha256"):
            require(batch_report[key] == report[key], "Batch identity mismatch: " + str(directory))
        check_artifacts(directory, batch_report, PUBLIC_PAIRS + (
            ("address_diagnostics.json", "address_diagnostics_sha256"),
            ("evaluation_audit.json", "evaluation_audit_sha256")))
        for key in ("exported_rules_sha256", "rule_id_map_sha256"):
            require(batch_report[key] == report[key], "Batch paired rule/map mismatch")
        for key in ("evaluation_audit_sha256", "address_diagnostics_sha256", "lookups_sha256"):
            require(batch_report[key] == batch[key], "Batch artifact/index mismatch: " + key)
        require(read(directory / "lookups.json") == {"as_of": report["as_of"], "lookups": {aid: lookups[aid] for aid in batch["address_ids"]}},
                "Merged and batch public entries differ")
        require(read(directory / "address_diagnostics.json") == {"as_of": report["as_of"], "addresses": {aid: diagnostics[aid] for aid in batch["address_ids"]}},
                "Merged and batch diagnostics differ")
        require(batch_report["addresses"] == {aid: report["addresses"][aid] for aid in batch["address_ids"]}, "Merged and batch address reports differ")
        audit = read(batch["audit"])
        require({key: fingerprint(value) for key, value in audit.items() if key not in ("facts", "decisions")}
                == old_metadata, "Source evidence, plan metadata, or address diagnostics changed in audit")
        require(list(audit["decisions"]) == list(audit["facts"]) == batch["address_ids"], "Audit inventory mismatch")
        for aid in batch["address_ids"]:
            row, bound, records = by_id[aid], audit["facts"][aid], audit["decisions"][aid]
            compare_observations(old_facts[aid], bound, row, aid in affected)
            if diagnostics[aid]["status"] == "skipped":
                require(records == [] and bound["facts"] == {}, "Skipped address has facts/decisions: " + aid)
                continue
            require([record["team_rule_id"] for record in records] == internal_ids, "Audit rule inventory/order mismatch: " + aid)
            require(len(records) == len(old_records[aid]), "Audit record count changed: " + aid)
            returned = [record for record in records if record["result"] is not None]
            entries = {entry["team_rule_id"]: entry for entry in lookups[aid]}
            require([registry[record["team_rule_id"]] for record in returned] == list(entries), "Audit/public rule membership differs: " + aid)
            for record, prior in zip(records, old_records[aid]):
                public_id = registry[record["team_rule_id"]]
                require(record["team_rule_id"] == prior["team_rule_id"] and record["conflict_flag"] == prior["conflict_flag"], "Audit identity/conflict changed: " + aid)
                expected_result = "applies" if public_id in changed_rules.get(aid, []) else prior["result"]
                require(record["result"] == expected_result, "Unexpected audit decision transition: " + aid + "/" + public_id)
                if aid not in affected:
                    require(fingerprint(record) == prior["sha256"], "Unrelated audit decision changed: " + aid)
                if record["result"] is None:
                    continue
                entry = entries[public_id]
                require(record["result"] == entry["result"] and record["conflict_flag"] == entry["conflict_flag"], "Audit/public decision differs: " + aid)
                require("Coverage does not establish compliance, violation, or an amount currently owed."
                        in entry["explanation"], "Coverage limitation absent from public explanation: " + aid)
                for branch in record.get("branches", []):
                    if branch["coverage"]["value"] != "true":
                        continue
                    if branch["event"]["value"] == "unknown":
                        require("This is a governing conditional protection; the event and event-specific exclusions are not established."
                                in entry["explanation"], "Unknown event qualification absent: " + aid)
                    elif branch["event"]["value"] == "false":
                        require("The governing conditional protection is reported, but the supplied event is not triggered or is excluded from this branch."
                                in entry["explanation"], "False event qualification absent: " + aid)
                note_counts.update(check_field_notes(record, entry, bound, row, manifest["dependencies"]))
                if aid in affected:
                    # The binding adds context without deciding tenancy, subsidy,
                    # ownership exemptions or the commercial part of a mixed use.
                    note = bound["facts"]["property.residential_use"].get("scope_note")
                    require(isinstance(note, str) and bool(note.strip()), "Residential repair lacks scope note: " + aid)
                    require(note in entry["explanation"], "Residential scope note absent from public explanation: " + aid)
                    scope_notes_checked += 1
            audits_checked += len(records)
        del audit, old_facts, old_records
        gc.collect()
        print(json.dumps({"validated_audit_batches": number, "total_audit_batches": len(index["batches"])}), flush=True)
    comparison_path = output / "residential_repair_comparison.csv"
    with comparison_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
        writer.writeheader()
        writer.writerows(comparisons)
    return {
        "status": "passed", "as_of": report["as_of"], "output": str(output), "baseline": str(baseline),
        "regression_reference": str(experiment_path), "regression_reference_sha256": digest(experiment_path),
        "regression_reference_scope": "The earlier single-fact experiment predicts the expected code delta, not legal correctness.",
        "address_count": len(ids), "evaluated_address_count": statuses["evaluated"], "skipped_address_count": statuses["skipped"],
        "skipped_address_ids": skipped, "repaired_address_count": len(affected), "repaired_address_ids": sorted(affected),
        "unaffected_evaluated_entries_and_audits_unchanged": statuses["evaluated"] - len(affected),
        "only_changed_observation": "property.residential_use", "residential_evidence_and_exact_subtypes_preserved": True,
        "before_results": dict(before_totals), "results": dict(totals), "returned_count": sum(totals.values()),
        "unknown_to_applies_count": transition_count, "changed_rule_ids_by_address": changed_rules,
        "conflict_flags_unchanged": True, "returned_rule_membership_unchanged": True,
        "exact_citations_checked": sum(totals.values()), "unknown_cause_checks": totals["unknown"],
        "residential_scope_notes_checked": scope_notes_checked, "field_note_counts": dict(note_counts),
        "audited_rule_decisions": audits_checked, "audit_batches_checked": len(index["batches"]),
        "original_inputs_and_paired_rules_unchanged": True, "exact_source_evidence_unchanged": True,
        "summary_csv_verified": True, "comparison_csv": str(comparison_path), "comparison_csv_sha256": digest(comparison_path),
        "validator_sha256": digest(Path(__file__)), "elapsed_seconds": time.perf_counter() - started,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=Path("outputs/evaluator_all500_workflow_v2"))
    parser.add_argument("--experiment", type=Path, default=Path("outputs/zero_applies_review_v1/counterfactual.json"))
    args = parser.parse_args()
    destination = args.output / "repair_validation.json"
    try:
        result = validate(args.output, args.baseline, args.experiment)
    except Exception as exc:
        destination.write_text(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}, indent=2) + "\n")
        raise
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "changed_rule_ids_by_address"}, indent=2))


if __name__ == "__main__":
    main()
