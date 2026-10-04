"""Coverage-phase reviews must preserve actual-event decisions and evidence."""

import copy
import itertools
import unittest

from rent_rules.evaluator import canonical_hash, evaluate_branch
from rent_rules.plan_review import apply_coverage_reviews, apply_temporal_reviews, isolate_property_type_domains, normalize_construction_year_comparisons
from rent_rules.eval_logic import evaluate_expression


class PlanReviewTests(unittest.TestCase):
    def test_temporal_review_preserves_relevance_and_missing_dates(self):
        bundle, _, rules = self.fixture()
        bundle['fact_scopes'] = {}
        branch = bundle['plans'][0]['branches'][0]
        branch['source_gates'] = [{'id': 'clock', 'stage': 'event', 'when': {'fact': 'clock_relevant', 'op': 'eq', 'value': True}, 'reason': 'Unimplemented synthetic clock', 'conflict': False}]
        review = {'reviews': [{'team_rule_id': 'synthetic', 'source_rule_sha256': canonical_hash(rules[0]), 'reason': 'Synthetic reviewed date gate', 'replacements': [], 'remove_duplicate_gate_ids': [], 'added_facts': [], 'gate_predicates': [{'id': 'clock', 'expression': {'date_compare': {'left': 'event_date', 'op': 'ge', 'right': 'sent_date', 'years': 0, 'days': 30}}}]}]}
        updated, changed = apply_temporal_reviews(bundle, review, rules)
        self.assertEqual(changed, ['synthetic'])
        new = updated['plans'][0]['branches'][0]
        facts = {k: {'status': 'known', 'value': v} for k,v in [('covered',True),('event',True),('excluded',False),('clock_relevant',False)]}
        self.assertEqual(evaluate_branch(new, facts)['event']['value'], 'true')
        facts['clock_relevant']['value'] = True
        result = evaluate_branch(new, facts)['event']
        self.assertEqual(result['value'], 'unknown')
        self.assertEqual(result['source_questions'], [])
        self.assertEqual(set(result['missing_facts']), {'event_date', 'sent_date'})
        facts.update({'event_date': {'status': 'known', 'value': '2026-09-30'}, 'sent_date': {'status': 'known', 'value': '2026-09-01'}})
        self.assertEqual(evaluate_branch(new, facts)['event']['value'], 'false')
        self.assertEqual(apply_temporal_reviews(updated, review, rules)[0], updated)
        rules[0]['changed'] = True
        with self.assertRaises(ValueError):
            apply_temporal_reviews(bundle, review, rules)

    def fixture(self):
        fact = lambda key: {"fact": key, "op": "eq", "value": True}
        branch = {"branch_id": "conditional", "label": "Conditional duty", "coverage": fact("covered"),
                  "trigger": fact("event"), "exclusions": [{"id": "payment_only", "stage": "coverage", "when": fact("excluded"), "reason": "Synthetic transaction exclusion"}],
                  "source_gates": [{"id": "definition", "stage": "coverage", "when": fact("source_relevant"), "reason": "Synthetic missing transaction definition", "conflict": True}],
                  "requirements": [], "modifiers": [], "duty": "Synthetic duty", "retained_conditions": [],
                  "evidence_refs": [{"criterion_id": "scope", "evidence_index": 0}]}
        rule = {"team_rule_id": "synthetic", "applicability": {"criteria": [{"id": "scope"}]}}
        bundle = {"supported_as_of": ["2026-10-01"], "compilation": {}, "plans": [{"team_rule_id": "synthetic", "branches": [branch]}]}
        review = {"schema_version": "1.0", "as_of": "2026-10-01", "coverage": fact("residential_query"),
                  "reviews": [{"team_rule_id": "synthetic", "source_rule_sha256": canonical_hash(rule),
                               "coverage_criterion_id": "scope", "reason": "Reviewed conditional duty", "event_description": "Synthetic event"}]}
        return bundle, review, [rule]

    def test_all_three_valued_event_combinations_are_preserved(self):
        bundle, review, rules = self.fixture()
        updated, changed = apply_coverage_reviews(bundle, review, rules)
        self.assertEqual(changed, ["synthetic"])
        old = bundle["plans"][0]["branches"][0]
        new = updated["plans"][0]["branches"][0]
        for values in itertools.product([True, False, None], repeat=4):
            facts = {"residential_query": {"status": "known", "value": True}}
            facts.update({key: {"status": "known", "value": value} for key, value in zip(
                ["covered", "event", "excluded", "source_relevant"], values) if value is not None})
            before, after = evaluate_branch(old, facts), evaluate_branch(new, facts)
            for key in ("value", "missing_facts", "source_questions", "conflicts"):
                self.assertEqual(before["event"][key], after["event"][key], (values, key))
        self.assertEqual(old["evidence_refs"], new["evidence_refs"])

    def test_review_is_idempotent_and_preserves_inputs(self):
        bundle, review, rules = self.fixture()
        before = copy.deepcopy(bundle)
        updated, _ = apply_coverage_reviews(bundle, review, rules)
        again, changed = apply_coverage_reviews(updated, review, rules)
        self.assertEqual(bundle, before)
        self.assertEqual(updated, again)
        self.assertEqual(changed, [])

    def test_incompatible_source_or_prior_review_is_rejected(self):
        bundle, review, rules = self.fixture()
        wrong = copy.deepcopy(rules)
        wrong[0]["changed"] = True
        with self.assertRaisesRegex(ValueError, "source changed"):
            apply_coverage_reviews(bundle, review, wrong)
        updated, _ = apply_coverage_reviews(bundle, review, rules)
        review["reviews"][0]["reason"] = "Different review"
        with self.assertRaisesRegex(ValueError, "incompatible coverage review"):
            apply_coverage_reviews(updated, review, rules)

    def test_assessor_category_cannot_disprove_legal_apartment_type(self):
        bundle, _, _ = self.fixture()
        bundle["fact_scopes"] = {"property.use": "address_property"}
        branch = bundle["plans"][0]["branches"][0]
        branch["coverage"] = {"fact": "property.use", "op": "eq", "value": "apartment"}
        fixed, changed = isolate_property_type_domains(bundle)
        self.assertEqual(changed, ["synthetic"])
        expression = fixed["plans"][0]["branches"][0]["coverage"]
        facts = {"property.use": {"status": "known", "value": "multifamily_residential"}}
        self.assertEqual(evaluate_expression(expression, facts)["value"], "unknown")
        facts["case.synthetic.legal_property_type"] = {"status": "known", "value": "apartment"}
        self.assertEqual(evaluate_expression(expression, facts)["value"], "true")
        again, changed = isolate_property_type_domains(fixed)
        self.assertEqual(again, fixed)
        self.assertEqual(changed, [])

    def test_actual_normalized_use_comparison_remains_bound(self):
        bundle, _, _ = self.fixture()
        bundle["fact_scopes"] = {"property.use": "address_property"}
        expression = {"fact": "property.use", "op": "eq", "value": "multifamily_residential"}
        bundle["plans"][0]["branches"][0]["coverage"] = expression
        fixed, changed = isolate_property_type_domains(bundle)
        self.assertEqual(changed, [])
        self.assertEqual(fixed["plans"][0]["branches"][0]["coverage"], expression)

    def test_calendar_year_conversion_preserves_full_year_boundaries(self):
        for op in ["lt", "le", "gt", "ge", "eq", "ne"]:
            for year in [1979, 1980, 1981]:
                bundle, _, _ = self.fixture()
                expression = {"fact": "property.construction_date", "op": op, "value": 1980}
                bundle["plans"][0]["branches"][0]["coverage"] = expression
                fixed, changed = normalize_construction_year_comparisons(bundle)
                self.assertEqual(changed, ["synthetic"])
                actual = fixed["plans"][0]["branches"][0]["coverage"]
                numeric = {"property.construction_date": {"status": "known", "value": year}}
                dates = {"property.construction_date": {"status": "known", "min": str(year)+"-01-01", "max": str(year)+"-12-31"}}
                self.assertEqual(evaluate_expression(expression, numeric)["value"],
                                 evaluate_expression(actual, dates)["value"], (op, year))


if __name__ == "__main__":
    unittest.main()
