"""Synthetic tests for lossless consolidation and evidenced relation direction."""

from collections import Counter
from copy import deepcopy
import json
from threading import Barrier, Event, Lock
from types import SimpleNamespace
import unittest

from rent_rules.consolidation import CONSOLIDATION_SCHEMA, consolidate_candidates


AS_OF = "2026-10-01"
RELATION = "The narrower receipt obligation supersedes the general obligation within its stated coverage."


class FakeModel:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def generate(self, prompt, schema, stage):
        self.calls.append((prompt, schema, stage))
        if self.error:
            raise self.error
        if self.response is None:
            records = json.loads(prompt.split("INPUT_RECORDS_JSON:\n", 1)[1])
            response = singleton_response([item["candidate_id"] for item in records])
        else:
            response = deepcopy(self.response)
        return response, {"stage": stage, "cached": False}


class ReverseCompletionModel(FakeModel):
    """Require three simultaneous calls, then finish in reverse state order."""

    def __init__(self, failure_state=None):
        super().__init__()
        self.barrier = Barrier(3)
        self.release = {state: Event() for state in ("CA", "MA", "NJ")}
        self.release["NJ"].set()
        self.lock = Lock()
        self.active = 0
        self.max_active = 0
        self.finished = []
        self.failure_state = failure_state

    def generate(self, prompt, schema, stage):
        records = json.loads(prompt.split("INPUT_RECORDS_JSON:\n", 1)[1])
        state = records[0]["rule"]["jurisdiction"]
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.barrier.wait(timeout=5)
            if not self.release[state].wait(timeout=5):
                raise RuntimeError("The preceding state did not finish.")
            with self.lock:
                self.finished.append(state)
            if state == self.failure_state:
                raise RuntimeError("Synthetic model failure for " + state)
            return super().generate(prompt, schema, stage)
        finally:
            with self.lock:
                self.active -= 1
            next_state = {"NJ": "MA", "MA": "CA"}.get(state)
            if next_state:
                self.release[next_state].set()


def singleton_response(ids, interactions=None, conflicts=None):
    return {"groups": [{"candidate_ids": [cid], "preferred_id": cid, "reason": "Independent obligation."}
                       for cid in ids],
            "conflicts": conflicts or [], "interactions": interactions or []}


class ConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.sources = {}

    def candidate(self, cid, requirement="The provider shall issue a receipt.", jurisdiction="CA",
                  document_type="statute", **changes):
        doc_id = "DOC-" + cid
        source = SimpleNamespace(text="Heading\n\n" + requirement + "\n" + RELATION,
                                 url="https://example.test/" + cid,
                                 retrieved_at="2026-10-01T12:00:00Z", source_type="official")
        self.sources[doc_id] = source
        rule = {"team_rule_id": cid, "jurisdiction": jurisdiction,
                "level": "city" if "," in jurisdiction else "state",
                "category": "security_deposits", "status": "in_force", "title": "Receipt duty",
                "requirement": requirement, "key_value": None, "coverage_conditions": "Covered rentals",
                "conditions": [], "exemptions": None, "penalty": None, "limitations": [],
                "effective_date": None, "end_date": None, "temporal_notes": None,
                "citation": "Example Act section A", "quoted_span": requirement,
                "source_doc_id": doc_id, "source_url": source.url, "retrieved_at": source.retrieved_at,
                "as_of": AS_OF, "overrides": [], "conflict_flag": False,
                "conflict_note": None, "interaction": None, "confidence": 0.8}
        rule.update(changes)
        return {"candidate_id": cid, "doc_id": doc_id, "rule": rule, "document_type": document_type,
                "evidence": [{"field": "requirement", "quote": requirement,
                              "source_doc_id": doc_id, "source_url": source.url}],
                "review": {"decision": "accept"}, "problems": []}

    def run_consolidation(self, candidates, model=None):
        return consolidate_candidates(candidates, model or FakeModel(), self.sources, AS_OF)

    def assert_coverage(self, candidates, audit):
        self.assertEqual(Counter(item["candidate_id"] for item in candidates),
                         Counter(cid for entry in audit for cid in entry["candidate_ids"]))

    def merge_response(self, ids, preferred=None):
        return {"groups": [{"candidate_ids": ids, "preferred_id": preferred or ids[0],
                            "reason": "The same obligation in equivalent language."}],
                "conflicts": [], "interactions": []}

    def relation(self, direction="yields_to", quote=RELATION):
        return {"from_id": "general", "to_id": "narrow", "direction": direction,
                "evidence_candidate_id": "narrow", "quote": quote,
                "reason": "The quoted clause expressly governs covered transactions only."}

    def related_candidates(self):
        general = self.candidate("general", "The provider shall give a general receipt.")
        narrow = self.candidate("narrow", "The provider shall give a detailed receipt.",
                                coverage_conditions="Covered transactions requiring itemized receipts")
        narrow["evidence"].append({"field": "interaction", "quote": RELATION,
                                   "source_doc_id": narrow["doc_id"]})
        return [general, narrow]

    def test_semantic_merge_preserves_primary_statute_and_all_evidence(self):
        statute = self.candidate("original", document_type="statute")
        guidance = self.candidate("summary", "A receipt must be issued by the provider.",
                                  document_type="official_guidance", confidence=1.0)
        response = self.merge_response(["original", "summary"], preferred="summary")
        original_input = deepcopy([statute, guidance])
        rules, audit, queue, calls = self.run_consolidation([statute, guidance], FakeModel(response))
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["requirement"], statute["rule"]["requirement"])
        self.assertEqual(rules[0]["source_doc_id"], statute["doc_id"])
        self.assertEqual(rules[0]["source_doc_ids"], ["DOC-original", "DOC-summary"])
        self.assertEqual({e["source_doc_id"] for e in rules[0]["evidence"]}, {"DOC-original", "DOC-summary"})
        self.assert_coverage([statute, guidance], audit)
        self.assertEqual([statute, guidance], original_input)
        self.assertEqual(queue, [])
        self.assertEqual(len(calls), 1)

    def test_ids_and_outputs_are_stable_when_input_order_changes(self):
        first = self.candidate("a")
        second = self.candidate("b", "The provider must issue a receipt.")
        response = self.merge_response(["b", "a"], "a")
        forward = self.run_consolidation([first, second], FakeModel(response))
        reverse = self.run_consolidation([second, first], FakeModel(response))
        self.assertEqual(forward, reverse)

    def test_three_states_are_separate_calls_and_empty_input_is_safe(self):
        candidates = [self.candidate(state + str(index), jurisdiction=state,
                                     requirement="The provider shall complete task {}.".format(index))
                      for state in ("NJ", "MA", "CA") for index in (1, 2)]
        model = FakeModel()
        rules, audit, queue, calls = self.run_consolidation(candidates, model)
        self.assertEqual([item["jurisdiction_group"] for item in calls], ["CA", "MA", "NJ"])
        self.assert_coverage(candidates, audit)
        self.assertEqual(len(rules), 6)
        self.assertEqual(queue, [])
        self.assertEqual(self.run_consolidation([], FakeModel(error=RuntimeError("unused"))), ([], [], [], []))
        self.assertTrue(all(call[1] == CONSOLIDATION_SCHEMA and call[2] == "consolidate" for call in model.calls))

    def test_single_candidate_skips_model(self):
        candidate = self.candidate("alone")
        model = FakeModel(error=RuntimeError("must not be called"))
        rules, audit, queue, calls = self.run_consolidation([candidate], model)
        self.assertEqual(len(rules), 1)
        self.assertEqual(model.calls, [])
        self.assert_coverage([candidate], audit)

    def test_concurrent_calls_preserve_output_despite_reverse_completion(self):
        candidates = [self.candidate(state + str(index), jurisdiction=state,
                                     requirement="The provider shall complete task {}.".format(index))
                      for state in ("CA", "MA", "NJ") for index in (1, 2)]
        expected = self.run_consolidation(candidates, FakeModel())
        model = ReverseCompletionModel()
        result = self.run_consolidation(candidates, model)
        self.assertEqual(model.max_active, 3)
        self.assertEqual(model.finished, ["NJ", "MA", "CA"])
        self.assertEqual(result, expected)
        self.assertEqual([call["jurisdiction_group"] for call in result[3]], ["CA", "MA", "NJ"])

    def test_concurrent_state_failure_retains_fallback_and_other_results(self):
        candidates = [self.candidate(state + str(index), jurisdiction=state,
                                     requirement="The provider shall complete task {}.".format(index))
                      for state in ("CA", "MA", "NJ") for index in (1, 2)]
        model = ReverseCompletionModel(failure_state="MA")
        rules, audit, queue, calls = self.run_consolidation(candidates, model)
        self.assertEqual(model.max_active, 3)
        self.assertEqual(model.finished, ["NJ", "MA", "CA"])
        self.assertEqual(len(rules), 6)
        self.assert_coverage(candidates, audit)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["type"], "consolidation_fallback")
        self.assertEqual(queue[0]["jurisdiction_group"], "MA")
        self.assertEqual([call["jurisdiction_group"] for call in calls], ["CA", "MA", "NJ"])
        self.assertEqual(calls[1]["status"], "failed")
        self.assertEqual(calls[1]["error"], "Synthetic model failure for MA")
        self.assertNotIn("status", calls[0])
        self.assertNotIn("status", calls[2])

    def test_omitted_duplicate_or_invented_candidate_falls_back_losslessly(self):
        candidates = [self.candidate("a"), self.candidate("b", "The provider shall retain the receipt.")]
        for ids in (["a"], ["a", "a", "b"], ["a", "b", "invented"]):
            with self.subTest(ids=ids):
                rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(self.merge_response(ids)))
                self.assertEqual(len(rules), 2)
                self.assert_coverage(candidates, audit)
                self.assertEqual(queue[0]["type"], "consolidation_fallback")

    def test_cross_jurisdiction_category_status_and_date_merge_are_rejected(self):
        first = self.candidate("a")
        for change in ({"jurisdiction": "Example City, CA", "level": "city"},
                       {"category": "application_screening_fees"}, {"status": "pending"},
                       {"effective_date": "2026-01-01"}, {"end_date": "2026-12-31"}):
            with self.subTest(change=change):
                second = self.candidate("b", **change)
                rules, audit, queue, calls = self.run_consolidation([first, second], FakeModel(self.merge_response(["a", "b"])))
                self.assertEqual(len(rules), 2)
                self.assert_coverage([first, second], audit)
                self.assertEqual(queue[0]["type"], "consolidation_fallback")
                self.assertFalse(any(rule["conflict_flag"] for rule in rules))

    def test_numeric_penalty_and_structured_conditions_cannot_disappear(self):
        pairs = [({"key_value": "30 days"}, {"key_value": "60 days"}),
                 ({"penalty": "$500"}, {"penalty": "$600"}),
                 ({"coverage_conditions": "Buildings with 4 units"}, {"coverage_conditions": "Buildings with 5 units"}),
                 ({"conditions": [{"fact": "owner_occupied", "operator": "eq", "value": "true", "logic_group": "all"}]},
                  {"conditions": [{"fact": "owner_occupied", "operator": "eq", "value": "false", "logic_group": "all"}]}),
                 ({"key_value": None}, {"key_value": "30 days"})]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                candidates = [self.candidate("a", **left), self.candidate("b", **right)]
                rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(self.merge_response(["a", "b"])))
                self.assertEqual(len(rules), 2)
                self.assert_coverage(candidates, audit)
                self.assertEqual(queue[0]["type"], "consolidation_fallback")

    def test_exact_fallback_keeps_distinct_citations_and_annual_parameters(self):
        candidates = [self.candidate("a", key_value="3%", citation="Example Act A"),
                      self.candidate("b", key_value="4%", citation="Example Act A"),
                      self.candidate("c", key_value="3%", citation="Example Act B")]
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(error=RuntimeError("offline")))
        self.assertEqual(len(rules), 3)
        self.assert_coverage(candidates, audit)
        self.assertEqual(calls[0]["status"], "failed")
        self.assertEqual(queue[0]["type"], "consolidation_fallback")

    def test_exact_fallback_can_combine_identical_claims_without_losing_sources(self):
        candidates = [self.candidate("a"), self.candidate("b")]
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(error=RuntimeError("offline")))
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["source_doc_ids"], ["DOC-a", "DOC-b"])
        self.assert_coverage(candidates, audit)

    def test_yields_to_creates_only_stronger_to_weaker_override(self):
        candidates = self.related_candidates()
        response = singleton_response(["general", "narrow"], [self.relation()])
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(response))
        by_source = {rule["source_doc_id"]: rule for rule in rules}
        general, narrow = by_source["DOC-general"], by_source["DOC-narrow"]
        self.assertEqual(narrow["overrides"], [general["team_rule_id"]])
        self.assertEqual(general["overrides"], [])
        relation = narrow["interactions"][0]
        self.assertEqual(relation["direction"], "yields_to")
        self.assertEqual(relation["evidence"]["quote"], RELATION)
        self.assertEqual(relation["scope"]["to_coverage_conditions"], narrow["coverage_conditions"])
        self.assertEqual(queue, [])

    def test_overrides_preserves_stated_direction(self):
        candidates = self.related_candidates()
        relation = self.relation("overrides")
        relation.update(from_id="narrow", to_id="general")
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(singleton_response(["general", "narrow"], [relation])))
        by_source = {rule["source_doc_id"]: rule for rule in rules}
        self.assertEqual(by_source["DOC-narrow"]["overrides"], [by_source["DOC-general"]["team_rule_id"]])

    def test_missing_or_changed_evidence_cannot_create_relationship(self):
        candidates = self.related_candidates()
        for quote in (RELATION.replace("supersedes", "complements"), "Unsupported evidence that has no original source match."):
            rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(singleton_response(["general", "narrow"], [self.relation(quote=quote)])))
            self.assertTrue(all(not rule["overrides"] for rule in rules))
            self.assertTrue(all(not rule.get("interactions") for rule in rules))
            self.assertEqual(queue[0]["type"], "unsupported_interaction")
        # Text elsewhere in the source is insufficient if not supplied as
        # accepted candidate evidence to the consolidation model.
        candidates[1]["evidence"] = candidates[1]["evidence"][:1]
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(singleton_response(["general", "narrow"], [self.relation()])))
        self.assertEqual(queue[0]["type"], "unsupported_interaction")

    def test_whitespace_evidence_is_restored_and_potential_conflict_is_not_override(self):
        candidates = self.related_candidates()
        response = singleton_response(["general", "narrow"], [self.relation("potential_conflict", RELATION.replace(" ", "\n"))])
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(response))
        self.assertTrue(all(rule["conflict_flag"] for rule in rules))
        self.assertTrue(all(not rule["overrides"] for rule in rules))
        self.assertTrue(all(rule["interactions"][0]["evidence"]["quote"] == RELATION for rule in rules))
        self.assertEqual(queue[0]["type"], "potential_conflict")

    def test_coexists_is_recorded_without_override_or_conflict(self):
        candidates = self.related_candidates()
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(singleton_response(["general", "narrow"], [self.relation("coexists")])))
        self.assertTrue(all(not rule["overrides"] and not rule["conflict_flag"] for rule in rules))
        self.assertTrue(all(rule["interactions"] for rule in rules))

    def test_conflict_never_discards_candidates_or_creates_override(self):
        candidates = [self.candidate("a", key_value="30 days"), self.candidate("b", key_value="60 days")]
        response = singleton_response(["a", "b"], conflicts=[{"candidate_ids": ["a", "b"],
                                                               "reason": "Two sources give different deadlines for the same cited duty."}])
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(response))
        self.assertEqual(len(rules), 2)
        self.assertTrue(all(rule["conflict_flag"] and not rule["overrides"] for rule in rules))
        self.assert_coverage(candidates, audit)
        self.assertEqual(queue[0]["type"], "semantic_conflict")

    def test_opposing_override_directions_are_queued_instead_of_applied(self):
        candidates = self.related_candidates()
        response = singleton_response(["general", "narrow"], [self.relation("overrides"), self.relation("yields_to")])
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(response))
        self.assertTrue(all(not rule["overrides"] and rule["conflict_flag"] for rule in rules))
        self.assertTrue(all(item["type"] == "contradictory_interactions" for item in queue))

    def test_pending_future_or_failed_rule_cannot_override_current_law(self):
        for status in ("pending", "not_yet_effective", "failed"):
            with self.subTest(status=status):
                candidates = self.related_candidates()
                candidates[1]["rule"]["status"] = status
                response = singleton_response(["general", "narrow"], [self.relation()])
                rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(response))
                self.assertTrue(all(not rule["overrides"] for rule in rules))
                self.assertTrue(all(not rule["interactions"][0]["active_as_of"] for rule in rules))
                self.assertEqual(queue[0]["type"], "inactive_interaction")

    def test_conflicting_pair_cannot_also_receive_a_deterministic_override(self):
        candidates = self.related_candidates()
        response = singleton_response(["general", "narrow"], [self.relation()],
                                      [{"candidate_ids": ["general", "narrow"],
                                        "reason": "The sources do not settle the priority of these obligations."}])
        rules, audit, queue, calls = self.run_consolidation(candidates, FakeModel(response))
        self.assertTrue(all(not rule["overrides"] and rule["conflict_flag"] for rule in rules))
        self.assertIn("inactive_interaction", {item["type"] for item in queue})

    def test_merge_preserves_conflict_notes_and_existing_interaction_metadata(self):
        first = self.candidate("a", conflict_flag=True, conflict_note="Source A notes unresolved timing.",
                               interaction="Rule A also refers to a supplementary requirement.",
                               related_citations=["Example Act section B"])
        second = self.candidate("b", conflict_flag=True, conflict_note="Source B notes unresolved coverage.",
                                interaction="Rule A also refers to a complementary requirement.",
                                related_citations=["Example Act section C"])
        rules, audit, queue, calls = self.run_consolidation([first, second], FakeModel(self.merge_response(["a", "b"])))
        self.assertEqual(len(rules), 1)
        for candidate in (first, second):
            self.assertIn(candidate["rule"]["conflict_note"], rules[0]["conflict_note"])
            self.assertIn(candidate["rule"]["interaction"], rules[0]["interaction"])
        self.assertEqual(rules[0]["related_citations"], ["Example Act section B", "Example Act section C"])
        # Without a model equivalence decision these are not exact claims.
        rules, audit, queue, calls = self.run_consolidation([first, second], FakeModel(error=RuntimeError("offline")))
        self.assertEqual(len(rules), 2)


if __name__ == "__main__":
    unittest.main()
