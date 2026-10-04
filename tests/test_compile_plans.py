"""Regression checks for lossless compilation accounting and fact isolation."""

import copy
import json
from pathlib import Path
import unittest

from rent_rules.compile_plans import assemble_response, compact_input, group_batches


ROOT = Path(__file__).resolve().parents[1]


class CompilationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = json.loads((ROOT / "outputs/module_a/rules.json").read_text())["rules"]
        cls.base = json.loads((ROOT / "data/evaluation/pilot_rule_plans.json").read_text())

    def draft(self, rule):
        return {"plans": [{
            "team_rule_id": rule["team_rule_id"], "notes": [], "facts": [],
            "branches": [{"branch_id": "synthetic", "label": "Synthetic fixture",
                          "coverage": {"fact": "property.residential_use", "op": "eq", "value": True},
                          "trigger": None, "exclusions": [], "source_gates": [],
                          "duty": "Synthetic fixture, not an authored legal plan.", "retained_conditions": []}],
            "criteria_ledger": [{"criterion_id": c["id"], "branch_ids": ["synthetic"],
                                 "role": "retained_requirement", "reason": "Synthetic accounting fixture"}
                                for c in rule["applicability"]["criteria"]],
            "source_question_ledger": [{"question_index": i, "branch_ids": ["synthetic"],
                                        "disposition": "retained_noncoverage", "reason": "Synthetic fixture"}
                                       for i, _ in enumerate(rule["applicability"]["unresolved_source_questions"])],
        }]}

    def test_omitted_or_duplicate_criterion_cannot_be_called_complete(self):
        rule = self.rules[0]
        for duplicate in (False, True):
            response = self.draft(rule)
            rows = response["plans"][0]["criteria_ledger"]
            rows.append(copy.deepcopy(rows[0])) if duplicate else rows.pop()
            with self.assertRaisesRegex(ValueError, "criterion ledger"):
                assemble_response([rule], response, self.base)

    def test_source_questions_cannot_disappear(self):
        rule = next(r for r in self.rules if r["applicability"]["unresolved_source_questions"])
        response = self.draft(rule)
        response["plans"][0]["source_question_ledger"].pop()
        with self.assertRaisesRegex(ValueError, "source-question ledger"):
            assemble_response([rule], response, self.base)

    def test_unrelated_case_fact_definitions_cannot_alias(self):
        rule = self.rules[0]
        response = self.draft(rule)
        response["plans"][0]["facts"] = [{"key": "owner.ambiguous_count", "subject_scope": "owner_portfolio", "description": "Synthetic", "data_type": "number"}]
        with self.assertRaisesRegex(ValueError, "rule-namespaced"):
            assemble_response([rule], response, self.base)

    def test_bound_fact_subject_cannot_change_to_owner_holdings(self):
        rule = self.rules[0]
        response = self.draft(rule)
        response["plans"][0]["facts"] = [{"key": "property.residential_use", "subject_scope": "owner_portfolio", "description": "Synthetic", "data_type": "boolean"}]
        with self.assertRaisesRegex(ValueError, "Incompatible fact scope"):
            assemble_response([rule], response, self.base)

    def test_evidence_pool_preserves_exact_text_and_all_criteria(self):
        rules = self.rules[:12]
        compact = compact_input(rules)
        for rule, reduced in zip(rules, compact["rules"]):
            for original, criterion in zip(rule["applicability"]["criteria"], reduced["applicability"]["criteria"]):
                self.assertEqual(original["id"], criterion["id"])
                self.assertEqual(original["condition"], criterion["condition"])
                self.assertEqual([e["quote"] for e in original["evidence"]],
                                 [compact["exact_evidence_pool"][key]["quote"] for key in criterion["evidence_refs"]])

    def test_batching_keeps_every_rule_once_without_truncating_large_units(self):
        rules = self.rules[:19]
        batches = group_batches(rules, max_rules=3, max_characters=500)
        self.assertEqual(sorted(r["team_rule_id"] for r in rules),
                         sorted(r["team_rule_id"] for batch in batches for r in batch))
        self.assertTrue(all(len(batch) <= 3 for batch in batches))

    def test_shared_expression_expands_with_rule_specific_fact_identity(self):
        rule = self.rules[0]
        response = self.draft(rule)
        response["templates"] = [{"id": "threshold", "expression": {
            "fact": "case.$RULE_ID.building_rental_units", "op": "ge", "value": 5},
            "facts": [{"key": "case.$RULE_ID.building_rental_units", "subject_scope": "building_rental_units",
                       "description": "Synthetic scoped unit count", "data_type": "number"}]}]
        response["plans"][0]["branches"][0]["coverage"] = {"ref": "threshold"}
        before = copy.deepcopy(response)
        bundle = assemble_response([rule], response, self.base)
        key = "case." + rule["team_rule_id"] + ".building_rental_units"
        self.assertEqual(bundle["plans"][0]["branches"][0]["coverage"], {"fact": key, "op": "ge", "value": 5})
        self.assertEqual(bundle["fact_scopes"][key], "building_rental_units")
        self.assertEqual(response, before)

    def test_missing_or_cyclic_templates_do_not_become_unknown_legal_results(self):
        rule = self.rules[0]
        for templates in ([], [{"id": "loop", "expression": {"ref": "loop"}, "facts": []}]):
            response = self.draft(rule)
            response["templates"] = templates
            response["plans"][0]["branches"][0]["coverage"] = {"ref": "loop"}
            with self.assertRaisesRegex(ValueError, "Missing or cyclic"):
                assemble_response([rule], response, self.base)

    def test_query_premise_cannot_override_nonresidential_evidence(self):
        rule = self.rules[0]
        response = self.draft(rule)
        response["plans"][0]["branches"][0]["coverage"] = {"any": [
            {"fact": "property.residential_use", "op": "eq", "value": True},
            {"fact": "context.residential_rental_lookup", "op": "eq", "value": True}]}
        with self.assertRaisesRegex(ValueError, "query premise"):
            assemble_response([rule], response, self.base)


if __name__ == "__main__":
    unittest.main()
