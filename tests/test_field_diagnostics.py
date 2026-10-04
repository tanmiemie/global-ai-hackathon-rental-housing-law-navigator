"""Address source issues annotate decisive inputs without changing decisions."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from rent_rules.evaluator import evaluate_rule
from rent_rules.field_diagnostics import load_field_dependencies, rule_field_diagnostics


RULE_ID = "synthetic-property-rule"
YEAR = "case.synthetic.construction_completion_date"
UNITS = "case.synthetic.premises_rental_units"
OTHER_UNITS = "case.synthetic.building_dwelling_units"
CERTIFICATE = "case.synthetic.certificate_of_occupancy_date"
PORTFOLIO = "owner.portfolio_dwelling_units_offered_for_rent"
RULES_HASH = "a" * 64
PLANS_HASH = "b" * 64


def fixture():
    definitions = [
        {"key": YEAR, "description": "Actual construction completion date of the building.",
         "subject_scope": "subject_building", "data_type": "date"},
        {"key": UNITS, "description": "Rented dwelling units in the premises at the relevant event.",
         "subject_scope": "subject_premises_at_relevant_event", "data_type": "number"},
        {"key": OTHER_UNITS, "description": "Dwelling units in the subject building.",
         "subject_scope": "subject_building", "data_type": "number"},
    ]
    coverage = {"all": [{"fact": YEAR, "op": "lt", "value": "1980-06-01"},
                         {"fact": UNITS, "op": "gt", "value": 2},
                         {"fact": OTHER_UNITS, "op": "gt", "value": 2}]}
    branch = {"branch_id": "main", "label": "Main protection", "coverage": coverage,
              "trigger": {"fact": "transaction.notice", "op": "eq", "value": True},
              "exclusions": [], "requirements": [], "modifiers": [], "source_gates": [],
              "duty": "The protection applies when its conditions hold.",
              "retained_conditions": [], "evidence_refs": []}
    rule = {"team_rule_id": RULE_ID, "title": "Synthetic conditional protection",
            "jurisdiction": "CA", "category": "test", "status": "in_force",
            "conflict_flag": False, "applicability": {"rule_kind": "transaction_rule"}}
    plan = {"team_rule_id": RULE_ID, "branches": [branch], "fact_definitions": definitions}
    dependencies = {item["key"]: {"input_group": "year_built" if item["key"] == YEAR else "units",
                                  **{key: value for key, value in item.items() if key != "key"},
                                  "rule_ids": [RULE_ID]} for item in definitions}
    facts = {"jurisdiction.state": {"status": "known", "value": "CA"}}
    row = {"year_built_validation_status": "unknown",
           "year_built_explanation": "  The assessor did not publish the construction year.  ",
           "unit_validation_status": "conflicting",
           "unit_validation_explanation": "Two supplied property records disagree on units."}
    return rule, plan, facts, row, dependencies


class FieldDiagnosticTests(unittest.TestCase):
    def test_exact_csv_reasons_and_scopes_preserved_without_binding_values(self):
        rule, plan, facts, row, dependencies = fixture()
        record = evaluate_rule(rule, plan, facts)
        before = deepcopy((record, plan, facts, row, dependencies))
        notes = rule_field_diagnostics(record, plan, facts, row, dependencies)
        self.assertEqual([n["input_group"] for n in notes], ["year_built", "units"])
        self.assertEqual(notes[0]["source_explanation"], row["year_built_explanation"])
        self.assertIn(row["year_built_explanation"], notes[0]["explanation"])
        self.assertEqual(notes[1]["source_explanation"], row["unit_validation_explanation"])
        self.assertEqual({n["key"] for n in notes[1]["affected_facts"]}, {UNITS, OTHER_UNITS})
        self.assertIn("relevant observation time", notes[1]["explanation"])
        self.assertIn("no date or certificate-of-occupancy date is inferred", notes[0]["explanation"])
        self.assertEqual((record, plan, facts, row, dependencies), before)

    def test_verified_inputs_add_no_old_source_warning(self):
        rule, plan, facts, row, dependencies = fixture()
        row["year_built_validation_status"] = row["unit_validation_status"] = "verified"
        record = evaluate_rule(rule, plan, facts)
        self.assertEqual(rule_field_diagnostics(record, plan, facts, row, dependencies), [])

    def test_true_or_alternative_has_no_decisive_missing_warning(self):
        rule, plan, facts, row, dependencies = fixture()
        plan["branches"][0]["coverage"] = {"any": [plan["branches"][0]["coverage"],
                                                         {"fact": "alternative", "op": "eq", "value": True}]}
        facts["alternative"] = {"status": "known", "value": True}
        record = evaluate_rule(rule, plan, facts)
        self.assertEqual(record["result"], "applies")
        self.assertEqual(record["missing_facts"], [])
        self.assertEqual(rule_field_diagnostics(record, plan, facts, row, dependencies), [])

    def test_event_inputs_are_annotated_only_in_event_mode(self):
        rule, plan, facts, row, dependencies = fixture()
        plan["branches"][0]["trigger"] = plan["branches"][0]["coverage"]
        plan["branches"][0]["coverage"] = {"fact": "covered", "op": "eq", "value": True}
        facts["covered"] = {"status": "known", "value": True}
        coverage = evaluate_rule(rule, plan, facts, mode="coverage")
        event = evaluate_rule(rule, plan, facts, mode="event")
        self.assertEqual(coverage["result"], "applies")
        self.assertEqual(rule_field_diagnostics(coverage, plan, facts, row, dependencies), [])
        self.assertEqual(event["result"], "unknown")
        self.assertEqual(len(rule_field_diagnostics(event, plan, facts, row, dependencies)), 2)

    def test_case_observations_supersede_csv_background_issue(self):
        rule, plan, facts, row, dependencies = fixture()
        facts[YEAR] = {"status": "known", "value": "1970-01-01"}
        facts[UNITS] = {"status": "known", "value": 6}
        facts[OTHER_UNITS] = {"status": "known", "value": 8}
        record = evaluate_rule(rule, plan, facts)
        self.assertEqual(record["result"], "applies")
        self.assertEqual(rule_field_diagnostics(record, plan, facts, row, dependencies), [])

    def test_certificate_portfolio_and_similar_new_fact_names_are_not_linked(self):
        rule, plan, facts, row, dependencies = fixture()
        record = {"team_rule_id": RULE_ID, "result": "unknown", "missing_facts": [
            CERTIFICATE, PORTFOLIO, "case.other.premises_rental_units", "project.converted_units"]}
        self.assertEqual(rule_field_diagnostics(record, plan, facts, row, dependencies), [])

    def test_irrelevant_or_omitted_rules_and_missing_plans_produce_no_notes(self):
        rule, plan, facts, row, dependencies = fixture()
        record = {"team_rule_id": RULE_ID, "result": None, "missing_facts": [YEAR]}
        self.assertEqual(rule_field_diagnostics(record, plan, facts, row, dependencies), [])
        record["result"] = "unknown"
        self.assertEqual(rule_field_diagnostics(record, None, facts, row, dependencies), [])
        self.assertEqual(rule_field_diagnostics(record, plan, facts, row, {}), [])

    def test_missing_source_explanation_is_explicit_not_fabricated(self):
        rule, plan, facts, row, dependencies = fixture()
        row.pop("year_built_explanation")
        record = evaluate_rule(rule, plan, facts)
        note = rule_field_diagnostics(record, plan, facts, row, dependencies)[0]
        self.assertIsNone(note["source_explanation"])
        self.assertIn("No explanation was supplied", note["explanation"])


class FieldDependencyManifestTests(unittest.TestCase):
    def setUp(self):
        _, self.plan, _, _, dependencies = fixture()
        self.manifest = {"schema_version": "1.0", "rules_sha256": RULES_HASH,
                         "plans_sha256": PLANS_HASH, "dependencies": dependencies}
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "dependencies.json"

    def load(self, rules_hash=RULES_HASH, plans_hash=PLANS_HASH):
        self.path.write_text(json.dumps(self.manifest), encoding="utf-8")
        return load_field_dependencies(self.path, rules_hash, plans_hash, {RULE_ID: self.plan})

    def test_snapshot_bound_manifest_loads(self):
        self.assertEqual(self.load(), self.manifest["dependencies"])

    def test_reject_stale_rules_or_plans(self):
        with self.assertRaisesRegex(ValueError, "another rule snapshot"):
            self.load(rules_hash="c" * 64)
        with self.assertRaisesRegex(ValueError, "another plan snapshot"):
            self.load(plans_hash="c" * 64)

    def test_reject_changed_scope_or_description(self):
        for field in ("subject_scope", "description", "data_type"):
            original = self.manifest["dependencies"][UNITS][field]
            self.manifest["dependencies"][UNITS][field] = "owner portfolio"
            with self.assertRaisesRegex(ValueError, "definition differs"):
                self.load()
            self.manifest["dependencies"][UNITS][field] = original

    def test_reject_unused_fact_and_wrong_rule_membership(self):
        self.manifest["dependencies"][UNITS]["rule_ids"] = ["some-other-rule"]
        with self.assertRaisesRegex(ValueError, "membership differs"):
            self.load()
        self.manifest["dependencies"][UNITS]["rule_ids"] = [RULE_ID]
        self.manifest["dependencies"]["case.other.premises_rental_units"] = deepcopy(
            self.manifest["dependencies"][UNITS])
        with self.assertRaisesRegex(ValueError, "not used"):
            self.load()

    def test_reviewed_production_manifest_distinguishes_dates_and_count_scopes(self):
        root = Path(__file__).resolve().parents[1]
        plan_path = root / "data/evaluation/rule_plans.json"
        bundle = json.loads(plan_path.read_text())
        dependencies = load_field_dependencies(
            root / "data/evaluation/address_field_dependencies.json", bundle["rules_sha256"],
            hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            {p["team_rule_id"]: p for p in bundle["plans"]})
        self.assertIn("property.construction_date", dependencies)
        self.assertIn("property.building_rental_unit_count", dependencies)
        self.assertNotIn("property.certificate_of_occupancy_date", dependencies)
        self.assertNotIn(PORTFOLIO, dependencies)
        for key in dependencies:
            self.assertNotIn("certificate_of_occupancy", key)
            self.assertNotIn("converted_units", key)
            self.assertNotIn("percent", key)
            self.assertNotIn("bedroom", key)


if __name__ == "__main__":
    unittest.main()
