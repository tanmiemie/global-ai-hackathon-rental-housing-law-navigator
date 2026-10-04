"""Public lookup explanations must remain supported and understandable."""

from copy import deepcopy
import unittest

from rent_rules.evaluator import evaluate_rule
from rent_rules.lookup_output import format_lookup_entry


RULE_ID = "r-" + "a" * 64
TENANCY = "case." + RULE_ID + ".tenancy_existed_before_cutoff"
EXEMPTION = "case." + RULE_ID + ".affordable_deed_restriction"
DESCRIPTION = "The tenancy existed immediately before July 1, 2020."
PUBLIC_FIELDS = {"team_rule_id", "result", "explanation", "conflict_flag"}


def known(value):
    return {"status": "known", "value": value}


def predicate(key):
    return {"fact": key, "op": "eq", "value": True}


def fixture():
    rule = {
        "team_rule_id": RULE_ID,
        "title": "Synthetic conditional protection",
        "jurisdiction": "CA",
        "category": "security_deposits",
        "status": "in_force",
        "as_of": "2026-10-01",
        "conflict_flag": False,
        "citation": "Synthetic Civil Code § 12(b)(1)",
        "source_doc_id": "DTEST-001",
        "source_url": "https://example.test/source/statute-12",
        "retrieved_at": "2026-09-30",
        "applicability": {"rule_kind": "transaction_rule"},
    }
    plan = {
        "team_rule_id": RULE_ID,
        "fact_definitions": [{"key": TENANCY, "subject_scope": "subject_tenancy",
                              "description": DESCRIPTION, "data_type": "boolean"}],
        "branches": [{
            "branch_id": "primary",
            "label": "Primary protection",
            "coverage": predicate(TENANCY),
            "trigger": predicate("event"),
            "exclusions": [],
            "requirements": [],
            "modifiers": [],
            "source_gates": [],
            "duty": "Return the deposit when the qualifying tenancy ends.",
            "retained_conditions": ["Only the tenant's documented payment is covered."],
            "evidence_refs": [],
        }],
    }
    facts = {"jurisdiction.state": known("CA"), TENANCY: known(True), "event": known(True)}
    return rule, plan, facts


class LookupOutputTests(unittest.TestCase):
    def render(self, rule, plan, facts):
        record = evaluate_rule(rule, plan, facts)
        return record, format_lookup_entry(rule, record, plan, facts)

    def assert_supported_citation(self, rule, entry):
        self.assertEqual(set(entry), PUBLIC_FIELDS)
        self.assertIn("Citation: " + rule["citation"], entry["explanation"])
        for field in ("source_doc_id", "source_url", "retrieved_at"):
            self.assertIn(rule[field], entry["explanation"])

    def test_applies_has_exact_source_and_retains_qualifying_conditions(self):
        rule, plan, facts = fixture()
        record, entry = self.render(rule, plan, facts)
        self.assertEqual(entry["result"], "applies")
        self.assertEqual(entry["team_rule_id"], record["team_rule_id"])
        self.assertEqual(entry["conflict_flag"], record["conflict_flag"])
        self.assert_supported_citation(rule, entry)
        self.assertIn(plan["branches"][0]["duty"], entry["explanation"])
        self.assertIn(plan["branches"][0]["retained_conditions"][0], entry["explanation"])
        self.assertIn("Coverage does not establish compliance", entry["explanation"])

    def test_unknown_fact_includes_definition_scope_and_observed_reason(self):
        rule, plan, facts = fixture()
        facts[TENANCY] = {"status": "unknown", "reason": "Lease commencement records were not supplied."}
        record, entry = self.render(rule, plan, facts)
        explanation = entry["explanation"]
        self.assertEqual(entry["result"], "unknown")
        self.assertTrue(explanation.startswith("Unknown because "))
        self.assertIn(DESCRIPTION.rstrip("."), explanation)
        self.assertIn("subject tenancy", explanation.lower().replace("_", " "))
        self.assertIn(facts[TENANCY]["reason"].rstrip("."), explanation)
        self.assertNotIn(TENANCY, explanation)
        self.assertNotIn("case.r-", explanation)
        self.assertIn("Unresolved inputs", explanation)
        self.assertNotIn("Decisive missing facts:", explanation)
        self.assert_supported_citation(rule, entry)
        self.assertEqual(record["missing_facts"], [TENANCY])

    def test_jurisdiction_unknown_names_geography_and_supplied_verification_gap(self):
        rule, plan, facts = fixture()
        rule["jurisdiction"] = "Example City, CA"
        reason = "The address did not yield a verified municipal boundary match."
        facts["jurisdiction.city"] = {"status": "unknown", "reason": reason}
        _, entry = self.render(rule, plan, facts)
        explanation = entry["explanation"]
        self.assertTrue(explanation.startswith("Unknown because "))
        self.assertIn("jurisdiction", explanation.lower())
        self.assertIn("city", explanation.lower())
        self.assertIn(reason.rstrip("."), explanation)
        self.assert_supported_citation(rule, entry)

    def test_source_gap_is_explained_without_inventing_missing_address_facts(self):
        rule, plan, facts = fixture()
        reason = "The source omits the cross-referenced definition of a qualifying deposit."
        plan["branches"][0]["source_gates"] = [{
            "id": "definition", "stage": "coverage", "when": None,
            "reason": reason, "conflict": False,
        }]
        record, entry = self.render(rule, plan, facts)
        self.assertEqual(record["missing_facts"], [])
        self.assertTrue(entry["explanation"].startswith("Unknown because "))
        self.assertIn("source", entry["explanation"].lower())
        self.assertIn(reason.rstrip("."), entry["explanation"])
        self.assertFalse(entry["conflict_flag"])
        self.assert_supported_citation(rule, entry)

    def test_conflicting_observations_keep_conflict_flag_and_concrete_reason(self):
        rule, plan, facts = fixture()
        reason = "The signed lease and amendment give different commencement dates."
        facts[TENANCY] = {"status": "conflicting", "alternatives": [True, False], "reason": reason}
        record, entry = self.render(rule, plan, facts)
        self.assertEqual(entry["result"], "unknown")
        self.assertTrue(entry["conflict_flag"])
        self.assertEqual(entry["conflict_flag"], record["conflict_flag"])
        self.assertIn("conflict", entry["explanation"].lower())
        self.assertIn(reason.rstrip("."), entry["explanation"])
        self.assertNotIn(TENANCY, entry["explanation"])
        self.assert_supported_citation(rule, entry)

    def test_uncompiled_rule_explicitly_distinguishes_implementation_gap(self):
        rule, _, facts = fixture()
        record, entry = self.render(rule, None, facts)
        self.assertTrue(record["compilation_gap"])
        self.assertTrue(entry["explanation"].startswith("Unknown because "))
        self.assertIn("plan", entry["explanation"].lower())
        self.assertIn("rule-conversion gap", entry["explanation"])
        self.assert_supported_citation(rule, entry)

    def test_missing_definition_uses_readable_fact_name_without_opaque_id(self):
        rule, plan, facts = fixture()
        plan.pop("fact_definitions")
        del facts[TENANCY]
        _, entry = self.render(rule, plan, facts)
        self.assertNotIn("case.r-", entry["explanation"])
        self.assertIn("tenancy existed before cutoff", entry["explanation"].lower())

    def test_alternative_unknown_inputs_are_not_labeled_as_all_required(self):
        rule, plan, facts = fixture()
        plan["branches"][0]["coverage"] = {"any": [predicate(TENANCY), predicate(EXEMPTION)]}
        del facts[TENANCY]
        _, entry = self.render(rule, plan, facts)
        self.assertEqual(entry["result"], "unknown")
        self.assertIn("Unresolved inputs", entry["explanation"])
        self.assertNotIn("Decisive missing facts:", entry["explanation"])
        self.assertNotIn("case.r-", entry["explanation"])

    def test_true_or_branch_stays_applies_despite_other_unknown_input(self):
        rule, plan, facts = fixture()
        plan["branches"][0]["coverage"] = {"any": [predicate(TENANCY), predicate(EXEMPTION)]}
        record, entry = self.render(rule, plan, facts)
        self.assertEqual(record["missing_facts"], [])
        self.assertEqual(entry["result"], "applies")
        self.assertFalse(entry["explanation"].startswith("Unknown because "))
        self.assert_supported_citation(rule, entry)

    def test_unknown_exclusion_is_not_rewritten_as_an_exemption(self):
        rule, plan, facts = fixture()
        plan["branches"][0]["exclusions"] = [{
            "id": "affordable", "stage": "coverage", "when": predicate(EXEMPTION),
            "reason": "The documented affordability restriction creates an exclusion.",
        }]
        record, entry = self.render(rule, plan, facts)
        self.assertEqual(record["result"], "unknown")
        self.assertEqual(entry["result"], "unknown")
        self.assertIn("affordable deed restriction", entry["explanation"].lower())

    def test_source_conflict_notes_survive_for_applicable_rule(self):
        rule, plan, facts = fixture()
        reason = "Two source provisions state different rates for the same deposit."
        plan["branches"][0]["source_gates"] = [{
            "id": "rates", "stage": "calculation", "when": None,
            "reason": reason, "conflict": True,
        }]
        record, entry = self.render(rule, plan, facts)
        self.assertEqual(entry["result"], "applies")
        self.assertEqual(entry["conflict_flag"], record["conflict_flag"])
        self.assertTrue(entry["conflict_flag"])
        self.assertIn(reason, entry["explanation"])
        self.assert_supported_citation(rule, entry)

    def test_formatter_does_not_mutate_decision_plan_facts_or_rule_metadata(self):
        rule, plan, facts = fixture()
        del facts[TENANCY]
        record = evaluate_rule(rule, plan, facts)
        arguments = (rule, record, plan, facts)
        before = deepcopy(arguments)
        first = format_lookup_entry(*arguments)
        second = format_lookup_entry(*arguments)
        self.assertEqual(arguments, before)
        self.assertEqual(first, second)

    def test_every_returned_status_requires_all_citation_metadata(self):
        for status in ("in_force", "pending", "not_yet_effective"):
            for field in ("citation", "source_doc_id", "source_url", "retrieved_at"):
                for bad_value in (None, "", "  "):
                    with self.subTest(status=status, field=field, value=bad_value):
                        rule, plan, facts = fixture()
                        rule["status"] = status
                        record = evaluate_rule(rule, plan, facts)
                        rule[field] = bad_value
                        with self.assertRaises(ValueError):
                            format_lookup_entry(rule, record, plan, facts)
        rule, plan, facts = fixture()
        del facts[TENANCY]
        record = evaluate_rule(rule, plan, facts)
        del rule["citation"]
        with self.assertRaises(ValueError):
            format_lookup_entry(rule, record, plan, facts)

    def test_unknown_without_structured_root_cause_is_rejected(self):
        rule, plan, facts = fixture()
        record = evaluate_rule(rule, plan, facts)
        record["result"] = "unknown"
        record["explanation"] = "Applicability unknown."
        with self.assertRaises(ValueError):
            format_lookup_entry(rule, record, plan, facts)

    def test_omitted_rule_cannot_be_serialized_as_returned_lookup(self):
        rule, plan, facts = fixture()
        facts["jurisdiction.state"] = known("NJ")
        record = evaluate_rule(rule, plan, facts)
        self.assertIsNone(record["result"])
        with self.assertRaises(ValueError):
            format_lookup_entry(rule, record, plan, facts)


if __name__ == "__main__":
    unittest.main()
