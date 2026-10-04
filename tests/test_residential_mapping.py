"""Guard residential classification without manufacturing legal eligibility.

Real-row regressions only check the supplied normalized use. Synthetic rules
exercise evaluator behavior and do not describe a legal determination.
"""

import csv
from pathlib import Path
import tempfile
import unittest

from rent_rules.eval_facts import load_address_facts
from rent_rules.evaluator import evaluate_rule
from rent_rules.lookup_output import format_lookup_entry


ROOT = Path(__file__).resolve().parents[1]
RESIDENTIAL_CLASSES = (
    "multifamily_residential", "subsidized_multifamily", "mixed_use_multifamily",
    "specialized_residential", "residential_tic",
)


def predicate(key, value=True):
    return {"fact": key, "op": "eq", "value": value}


def synthetic_rule(coverage=None, exclusions=None, trigger=None):
    rule = {
        "team_rule_id": "synthetic-residential-rule", "title": "Synthetic duty",
        "jurisdiction": "CA", "category": "synthetic", "status": "in_force",
        "applicability": {"rule_kind": "transaction_rule"},
        "citation": "Synthetic rule section 1", "source_doc_id": "DTEST",
        "source_url": "https://example.test/synthetic", "retrieved_at": "2026-10-01",
    }
    plan = {"branches": [{
        "branch_id": "main", "label": "Synthetic residential branch",
        "coverage": coverage or {"all": [
            predicate("property.residential_use"),
            predicate("context.residential_rental_lookup"),
        ]},
        "trigger": trigger, "exclusions": exclusions or [],
        "requirements": [], "modifiers": [], "source_gates": [],
        "duty": "Perform a fictional action if the event occurs.",
        "retained_conditions": [], "evidence_refs": [],
    }]}
    return rule, plan


class ResidentialMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "addresses.csv"
        self.row = {
            "address_id": "ATEST", "legal_state": "CA", "legal_city": "Fictional City",
            "jurisdiction_status": "verified", "jurisdiction_evidence_ref": "Synthetic boundary record",
            "resolved_property_use": "multifamily_residential",
            "property_use_validation_status": "verified",
            "property_use_source": "Synthetic normalized-use dictionary",
            "property_use_explanation": "Synthetic code and description agree.",
            "original_use_code": "SYNTHETIC", "original_use_description": "Synthetic residence",
            "original_source_retrieved_at": "2026-10-01T01:00:00+00:00",
            "resolved_year_built": "1970", "year_built_validation_status": "verified",
            "unit_validation_status": "verified", "unit_value_type": "exact",
            "resolved_units": "5", "resolved_units_min": "5", "resolved_units_max": "5",
        }

    def bind(self, **changes):
        row = dict(self.row, **changes)
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        return load_address_facts(self.path, "ATEST")

    def test_verified_residential_classes_preserve_subtype_and_source_provenance(self):
        for use in RESIDENTIAL_CLASSES:
            with self.subTest(use=use):
                bound = self.bind(resolved_property_use=use)
                facts = bound["facts"]
                residential = facts["property.residential_use"]
                self.assertEqual((residential["status"], residential["value"]), ("known", True))
                self.assertEqual(residential["subject_scope"], "address_property")
                self.assertEqual(facts["property.use"]["value"], use)
                self.assertEqual(bound["address"]["resolved_property_use"], use)
                self.assertEqual(facts["property.use"]["reason"], self.row["property_use_explanation"])
                evidence = residential["evidence"][0]
                self.assertEqual(evidence["path"], str(self.path.resolve()))
                self.assertEqual(evidence["row_id"], "ATEST")
                self.assertEqual(evidence["retrieved_at"], self.row["original_source_retrieved_at"])
                self.assertIn("property_use_validation_status", evidence["fields"])
                self.assertIn("property_use_explanation", evidence["fields"])
                self.assertIn("original_use_description", evidence["fields"])
                self.assertEqual(residential["evidence"], facts["property.use"]["evidence"])

    def test_classification_does_not_create_case_or_eligibility_facts(self):
        for use in RESIDENTIAL_CLASSES:
            with self.subTest(use=use):
                facts = self.bind(resolved_property_use=use)["facts"]
                self.assertFalse(any(key.startswith(("case.", "tenancy.", "tenant.", "owner.",
                                                       "transaction.")) for key in facts))
                for key in (
                    "property.housing_assistance_program_applies",
                    "property.affordable_housing_subsidy_agreement",
                    "property.recorded_affordable_housing_restriction",
                    "property.care_license", "property.separately_alienable_title",
                    "property.certificate_of_occupancy_date", "property.building_unit_count",
                    "property.rental_units", "property.exempt",
                ):
                    self.assertNotIn(key, facts)
                self.assertEqual(facts["source.reported_units"]["subject_scope"], "source_reported_count")
                self.assertTrue(facts["context.residential_rental_lookup"]["assumed_query_context"])

    def test_scope_notes_retain_the_limits_of_each_additional_subtype(self):
        terms = {
            "subsidized_multifamily": ("residential-rental query", "subsidy", "separate facts"),
            "mixed_use_multifamily": ("residential", "component", "not the commercial premises"),
            "specialized_residential": ("residential-rental query", "licensing", "separate facts"),
            "residential_tic": ("residential-rental query", "ownership", "does not establish a tenant"),
        }
        for use, expected_terms in terms.items():
            with self.subTest(use=use):
                fact = self.bind(resolved_property_use=use)["facts"]["property.residential_use"]
                note = fact["scope_note"]
                self.assertIn(use, note)
                for term in expected_terms:
                    self.assertIn(term, note)
        self.assertNotIn("scope_note", self.bind()["facts"]["property.residential_use"])
        for status in ("partial", "conflict"):
            fact = self.bind(resolved_property_use="mixed_use_multifamily",
                             property_use_validation_status=status)["facts"]["property.residential_use"]
            self.assertNotIn("scope_note", fact)

    def test_scope_limits_reach_applies_and_unknown_explanations_with_citations(self):
        for use in RESIDENTIAL_CLASSES[1:]:
            for mode, expected_result in (("coverage", "applies"), ("event", "unknown")):
                with self.subTest(use=use, mode=mode):
                    facts = self.bind(resolved_property_use=use)["facts"]
                    rule, plan = synthetic_rule(trigger=predicate("transaction.synthetic_event"))
                    decision = evaluate_rule(rule, plan, facts, mode=mode)
                    entry = format_lookup_entry(rule, decision, plan, facts)
                    self.assertEqual(entry["result"], expected_result)
                    self.assertEqual(set(entry), {"team_rule_id", "result", "explanation", "conflict_flag"})
                    note = facts["property.residential_use"]["scope_note"]
                    self.assertIn(note, entry["explanation"])
                    self.assertIn("Citation: " + rule["citation"], entry["explanation"])
                    self.assertLess(entry["explanation"].index(note), entry["explanation"].index("Citation:"))
                    if expected_result == "unknown":
                        self.assertTrue(entry["explanation"].startswith("Unknown because "))

    def test_unknown_unrecognized_and_raw_labels_do_not_become_residential(self):
        for use in ("", "unknown", "mixed_use", "commercial", "apartment", "residential",
                    "mixed_use_multifamily_unverified", "MULTIFAMILY_RESIDENTIAL"):
            with self.subTest(use=use):
                facts = self.bind(resolved_property_use=use,
                                  original_use_description="Residential Apartments",
                                  property_use_explanation="A residential-looking raw label.")["facts"]
                self.assertEqual(facts["property.residential_use"]["status"], "unknown")
                self.assertNotIn("value", facts["property.residential_use"])

    def test_nonverified_residential_classification_is_never_promoted(self):
        for use in RESIDENTIAL_CLASSES:
            for status in ("", "unknown", "partial", "unverified", "Verified"):
                with self.subTest(use=use, status=status):
                    facts = self.bind(resolved_property_use=use,
                                      property_use_validation_status=status)["facts"]
                    for key in ("property.use", "property.residential_use"):
                        self.assertEqual(facts[key]["status"], "unknown")
                        self.assertNotIn("value", facts[key])

    def test_conflicting_use_retains_conflict_and_no_decisive_value(self):
        for use in RESIDENTIAL_CLASSES:
            for status in ("conflict", "conflicting"):
                with self.subTest(use=use, status=status):
                    facts = self.bind(resolved_property_use=use,
                                      property_use_validation_status=status)["facts"]
                    for key in ("property.use", "property.residential_use"):
                        self.assertEqual(facts[key]["status"], "conflicting")
                        self.assertNotIn("value", facts[key])
                    rule, plan = synthetic_rule()
                    decision = evaluate_rule(rule, plan, facts)
                    self.assertEqual(decision["result"], "unknown")
                    self.assertTrue(decision["conflict_flag"])

    def test_actual_address_regressions_use_supplied_classification_only(self):
        expected = {"A0006": "subsidized_multifamily", "A0030": "mixed_use_multifamily",
                    "A0093": "specialized_residential", "A0336": "mixed_use_multifamily",
                    "A0398": "residential_tic"}
        for address_id, use in expected.items():
            with self.subTest(address_id=address_id):
                bound = load_address_facts(ROOT / "outputs/address_facts/address_enrichment.csv", address_id)
                self.assertEqual(bound["address"]["property_use_validation_status"], "verified")
                self.assertEqual(bound["facts"]["property.use"]["value"], use)
                self.assertTrue(bound["facts"]["property.residential_use"]["value"])
                self.assertEqual(bound["facts"]["property.residential_use"]["evidence"][0]["row_id"], address_id)
                if address_id == "A0398":
                    self.assertEqual(bound["facts"]["source.reported_units"]["status"], "conflicting")
                    self.assertNotIn("value", bound["facts"]["source.reported_units"])

    def test_unknown_exemption_and_event_remain_unknown_after_mapping(self):
        exclusion = {"id": "special-exemption", "stage": "coverage",
                     "when": predicate("case.special_exemption"), "reason": "Synthetic exception."}
        for use in RESIDENTIAL_CLASSES[1:]:
            with self.subTest(use=use):
                facts = self.bind(resolved_property_use=use)["facts"]
                rule, plan = synthetic_rule(trigger=predicate("transaction.synthetic_event"))
                self.assertEqual(evaluate_rule(rule, plan, facts)["result"], "applies")
                event = evaluate_rule(rule, plan, facts, mode="event")
                self.assertEqual(event["result"], "unknown")
                self.assertEqual(event["missing_facts"], ["transaction.synthetic_event"])
                rule, plan = synthetic_rule(exclusions=[exclusion])
                decision = evaluate_rule(rule, plan, facts)
                self.assertEqual(decision["result"], "unknown")
                self.assertEqual(decision["missing_facts"], ["case.special_exemption"])
                facts["case.special_exemption"] = {"status": "known", "value": True}
                self.assertEqual(evaluate_rule(rule, plan, facts)["decision_basis"], "exempt")
                facts["case.special_exemption"]["value"] = False
                self.assertEqual(evaluate_rule(rule, plan, facts)["result"], "applies")

    def test_verified_residential_branch_does_not_erase_and_or_distinction(self):
        facts = self.bind(resolved_property_use="mixed_use_multifamily")["facts"]
        clauses = [predicate("property.residential_use"), predicate("case.unestablished_qualification")]
        for operator, expected in (("any", "applies"), ("all", "unknown")):
            with self.subTest(operator=operator):
                rule, plan = synthetic_rule(coverage={operator: clauses})
                decision = evaluate_rule(rule, plan, facts)
                self.assertEqual(decision["result"], expected)
                self.assertEqual(decision["missing_facts"], [] if operator == "any" else
                                 ["case.unestablished_qualification"])


if __name__ == "__main__":
    unittest.main()
