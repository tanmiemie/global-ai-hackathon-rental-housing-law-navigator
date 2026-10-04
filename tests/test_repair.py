"""Bounded repair tests using fictional English source text and fake models."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest

from rent_rules.pipeline import build_batches, prepare_candidate
from rent_rules.repair import repair_candidates
from rent_rules.sources import SourceDocument


AS_OF = "2026-10-01"
OBLIGATION = "A fictional provider shall issue a receipt for each application payment."
EXCEPTION = "A receipt is not required when the applicant pays no application charge."
BODY = OBLIGATION + "\n" + EXCEPTION
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["team_rule_id", "jurisdiction", "level", "category", "status",
                 "requirement", "citation", "source_url", "quoted_span"],
    "properties": {
        "status": {"enum": ["in_force", "not_yet_effective", "pending", "failed"]},
        "quoted_span": {"type": "string", "minLength": 20},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


def raw_rule(**updates):
    rule = {
        "jurisdiction": "CA", "level": "state", "category": "application_screening_fees",
        "status": "in_force", "title": "Fictional receipt provision", "requirement": OBLIGATION,
        "key_value": None, "coverage_conditions": "Fictional application payments.",
        "exemptions": None, "conditions": [], "effective_date": None, "end_date": None,
        "temporal_notes": None, "citation": "Synthetic Provision 1", "quoted_span": OBLIGATION,
        "evidence": [], "confidence": 0.8, "conflict_flag": False, "conflict_note": None,
        "interaction": None, "related_citations": [], "penalty": None, "limitations": [],
    }
    rule.update(updates)
    return rule


def corrected_rule():
    return raw_rule(
        requirement=OBLIGATION + " " + EXCEPTION,
        exemptions=EXCEPTION,
        evidence=[{"field": "exemptions", "quote": EXCEPTION}],
    )


class RepairModel:
    """Return test responses; the production code must validate their structure."""

    def __init__(self, revised=None, unresolved=False, repair_transform=None,
                 review_transform=None, fail_stage=None):
        self.revised = corrected_rule() if revised is None else revised
        self.unresolved = unresolved
        self.repair_transform = repair_transform
        self.review_transform = review_transform
        self.fail_stage = fail_stage
        self.calls = []
        self.prompts = []

    def generate(self, prompt, schema, stage):
        self.calls.append(stage)
        self.prompts.append(prompt)
        if stage == self.fail_stage:
            raise RuntimeError("Synthetic {} failure".format(stage))
        if stage == "repair":
            selected = json.loads(prompt.split("\nREPAIR_CANDIDATES:\n", 1)[1])
            response = {"repairs": [{
                "candidate_id": candidate["candidate_id"],
                "decision": "unresolved" if self.unresolved else "revise",
                "reason": "No supported correction." if self.unresolved else "Preserve the explicit exception.",
                "rule": None if self.unresolved else deepcopy(self.revised),
            } for candidate in selected]}
            if self.repair_transform:
                response = self.repair_transform(response)
        elif stage == "review":
            selected = json.loads(prompt.split("\nCANDIDATES:\n", 1)[1])
            response = {"reviews": [{
                "candidate_id": candidate["candidate_id"], "decision": "accept",
                "reason": "The fictional source supports the corrected exception.",
            } for candidate in selected]}
            if self.review_transform:
                response = self.review_transform(response)
        else:
            raise AssertionError("Unexpected stage: " + stage)
        return response, {"stage": stage, "synthetic": True, "cached": False}


class RepairTests(unittest.TestCase):
    def setUp(self):
        url = "https://example.test/synthetic-repair"
        text = "SOURCE: {}\nRETRIEVED: 2026-10-01 22:35 UTC\n\n{}".format(url, BODY)
        self.source = SourceDocument(
            "SYN001", "CA", url, "official", "2026-10-01T22:35Z", text, None,
            hashlib.sha256(text.encode("utf-8")).hexdigest(), "local",
        )
        self.sources = {self.source.doc_id: self.source}
        self.batch = build_batches(self.sources)[0]

    def candidate(self, raw=None, decision="review", index=0, reason=None):
        result = prepare_candidate(
            raw or raw_rule(), self.source, self.batch[0]["chunk_id"], index,
            AS_OF, SCHEMA, self.sources,
        )
        result["document_type"] = "official_guidance"
        if decision is not None:
            result["review"] = {
                "candidate_id": result["candidate_id"], "decision": decision,
                "reason": reason or "The no-charge exception has been omitted.",
            }
        return result

    def repair(self, candidates, model):
        return repair_candidates(self.batch, candidates, model, self.sources, AS_OF, SCHEMA)

    def test_omitted_condition_is_repaired_then_independently_accepted(self):
        original = self.candidate()
        already_accepted = self.candidate(raw_rule(title="Other fictional provision"), "accept", 1)
        inputs = [original, already_accepted]
        before = deepcopy(inputs)
        model = RepairModel()
        result, calls, issues = self.repair(inputs, model)
        self.assertEqual(inputs, before)
        self.assertEqual(result[1], already_accepted)
        self.assertEqual(model.calls, ["repair", "review"])
        self.assertEqual([call["stage"] for call in calls], model.calls)
        self.assertEqual(issues, [])
        self.assertEqual(len(result), 3)
        revised = result[2]
        self.assertEqual(revised["repair_of"], original["candidate_id"])
        self.assertEqual(result[0]["resolved_by"], revised["candidate_id"])
        self.assertEqual(revised["document_type"], original["document_type"])
        self.assertEqual(revised["chunk_id"], original["chunk_id"])
        self.assertEqual(revised["rule"]["exemptions"], EXCEPTION)
        self.assertEqual(revised["problems"], [])
        self.assertEqual(revised["review"]["decision"], "accept")
        self.assertTrue(any(item["field"] == "exemptions" for item in revised["evidence"]))
        self.assertIn(EXCEPTION, model.prompts[0])
        self.assertIn(EXCEPTION, model.prompts[1])
        repeated, _, _ = self.repair(inputs, RepairModel())
        self.assertEqual(repeated[2]["candidate_id"], revised["candidate_id"])

    def test_invented_quote_is_withheld_before_independent_review(self):
        invented = "Fictional providers shall give each applicant an invented bonus payment."
        original = self.candidate()
        model = RepairModel(revised=raw_rule(quoted_span=invented, requirement=invented))
        result, _, issues = self.repair([original], model)
        self.assertEqual(model.calls, ["repair"])
        self.assertEqual(result[0], original)
        self.assertEqual(len(result), 2)
        self.assertIn("unsupported_quote", {item["code"] for item in result[1]["problems"]})
        self.assertNotIn("review", result[1])
        self.assertNotIn("resolved_by", result[0])
        self.assertEqual(issues[0]["type"], "repair_validation_failed")

    def test_unresolved_preserves_all_originals_and_is_never_accepted(self):
        originals = [self.candidate(), self.candidate(index=1, decision="accept")]
        model = RepairModel(unresolved=True)
        result, _, issues = self.repair(originals, model)
        self.assertEqual(result, originals)
        self.assertIsNot(result, originals)
        self.assertEqual(model.calls, ["repair"])
        self.assertEqual(issues[0]["type"], "repair_unresolved")
        self.assertNotIn("resolved_by", result[0])

    def test_excluded_candidates_are_not_sent_to_the_model(self):
        cases = [
            self.candidate(raw_rule(status="unverified")),
            self.candidate(raw_rule(end_date="2026-09-30")),
            self.candidate(raw_rule(jurisdiction="XX")),
            self.candidate(decision="accept"),
            self.candidate(decision="reject"),
        ]
        self.assertIn("historical_rule", {p["code"] for p in cases[1]["problems"]})
        self.assertIn("out_of_scope_jurisdiction", {p["code"] for p in cases[2]["problems"]})
        for candidate in cases:
            with self.subTest(status=candidate["rule"]["status"], review=candidate.get("review")):
                model = RepairModel()
                result, calls, _ = self.repair([candidate], model)
                self.assertEqual(result, [candidate])
                self.assertEqual(model.calls, [])
                self.assertEqual(calls, [])

    def test_only_the_named_program_problems_are_repairable_without_review(self):
        raws = [
            raw_rule(effective_date="2026-01-01"),
            raw_rule(evidence=[{"field": "exemptions", "quote": "An unsupported extra quote."}]),
        ]
        for raw in raws:
            with self.subTest(raw=raw):
                original = self.candidate(raw, decision=None)
                self.assertTrue(original["problems"])
                self.assertLessEqual({p["code"] for p in original["problems"]},
                                     {"date_evidence_missing", "unsupported_quote"})
                result, _, issues = self.repair([original], RepairModel())
                self.assertEqual(issues, [])
                self.assertIn("resolved_by", result[0])
        ineligible = self.candidate(raw_rule(effective_date="impossible-date"), decision=None)
        model = RepairModel()
        result, _, _ = self.repair([ineligible], model)
        self.assertEqual(model.calls, [])
        self.assertEqual(result, [ineligible])

    def test_missing_citation_can_be_repaired_with_a_supported_official_title_or_remain_unresolved(self):
        title = "Fictional Application Receipt Notice"
        text = self.source.text + "\n" + title + "\n"
        self.source = replace(self.source, text=text, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.sources = {self.source.doc_id: self.source}
        self.batch = build_batches(self.sources)[0]
        original = self.candidate(raw_rule(citation="No legal citation supplied for the fictional rule."), decision=None)
        self.assertEqual({p["code"] for p in original["problems"]}, {"missing_citation"})
        revised = corrected_rule()
        revised["citation"] = title
        revised["evidence"].append({"field": "citation", "quote": title})
        model = RepairModel(revised=revised)
        result, _, issues = self.repair([original], model)
        self.assertEqual(model.calls, ["repair", "review"])
        self.assertEqual(issues, [])
        self.assertEqual(result[1]["rule"]["citation"], title)
        self.assertEqual(result[0]["resolved_by"], result[1]["candidate_id"])
        result, _, issues = self.repair([original], RepairModel(unresolved=True))
        self.assertEqual(result, [original])
        self.assertEqual(issues[0]["type"], "repair_unresolved")

    def test_explicit_filter_or_refusal_is_not_retried_or_reframed(self):
        for reason in ["The model response was content-filtered.",
                       "I cannot assist with this request.", "Refusal: service policy."]:
            with self.subTest(reason=reason):
                original = self.candidate(reason=reason)
                model = RepairModel()
                result, calls, issues = self.repair([original], model)
                self.assertEqual(result, [original])
                self.assertEqual(model.calls, [])
                self.assertEqual(calls, [])
                self.assertEqual(issues[0]["type"], "repair_blocked")

    def test_legal_refusal_wording_is_not_treated_as_a_service_refusal(self):
        original = self.candidate(reason="The exception for refusal to renew is missing.")
        model = RepairModel(unresolved=True)
        self.repair([original], model)
        self.assertEqual(model.calls, ["repair"])

    def test_malformed_repairs_preserve_previously_accepted_candidates(self):
        def wrong_id(response):
            response["repairs"][0]["candidate_id"] = "invented-candidate"
            return response

        def null_revision(response):
            response["repairs"][0]["rule"] = None
            return response

        def unresolved_with_rule(response):
            response["repairs"][0]["decision"] = "unresolved"
            return response

        transforms = [lambda r: {"repairs": []},
                      lambda r: {"repairs": r["repairs"] * 2}, wrong_id,
                      null_revision, unresolved_with_rule, lambda r: {"wrong": []}]
        originals = [self.candidate(), self.candidate(index=1, decision="accept")]
        before = deepcopy(originals)
        for transform in transforms:
            with self.subTest(transform=transform):
                model = RepairModel(repair_transform=transform)
                result, calls, issues = self.repair(originals, model)
                self.assertEqual(originals, before)
                self.assertEqual(result, originals)
                self.assertEqual(model.calls, ["repair"])
                self.assertEqual(calls[0]["status"], "failed")
                self.assertEqual(issues[0]["type"], "repair_failure")

    def test_model_failure_preserves_originals_and_records_a_failure(self):
        originals = [self.candidate(), self.candidate(index=1, decision="accept")]
        result, calls, issues = self.repair(originals, RepairModel(fail_stage="repair"))
        self.assertEqual(result, originals)
        self.assertEqual(calls[0]["status"], "failed")
        self.assertEqual(issues[0]["type"], "repair_failure")

    def test_review_missing_or_duplicate_ids_never_resolves_the_original(self):
        original = self.candidate()
        for transform in [lambda r: {"reviews": []}, lambda r: {"reviews": r["reviews"] * 2}]:
            with self.subTest(transform=transform):
                model = RepairModel(review_transform=transform)
                result, calls, issues = self.repair([original], model)
                self.assertEqual(result[0], original)
                self.assertEqual(len(result), 2)
                self.assertNotIn("review", result[1])
                self.assertEqual(model.calls, ["repair", "review"])
                self.assertEqual(calls[-1]["status"], "failed")
                self.assertEqual(issues[-1]["stage"], "review")

    def test_failed_or_unaccepting_review_leaves_revision_withheld_without_more_repair(self):
        original = self.candidate()
        for decision in ["review", "reject", "failure"]:
            with self.subTest(decision=decision):
                def decide(response):
                    response["reviews"][0]["decision"] = decision
                    return response

                model = RepairModel(fail_stage="review" if decision == "failure" else None,
                                    review_transform=decide)
                result, _, issues = self.repair([original], model)
                self.assertEqual(result[0], original)
                self.assertEqual(model.calls, ["repair", "review"])
                self.assertNotIn("resolved_by", result[0])
                if decision != "failure":
                    self.assertEqual(result[1]["review"]["decision"], decision)
                    self.assertEqual(issues[-1]["type"], "repair_not_accepted")
                else:
                    self.assertNotIn("review", result[1])
                    self.assertEqual(issues[-1]["type"], "repair_failure")

    def test_explicit_refusal_response_never_enters_structural_retry(self):
        class RetryModel(RepairModel):
            def __init__(self, refusal):
                super().__init__(repair_transform=lambda r: {"refusal": refusal})
                self.invalidations = []

            def invalidate_response(self, metadata, reason):
                self.invalidations.append((metadata, reason))

        original = self.candidate()
        for refusal in ["I cannot comply with this request.", "Request declined."]:
            with self.subTest(refusal=refusal):
                model = RetryModel(refusal)
                result, _, issues = self.repair([original], model)
                self.assertEqual(result, [original])
                self.assertEqual(model.calls, ["repair"])
                self.assertEqual(model.invalidations, [])
                self.assertEqual(issues[0]["type"], "repair_blocked")

    def test_blocked_extraction_is_not_repaired_even_without_a_reason(self):
        original = self.candidate()
        original["extraction_status"] = "blocked"
        model = RepairModel()
        result, calls, issues = self.repair([original], model)
        self.assertEqual(result, [original])
        self.assertEqual((model.calls, calls), ([], []))
        self.assertEqual(issues[0]["type"], "repair_blocked")

    def test_malformed_coverage_uses_existing_bounded_cache_recovery(self):
        class RecoveringModel(RepairModel):
            def __init__(self):
                super().__init__()
                self.invalidations = []

            def generate(self, prompt, schema, stage):
                response, metadata = super().generate(prompt, schema, stage)
                if len(self.calls) == 1:
                    response["repairs"] = []
                return response, metadata

            def invalidate_response(self, metadata, reason):
                self.invalidations.append((metadata, reason))

        model = RecoveringModel()
        result, calls, issues = self.repair([self.candidate()], model)
        self.assertEqual(model.calls, ["repair", "repair", "review"])
        self.assertEqual(len(model.invalidations), 1)
        self.assertEqual(len(calls[0]["rejected_responses"]), 1)
        self.assertEqual(issues, [])
        self.assertIn("resolved_by", result[0])

    def test_repaired_or_resolved_candidates_do_not_reenter_the_pass(self):
        repaired = self.candidate()
        repaired["repair_of"] = "earlier-id"
        resolved = self.candidate(index=1)
        resolved["resolved_by"] = "later-id"
        model = RepairModel()
        result, calls, issues = self.repair([repaired, resolved], model)
        self.assertEqual(result, [repaired, resolved])
        self.assertEqual((model.calls, calls, issues), ([], [], []))

    def test_missing_original_chunk_is_reported_without_any_model_call(self):
        original = self.candidate()
        original["chunk_id"] = "missing-chunk"
        model = RepairModel()
        result, calls, issues = self.repair([original], model)
        self.assertEqual(result, [original])
        self.assertEqual(model.calls, [])
        self.assertEqual(calls, [])
        self.assertEqual(issues[0]["type"], "repair_input_error")


if __name__ == "__main__":
    unittest.main()
