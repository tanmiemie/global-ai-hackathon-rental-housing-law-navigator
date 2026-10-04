"""Reject unverified address inputs before binding facts or evaluating rules."""

import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rent_rules.eval_facts import load_address_facts
from rent_rules.evaluator import run_lookup


class AddressJurisdictionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addresses = self.root / "addresses.csv"
        self.row = {
            "address_id": "A0001",
            "street_address": "1 Fictional Street",
            "legal_city": "Los Angeles",
            "legal_state": "CA",
            "jurisdiction_status": "verified",
            "jurisdiction_evidence_ref": "Boundary evidence: parcel could not be resolved.\nPreserve this detail.",
            "state_validation_status": "verified",
            "resolved_year_built": "1978",
            "year_built_validation_status": "verified",
            "year_built_explanation": "Assessor supplied a construction year.",
            "resolved_units": "12",
            "resolved_units_min": "12",
            "resolved_units_max": "12",
            "unit_value_type": "exact",
            "unit_validation_status": "verified",
            "unit_validation_explanation": "Source reports twelve units.",
            "resolved_property_use": "multifamily_residential",
            "property_use_validation_status": "verified",
        }
        self.write_rows([self.row])
        self.rule = {
            "team_rule_id": "synthetic-rule",
            "jurisdiction": "CA",
            "citation": "Synthetic Code section 1",
            "source_doc_id": "SYNTHETIC",
            "source_url": "https://example.test/synthetic-code",
            "retrieved_at": "2026-10-01",
        }
        self.rules_path = self.root / "rules.json"
        self.rules_path.write_text(json.dumps({"rules": [self.rule]}), encoding="utf-8")
        self.plans_path = self.root / "plans.json"
        self.plans_path.write_text(json.dumps({"plans": []}), encoding="utf-8")
        self.dependencies_path = self.root / "dependencies.json"
        self.dependencies_path.write_text("{}", encoding="utf-8")
        self.registry_path = self.root / "registry.json"
        self.registry_path.write_text(json.dumps({
            "schema_version": "1.0",
            "internal_to_submission": {"synthetic-rule": "r-0001"},
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self, rows):
        fields = sorted(set().union(*(set(row) for row in rows)))
        with self.addresses.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def load(self, **kwargs):
        return load_address_facts(
            self.addresses, "A0001", skip_unverified_jurisdiction=True, **kwargs)

    def run_lookup(self, output, address_ids=None, overrides_path=None):
        with patch("rent_rules.evaluator.validate_plan_bundle", return_value=({}, {})), \
                patch("rent_rules.evaluator.validate_fact_scopes"), \
                patch("rent_rules.evaluator.load_field_dependencies", return_value={}):
            return run_lookup(
                self.rules_path, self.plans_path, self.addresses,
                address_ids or ["A0001"], output,
                overrides_path=overrides_path, source_root=self.root,
                rule_id_map_path=self.registry_path,
                field_dependencies_path=self.dependencies_path)

    def test_only_exact_verified_status_passes_the_gate(self):
        for status in ("partial", "unknown", "conflict", "conflicting", "", "Wified", "Verified", "verified "):
            with self.subTest(status=status):
                self.row["jurisdiction_status"] = status
                self.write_rows([self.row])
                with patch("rent_rules.eval_facts._bind_csv", side_effect=AssertionError("must not bind")):
                    bound = self.load()
                self.assertEqual(bound["facts"], {})
                self.assertEqual(bound["address"], self.row)
                self.assertEqual(bound["input_sha256"][str(self.addresses.resolve())],
                                 hashlib.sha256(self.addresses.read_bytes()).hexdigest())

    def test_missing_status_is_skipped_instead_of_assumed_verified(self):
        del self.row["jurisdiction_status"]
        self.write_rows([self.row])
        with patch("rent_rules.eval_facts._bind_csv", side_effect=AssertionError("must not bind")):
            self.assertEqual(self.load()["facts"], {})

    def test_verified_address_retains_normal_typed_facts(self):
        bound = self.load()
        self.assertEqual(bound["facts"]["jurisdiction.state"]["value"], "CA")
        self.assertEqual(bound["facts"]["property.construction_date"]["min"], "1978-01-01")
        self.assertEqual(bound["facts"]["source.reported_units"]["value"], 12)

    def test_partial_address_skips_malformed_year_units_and_timestamps(self):
        self.row.update({
            "jurisdiction_status": "partial",
            "resolved_year_built": "not a year",
            "resolved_units": "not a count",
            "jurisdiction_retrieved_at": "not a timestamp",
            "original_source_retrieved_at": "not a timestamp",
            "legal_query_date": "not a date",
        })
        self.write_rows([self.row])
        with patch("rent_rules.eval_facts._bind_csv", side_effect=AssertionError("must not bind")):
            self.assertEqual(self.load()["facts"], {})

    def test_verified_address_still_rejects_malformed_verified_year(self):
        self.row["resolved_year_built"] = "not a year"
        self.write_rows([self.row])
        with self.assertRaisesRegex(ValueError, "four-digit year"):
            self.load()

    def test_overrides_and_component_verification_cannot_bypass_csv_gate(self):
        self.row["jurisdiction_status"] = "partial"
        self.write_rows([self.row])
        overrides = self.root / "overrides.json"
        overrides.write_text(json.dumps({
            "address_id": "A0001", "as_of": "2026-10-01",
            "facts": {"jurisdiction.city": {"status": "known", "value": "Los Angeles"}},
        }), encoding="utf-8")
        with patch("rent_rules.eval_facts._apply_overrides", side_effect=AssertionError("must not apply overrides")):
            bound = self.load(overrides_path=overrides)
        self.assertEqual(bound["facts"], {})
        self.assertEqual(bound["input_sha256"][str(overrides.resolve())],
                         hashlib.sha256(overrides.read_bytes()).hexdigest())

    def test_skip_returns_empty_lookup_verbatim_reason_and_zero_rule_calls(self):
        self.row["jurisdiction_status"] = "partial"
        self.write_rows([self.row])
        output = self.root / "skipped"
        with patch("rent_rules.evaluator.evaluate_rule", side_effect=AssertionError("must not evaluate rules")) as evaluate:
            report = self.run_lookup(output)
        evaluate.assert_not_called()
        payload = json.loads((output / "lookups.json").read_text(encoding="utf-8"))
        self.assertEqual(payload, {"as_of": "2026-10-01", "lookups": {"A0001": []}})
        diagnostics = json.loads((output / "address_diagnostics.json").read_text(encoding="utf-8"))
        diagnostic = diagnostics["addresses"]["A0001"]
        self.assertEqual(diagnostic["status"], "skipped")
        self.assertEqual(diagnostic["jurisdiction_status"], "partial")
        self.assertEqual(diagnostic["reason"], self.row["jurisdiction_evidence_ref"])
        self.assertEqual(diagnostic["reason_code"], "jurisdiction_not_verified")
        self.assertEqual(diagnostic["rules_evaluated"], 0)
        summary = report["addresses"]["A0001"]
        self.assertEqual(summary["evaluation_status"], "skipped")
        self.assertEqual(summary["evaluated_rule_count"], 0)
        self.assertFalse(summary["coverage_complete"])
        audit = json.loads((output / "evaluation_audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["decisions"]["A0001"], [])
        self.assertEqual(audit["facts"]["A0001"]["facts"], {})

    def test_mixed_batch_evaluates_only_verified_address(self):
        partial = dict(self.row, address_id="A0002", jurisdiction_status="partial")
        self.write_rows([self.row, partial])
        record = {
            "team_rule_id": "synthetic-rule", "result": "applies",
            "explanation": "The synthetic condition is established.",
            "conflict_flag": False, "decision_basis": "covered", "compiled": True,
        }
        output = self.root / "mixed"
        with patch("rent_rules.evaluator.evaluate_rule", return_value=record) as evaluate:
            report = self.run_lookup(output, address_ids=["A0001", "A0002"])
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(evaluate.call_args.args[2]["jurisdiction.state"]["value"], "CA")
        payload = json.loads((output / "lookups.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload["lookups"]["A0001"]), 1)
        self.assertIn("Citation: Synthetic Code section 1", payload["lookups"]["A0001"][0]["explanation"])
        self.assertEqual(payload["lookups"]["A0002"], [])
        self.assertEqual(report["addresses"]["A0001"]["evaluation_status"], "evaluated")
        self.assertEqual(report["addresses"]["A0001"]["evaluated_rule_count"], 1)
        self.assertEqual(report["addresses"]["A0002"]["evaluated_rule_count"], 0)


if __name__ == "__main__":
    unittest.main()
