"""Exercise extraction gates using synthetic English sources and a fake model."""

import copy
import csv
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from rent_rules.pipeline import build_batches, process_batch, run
from rent_rules.sources import load_sources


AS_OF = "2026-10-01"
RETRIEVED_AT = "2026-10-01T22:35Z"
RECEIPT = "Providers shall give the applicant an itemized receipt for each screening payment."
REFUND = "Unused application payments shall be returned to the applicant."
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "team_rule_id", "jurisdiction", "level", "category", "status", "title",
        "requirement", "citation", "source_url", "quoted_span",
    ],
    "properties": {
        "team_rule_id": {"type": "string"},
        "jurisdiction": {"type": "string"},
        "level": {"enum": ["state", "city"]},
        "category": {"enum": ["application_screening_fees"]},
        "status": {"enum": ["in_force", "not_yet_effective", "pending", "failed"]},
        "title": {"type": "string"},
        "requirement": {"type": "string"},
        "citation": {"type": "string"},
        "source_doc_id": {"type": ["string", "null"]},
        "source_url": {"type": "string"},
        "quoted_span": {"type": "string", "minLength": 20},
        "effective_date": {
            "type": ["string", "null"],
            "pattern": r"^\d{4}(-\d{2}(-\d{2})?)?$",
        },
        "overrides": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "conflict_flag": {"type": "boolean"},
    },
}


def synthetic_rule(quote=RECEIPT, **updates):
    """Create a schema-shaped test claim, never an actual jurisdiction answer."""
    rule = {
        "jurisdiction": "CA",
        "level": "state",
        "category": "application_screening_fees",
        "status": "in_force",
        "title": "Synthetic receipt requirement",
        "requirement": quote,
        "key_value": None,
        "coverage_conditions": "Only the fictional providers described in this test.",
        "exemptions": None,
        "conditions": [],
        "effective_date": None,
        "end_date": None,
        "temporal_notes": None,
        "citation": "Synthetic Test Provision 1",
        "quoted_span": quote,
        "evidence": [],
        "confidence": 0.8,
        "conflict_flag": False,
        "conflict_note": None,
        "interaction": None,
        "related_citations": [],
        "penalty": None,
        "limitations": [],
    }
    rule.update(updates)
    return rule


class FakeModel:
    """Return schema-valid test responses without any CLI or network access."""

    def __init__(self, rules_by_doc=None, extract_transform=None,
                 review_transform=None, fail_stage=None, consolidate_transform=None):
        self.rules_by_doc = rules_by_doc or {}
        self.extract_transform = extract_transform
        self.review_transform = review_transform
        self.consolidate_transform = consolidate_transform
        self.fail_stage = fail_stage
        self.calls = []

    def generate(self, prompt, schema, stage):
        self.calls.append(stage)
        if stage == self.fail_stage:
            raise RuntimeError("Synthetic {} failure".format(stage))
        if stage == "extract":
            chunks = json.loads(prompt.split("\nSOURCE_CHUNKS:\n", 1)[1])
            response = {"documents": [{
                "chunk_id": chunk["chunk_id"],
                "doc_id": chunk["doc_id"],
                "document_type": "official_guidance",
                "summary": "A synthetic document used only for automated testing.",
                "rules": copy.deepcopy(self.rules_by_doc.get(chunk["doc_id"], [])),
                "issues": [],
            } for chunk in chunks]}
            if self.extract_transform is not None:
                response = self.extract_transform(response)
        elif stage == "review":
            candidates = json.loads(prompt.split("\nCANDIDATES:\n", 1)[1])
            response = {"reviews": [{
                "candidate_id": candidate["candidate_id"],
                "decision": "accept",
                "reason": "Accepted by the synthetic test reviewer.",
            } for candidate in candidates]}
            if self.review_transform is not None:
                response = self.review_transform(response)
        elif stage == "consolidate":
            candidates = json.loads(prompt.split("\nINPUT_RECORDS_JSON:\n", 1)[1])
            response = {
                "groups": [{
                    "candidate_ids": [candidate["candidate_id"]],
                    "preferred_id": candidate["candidate_id"],
                    "reason": "Keep this synthetic claim separate.",
                } for candidate in candidates],
                "conflicts": [],
                "interactions": [],
            }
            if self.consolidate_transform is not None:
                response = self.consolidate_transform(response)
        elif stage == "repair":
            candidates = json.loads(prompt.split("\nREPAIR_CANDIDATES:\n", 1)[1])
            response = {"repairs": [{
                "candidate_id": candidate["candidate_id"],
                "decision": "unresolved",
                "reason": "No correction is supplied by this synthetic model.",
                "rule": None,
            } for candidate in candidates]}
        else:
            raise AssertionError("Unexpected model stage: {}".format(stage))
        # Missing/duplicate chunk responses are structurally valid. The pipeline
        # must enforce semantic response coverage independently of JSON Schema.
        Draft202012Validator(schema).validate(response)
        return response, {"stage": stage, "cached": False, "synthetic": True}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pack = self.root / "pack"
        self.output = self.root / "output"
        (self.pack / "corpus/text").mkdir(parents=True)
        (self.pack / "schema").mkdir()
        (self.pack / "schema/rule_record.schema.json").write_text(
            json.dumps(SCHEMA), encoding="utf-8"
        )

    def make_sources(self, bodies):
        rows = []
        for doc_id, body in bodies.items():
            url = "https://example.test/synthetic/" + doc_id
            text = "SOURCE: {}\nRETRIEVED: 2026-10-01 22:35 UTC\n\n{}".format(url, body)
            (self.pack / "corpus/text" / (doc_id + ".txt")).write_bytes(text.encode("utf-8"))
            rows.append({
                "doc_id": doc_id,
                "jurisdictions": "CA",
                "url": url,
                "source_type": "official",
                "capture": "yes",
                "retrieved_at": RETRIEVED_AT,
                "sha256": "",
                "text_file": "text/{}.txt".format(doc_id),
                "status": "ok",
            })
        fields = [
            "doc_id", "jurisdictions", "url", "source_type", "capture",
            "retrieved_at", "sha256", "text_file", "status",
        ]
        with (self.pack / "corpus/corpus_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return load_sources(self.pack)

    def read_output(self, filename):
        return json.loads((self.output / filename).read_text(encoding="utf-8"))

    def run_pipeline(self, model, **options):
        with redirect_stdout(io.StringIO()):
            report = run(self.pack, self.output, model, workers=1, as_of=AS_OF, **options)
        return report

    def assert_withheld(self, expected_problem):
        self.assertEqual(self.read_output("rules.json"), {"rules": []})
        candidates = self.read_output("candidates.json")["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertIn(expected_problem, {problem["code"] for problem in candidates[0]["problems"]})
        queued = [item for item in self.read_output("review_queue.json")["items"]
                  if item["type"] == "candidate_review"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["candidate_id"], candidates[0]["candidate_id"])
        self.assertIn(expected_problem, {problem["code"] for problem in queued[0]["problems"]})

    def test_effective_date_without_field_evidence_is_queued_not_exported(self):
        self.make_sources({"SYN001": RECEIPT})
        rule = synthetic_rule(effective_date="2026-01-01")
        model = FakeModel({"SYN001": [rule]})
        report = self.run_pipeline(model)
        self.assert_withheld("date_evidence_missing")
        self.assertEqual(report["accepted_candidate_count"], 0)
        self.assertEqual(model.calls, ["extract", "repair"])

    def test_end_date_evidence_does_not_establish_the_effective_date(self):
        sunset = "This synthetic provision expires after December 31, 2027."
        self.make_sources({"SYN001": RECEIPT + "\n" + sunset})
        rule = synthetic_rule(
            effective_date="2026-01-01",
            end_date="2027-12-31",
            evidence=[{"field": "end_date", "quote": sunset}],
        )
        model = FakeModel({"SYN001": [rule]})
        self.run_pipeline(model)
        self.assert_withheld("date_evidence_missing")
        self.assertEqual(model.calls, ["extract", "repair"])

    def test_unverified_status_never_reaches_the_export_or_accepting_reviewer(self):
        self.make_sources({"SYN001": RECEIPT})
        model = FakeModel({"SYN001": [synthetic_rule(status="unverified")]})
        self.run_pipeline(model)
        self.assert_withheld("unverified_status")
        self.assertEqual(model.calls, ["extract"])

    def test_expired_end_date_is_withheld_even_if_extraction_calls_it_in_force(self):
        interval = "This synthetic provision is valid through September 30, 2026."
        self.make_sources({"SYN001": RECEIPT + "\n" + interval})
        rule = synthetic_rule(end_date="2026-09-30", evidence=[{"field": "end_date", "quote": interval}])
        model = FakeModel({"SYN001": [rule]})
        self.run_pipeline(model)
        self.assert_withheld("historical_rule")
        self.assertEqual(model.calls, ["extract"])

    def test_inclusive_end_date_is_still_eligible_on_its_last_day(self):
        interval = "This synthetic provision remains valid through October 1, 2026."
        self.make_sources({"SYN001": RECEIPT + "\n" + interval})
        rule = synthetic_rule(end_date=AS_OF, evidence=[{"field": "end_date", "quote": interval}])
        model = FakeModel({"SYN001": [rule]})
        report = self.run_pipeline(model)
        self.assertEqual(len(self.read_output("rules.json")["rules"]), 1)
        self.assertEqual(report["accepted_candidate_count"], 1)
        self.assertEqual(model.calls, ["extract", "review"])

    def test_missing_and_duplicate_chunks_fail_instead_of_silently_disappearing(self):
        body = "Harmless context for a synthetic source coverage test.\n" * 65
        sources = self.make_sources({"SYN001": body})
        batch = build_batches(sources, max_chars=1600, batch_chars=100000)[0]
        self.assertGreater(len(batch), 1)

        def missing(response):
            response["documents"].pop()
            return response

        def duplicate(response):
            response["documents"][-1] = copy.deepcopy(response["documents"][0])
            return response

        for transform in (missing, duplicate):
            with self.subTest(transform=transform.__name__):
                model = FakeModel(extract_transform=transform)
                with self.assertRaisesRegex(ValueError, "every requested chunk exactly once"):
                    process_batch(batch, AS_OF, SCHEMA, sources, model)
                self.assertEqual(model.calls, ["extract"])
                report = self.run_pipeline(model, max_chars=1600, batch_chars=100000)
                self.assertEqual(report["failed_batch_count"], 1)
                self.assertFalse(report["scope_complete"])
                self.assertEqual(self.read_output("source_coverage.json")["sources"][0]["status"], "failed")

    def test_review_must_cover_every_candidate_once(self):
        sources = self.make_sources({"SYN001": RECEIPT + "\n" + REFUND})
        batch = build_batches(sources)[0]
        rules = [synthetic_rule(), synthetic_rule(REFUND, title="Synthetic refund requirement")]

        def missing(response):
            response["reviews"].pop()
            return response

        def duplicate(response):
            response["reviews"][-1] = copy.deepcopy(response["reviews"][0])
            return response

        for transform in (missing, duplicate):
            with self.subTest(transform=transform.__name__):
                model = FakeModel({"SYN001": rules}, review_transform=transform)
                with self.assertRaisesRegex(ValueError, "every candidate exactly once"):
                    process_batch(batch, AS_OF, SCHEMA, sources, model)
                self.assertEqual(model.calls, ["extract", "review"])

    def test_whitespace_normalization_restores_exact_source_before_valid_export(self):
        original = "Providers shall give\r\n\r\nthe applicant\tan itemized receipt for each screening payment."
        sources = self.make_sources({"SYN001": original})
        model = FakeModel({"SYN001": [synthetic_rule()]})
        report = self.run_pipeline(model)
        rules = self.read_output("rules.json")["rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["quoted_span"], original)
        Draft202012Validator(SCHEMA).validate(rules[0])
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(model.calls, ["extract", "review"])
        candidate = self.read_output("candidates.json")["candidates"][0]
        self.assertEqual(candidate["raw_extraction"]["quoted_span"], RECEIPT)
        self.assertEqual(candidate["rule"]["quoted_span"], original)
        evidence = rules[0]["evidence"][0]
        self.assertEqual(evidence["match_type"], "whitespace")
        self.assertEqual(sources["SYN001"].text[evidence["start"]:evidence["end"]], original)

    def test_crlf_retrieval_header_cannot_be_exported_as_legal_evidence(self):
        self.make_sources({"SYN001": RECEIPT})
        path = self.pack / "corpus/text/SYN001.txt"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        quote = "RETRIEVED: 2026-10-01 22:35 UTC"
        model = FakeModel({"SYN001": [synthetic_rule(quote)]})
        self.run_pipeline(model)
        self.assert_withheld("header_quote")
        self.assertEqual(model.calls, ["extract"])

    def test_distinct_monetary_values_survive_a_proposed_duplicate_merge(self):
        low_quote = "The application screening fee must not exceed $10."
        high_quote = "The application screening fee must not exceed $20."
        self.make_sources({"SYN001": low_quote, "SYN002": high_quote})
        shared_requirement = "The application screening fee must not exceed the stated cap."
        rules = {
            "SYN001": [synthetic_rule(low_quote, requirement=shared_requirement, key_value="$10")],
            "SYN002": [synthetic_rule(high_quote, requirement=shared_requirement, key_value="$20")],
        }

        def merge_everything(response):
            ids = [candidate_id for group in response["groups"] for candidate_id in group["candidate_ids"]]
            response["groups"] = [{
                "candidate_ids": ids,
                "preferred_id": ids[0],
                "reason": "An intentionally incorrect synthetic duplicate proposal.",
            }]
            return response

        model = FakeModel(rules, consolidate_transform=merge_everything)
        report = self.run_pipeline(model)
        exported = self.read_output("rules.json")["rules"]
        self.assertEqual(len(exported), 2)
        self.assertEqual({rule["key_value"] for rule in exported}, {"$10", "$20"})
        self.assertEqual(len({rule["team_rule_id"] for rule in exported}), 2)
        self.assertEqual({rule["source_doc_id"] for rule in exported}, {"SYN001", "SYN002"})
        self.assertEqual(model.calls, ["extract", "review", "consolidate"])
        self.assertTrue(report["valid"], report["errors"])
        fallback = [item for item in self.read_output("review_queue.json")["items"]
                    if item["type"] == "consolidation_fallback"]
        self.assertEqual(len(fallback), 1)
        self.assertIn("different explicit numbers", fallback[0]["reason"])
        self.assertEqual(report["model_failure_count"], 1)
        consolidation = [call for call in report["model_calls"] if call["stage"] == "consolidate"]
        self.assertEqual(consolidation[0]["status"], "failed")
        self.assertIn("different explicit numbers", consolidation[0]["validation_error"])

    def test_model_errors_mark_all_batch_sources_failed_with_a_visible_reason(self):
        self.make_sources({"SYN001": RECEIPT, "SYN002": REFUND})
        for stage in ("extract", "review"):
            with self.subTest(stage=stage):
                model = FakeModel({"SYN001": [synthetic_rule()], "SYN002": [synthetic_rule(REFUND)]}, fail_stage=stage)
                report = self.run_pipeline(model)
                coverage = self.read_output("source_coverage.json")["sources"]
                self.assertEqual({source["doc_id"] for source in coverage}, {"SYN001", "SYN002"})
                self.assertEqual({source["status"] for source in coverage}, {"failed"})
                self.assertEqual(report["failed_batch_count"], 1)
                self.assertEqual(report["processed_source_count"], 0)
                self.assertFalse(report["scope_complete"])
                self.assertEqual(self.read_output("rules.json"), {"rules": []})
                failures = [item for item in self.read_output("review_queue.json")["items"]
                            if item["type"] == "extraction_failure"]
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0]["doc_ids"], ["SYN001", "SYN002"])
                self.assertIn("Synthetic {} failure".format(stage), failures[0]["reason"])

    def test_filtered_empty_response_is_a_failed_source_without_a_retry(self):
        self.make_sources({"SYN001": RECEIPT, "SYN002": REFUND})

        def blocked(response):
            response["documents"][0].update(
                rules=[], summary="Extraction could not be completed because the response was blocked by a content filter.",
                issues=["An empty rules list does not mean this source contains no relevant rules."])
            response["documents"][1].update(
                rules=[], summary="A synthetic source inventory entry.",
                issues=["Extraction omitted because the response was blocked."])
            return response

        model = FakeModel(extract_transform=blocked)
        report = self.run_pipeline(model)
        self.assertEqual(model.calls, ["extract"])
        self.assertEqual(report["processed_source_count"], 0)
        self.assertEqual(report["failed_batch_count"], 1)
        self.assertFalse(report["scope_complete"])
        self.assertEqual({item["status"] for item in self.read_output("source_coverage.json")["sources"]}, {"failed"})
        failures = [item for item in self.read_output("review_queue.json")["items"]
                    if item["type"] == "extraction_blocked"]
        self.assertEqual(failures[0]["doc_ids"], ["SYN001", "SYN002"])
        self.assertEqual(report["model_failure_count"], 1)

    def test_filtered_partial_rules_are_withheld_while_other_sources_survive(self):
        self.make_sources({"SYN001": RECEIPT, "SYN002": REFUND})

        def blocked(response):
            response["documents"][0]["summary"] = "The response was content-filtered after partial extraction."
            return response

        model = FakeModel({"SYN001": [synthetic_rule()], "SYN002": [synthetic_rule(REFUND)]},
                          extract_transform=blocked)
        report = self.run_pipeline(model)
        self.assertEqual(model.calls, ["extract", "review"])
        exported = self.read_output("rules.json")["rules"]
        self.assertEqual([rule["source_doc_id"] for rule in exported], ["SYN002"])
        candidates = self.read_output("candidates.json")["candidates"]
        blocked_candidate = next(item for item in candidates if item["doc_id"] == "SYN001")
        self.assertIn("extraction_blocked", {item["code"] for item in blocked_candidate["problems"]})
        self.assertNotIn("review", blocked_candidate)
        self.assertEqual(report["processed_source_count"], 1)
        self.assertEqual(report["failed_batch_count"], 1)
        self.assertEqual(report["model_failure_count"], 1)
        coverage = {item["doc_id"]: item["status"] for item in self.read_output("source_coverage.json")["sources"]}
        self.assertEqual(coverage, {"SYN001": "failed", "SYN002": "processed"})

    def test_filtered_independent_review_is_withheld_without_repair_for_every_decision(self):
        self.make_sources({"SYN001": RECEIPT, "SYN002": REFUND})
        for decision in ("accept", "review", "reject"):
            with self.subTest(decision=decision):
                def blocked(response):
                    response["reviews"][0].update(
                        decision=decision, reason="The model response was blocked by a content filter.")
                    return response

                model = FakeModel({"SYN001": [synthetic_rule()], "SYN002": [synthetic_rule(REFUND)]},
                                  review_transform=blocked)
                report = self.run_pipeline(model)
                self.assertEqual(model.calls, ["extract", "review"])
                self.assertEqual(report["rule_count"], 1)
                self.assertEqual(report["model_failure_count"], 1)
                self.assertEqual(report["failed_batch_count"], 1)
                self.assertEqual(report["processed_source_count"], 1)
                self.assertFalse(report["scope_complete"])
                candidates = self.read_output("candidates.json")["candidates"]
                blocked_candidate = next(item for item in candidates if item["review"]["reason"].startswith("The model response"))
                self.assertIn("review_blocked", {item["code"] for item in blocked_candidate["problems"]})
                self.assertNotIn("resolved_by", blocked_candidate)
                self.assertFalse(any(item.get("repair_of") for item in candidates))
                exported = self.read_output("rules.json")["rules"]
                self.assertNotIn(blocked_candidate["doc_id"], [rule["source_doc_id"] for rule in exported])
                calls = report["model_calls"]
                self.assertEqual(next(item for item in calls if item["stage"] == "review")["status"], "blocked")
                coverage = {item["doc_id"]: item["status"] for item in self.read_output("source_coverage.json")["sources"]}
                self.assertEqual(coverage[blocked_candidate["doc_id"]], "failed")
                self.assertEqual(list(coverage.values()).count("processed"), 1)
                self.assertTrue(any(item["type"] == "review_blocked"
                                    for item in self.read_output("review_queue.json")["items"]))


if __name__ == "__main__":
    unittest.main()
