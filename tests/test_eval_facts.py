"""Exercise address identity, provenance, and legally distinct fact scopes."""

import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from rent_rules.eval_facts import load_address_facts


class AddressFactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.csv_path = self.root / "addresses.csv"
        self.override_path = self.root / "overrides.json"
        self.row = {
            "address_id": "A0001", "street_address": "1 Fictional Street",
            "postal_city": "Postal Label", "legal_state": "CA", "legal_city": "Los Angeles",
            "incorporation_status": "incorporated", "jurisdiction_status": "verified",
            "jurisdiction_evidence_ref": "Fictional boundary record", "jurisdiction_notes": "",
            "jurisdiction_retrieved_at": "2026-10-04T00:49:15+00:00",
            "resolved_year_built": "1978", "year_built_validation_status": "verified",
            "year_built_source": "Fictional assessor", "year_built_explanation": "Sources agree.",
            "year_built_conflict_log": "", "resolved_units": "32", "resolved_units_min": "32",
            "resolved_units_max": "32", "unit_value_type": "exact", "unit_validation_status": "verified",
            "unit_source": "Fictional source count", "unit_validation_explanation": "Sources agree.",
            "resolved_property_use": "multifamily_residential", "property_use_validation_status": "verified",
            "property_use_source": "Fictional code dictionary", "property_use_explanation": "Residential classification.",
            "original_use_code": "0500", "original_use_description": "Five or more apartments",
            "original_source_dataset": "LA County eGIS parcels", "original_source_retrieved_at": "2026-10-01T22:50Z",
            "public_parcel_processed_at": "2026-10-04T02:11:41+00:00", "legal_query_date": "2026-10-01",
        }
        self.write_rows([self.row])

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self, rows):
        fields = sorted(set().union(*(set(row) for row in rows)))
        with self.csv_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def load(self, **options):
        return load_address_facts(self.csv_path, "A0001", **options)

    def override(self, facts, **wrapper_updates):
        wrapper = {"address_id": "A0001", "as_of": "2026-10-01", "facts": facts}
        wrapper.update(wrapper_updates)
        self.override_path.write_text(json.dumps(wrapper))
        return self.load(overrides_path=self.override_path)

    def observation(self, value=True, scope="owner_portfolio", status="known"):
        record = {"status": status, "subject_scope": scope, "reason": "Supported by a supplied record.",
                  "observed_at": "2026-10-01", "evidence": [{"path": "fixtures/owner_record.json",
                  "row_id": "A0001", "fields": ["owner_form"], "retrieved_at": "2026-10-03"}]}
        if status == "known":
            record["value"] = value
        return record

    def test_verified_mapping_preserves_evidence_and_original_row(self):
        result = self.load()
        self.assertEqual(result["address"], self.row)
        facts = result["facts"]
        self.assertEqual(facts["jurisdiction.state"]["value"], "CA")
        self.assertEqual(facts["jurisdiction.city"]["value"], "Los Angeles")
        self.assertTrue(facts["property.residential_use"]["value"])
        self.assertEqual(facts["property.use"]["value"], "multifamily_residential")
        evidence = facts["jurisdiction.city"]["evidence"][0]
        self.assertEqual(evidence["row_id"], "A0001")
        self.assertIn("jurisdiction_evidence_ref", evidence["fields"])
        self.assertEqual(result["input_sha256"][str(self.csv_path.resolve())], hashlib.sha256(self.csv_path.read_bytes()).hexdigest())
        self.assertTrue(facts["context.residential_rental_lookup"]["assumed_query_context"])

    def test_address_typo_is_not_silently_normalized(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            load_address_facts(self.csv_path, "A00001")

    def test_rule_scoped_fact_ids_accept_source_rule_identifier(self):
        key = "case.r-05d0c98608939365ddba.owner_property_count"
        result = self.override({key: self.observation(2)})
        self.assertEqual(result["facts"][key]["value"], 2)

    def test_duplicate_ids_even_unselected_cannot_be_ignored(self):
        other = dict(self.row, address_id="A0002")
        self.write_rows([self.row, other, other])
        with self.assertRaisesRegex(ValueError, "Duplicate address_id"):
            self.load()

    def test_partial_jurisdiction_does_not_promote_retained_city_or_state(self):
        self.row["jurisdiction_status"] = "partial"
        self.write_rows([self.row])
        facts = self.load()["facts"]
        for key in ("jurisdiction.city", "jurisdiction.state"):
            self.assertEqual(facts[key]["status"], "unknown")
            self.assertNotIn("value", facts[key])
        self.row["state_validation_status"] = "verified"
        self.write_rows([self.row])
        facts = self.load()["facts"]
        self.assertEqual(facts["jurisdiction.state"]["value"], "CA")
        self.assertEqual(facts["jurisdiction.city"]["status"], "unknown")

    def test_verification_without_jurisdiction_evidence_is_not_enough(self):
        self.row["jurisdiction_evidence_ref"] = ""
        self.write_rows([self.row])
        self.assertEqual(self.load()["facts"]["jurisdiction.city"]["status"], "unknown")

    def test_source_count_is_not_bound_to_building_or_owner_or_rentable_units(self):
        result = self.load()
        count = result["facts"]["source.reported_units"]
        self.assertEqual((count["value"], count["min"], count["max"]), (32, 32, 32))
        self.assertEqual(count["subject_scope"], "source_reported_count")
        for prohibited in ("property.building_unit_count", "property.rental_units", "owner.total_rental_unit_count"):
            self.assertNotIn(prohibited, result["facts"])
        self.assertTrue(any("Units1" in warning for warning in result["warnings"]))

    def test_open_and_closed_unit_ranges_keep_their_bounds(self):
        self.row.update(unit_value_type="range", resolved_units="5..", resolved_units_min="5", resolved_units_max="")
        self.write_rows([self.row])
        fact = self.load()["facts"]["source.reported_units"]
        self.assertEqual((fact["min"], fact["max"]), (5, None))
        self.assertNotIn("value", fact)
        self.row.update(resolved_units="7..30", resolved_units_min="7", resolved_units_max="30")
        self.write_rows([self.row])
        fact = self.load()["facts"]["source.reported_units"]
        self.assertEqual((fact["min"], fact["max"]), (7, 30))

    def test_conflicting_units_keep_conflict_without_selecting_a_value(self):
        self.row.update(unit_validation_status="conflict", resolved_units="unknown",
                        resolved_units_min="", resolved_units_max="", unit_value_type="unknown",
                        unit_validation_explanation="Original source says 6; public parcel says 4.")
        self.write_rows([self.row])
        fact = self.load()["facts"]["source.reported_units"]
        self.assertEqual(fact["status"], "conflicting")
        self.assertNotIn("value", fact)
        self.assertIn("says 6", fact["reason"])
        self.assertIn("unit_validation_explanation", fact["evidence"][0]["fields"])

    def test_invalid_verified_counts_fail_instead_of_becoming_usable(self):
        for changes in ({"resolved_units_max": "31"}, {"resolved_units": "32.5"},
                        {"unit_value_type": "range", "resolved_units_min": "7", "resolved_units_max": "4"}):
            with self.subTest(changes=changes):
                row = dict(self.row, **changes)
                self.write_rows([row])
                with self.assertRaises(ValueError):
                    self.load()

    def test_year_is_a_calendar_interval_and_never_a_co_date(self):
        facts = self.load()["facts"]
        self.assertEqual(facts["property.construction_date"]["min"], "1978-01-01")
        self.assertEqual(facts["property.construction_date"]["max"], "1978-12-31")
        self.assertNotIn("value", facts["property.construction_date"])
        self.assertNotIn("property.certificate_of_occupancy_date", facts)

    def test_retrieval_after_query_warns_without_becoming_an_event_date(self):
        result = self.load()
        self.assertEqual(result["facts"]["jurisdiction.city"]["status"], "known")
        self.assertIsNone(result["facts"]["jurisdiction.city"]["observed_at"])
        self.assertIsNone(result["facts"]["property.construction_date"]["observed_at"])
        self.assertTrue(any("jurisdiction_retrieved_at" in warning for warning in result["warnings"]))
        self.assertEqual(result["address"]["legal_query_date"], "2026-10-01")
        later_query = self.load(as_of="2026-10-10")
        self.assertTrue(any("differs from requested" in warning for warning in later_query["warnings"]))

    def test_other_normalized_use_is_not_automatically_nonresidential(self):
        self.row["resolved_property_use"] = "mixed_use"
        self.write_rows([self.row])
        facts = self.load()["facts"]
        self.assertEqual(facts["property.use"]["value"], "mixed_use")
        self.assertEqual(facts["property.residential_use"]["status"], "unknown")

    def test_override_adds_supported_false_and_records_both_input_hashes(self):
        result = self.override({"owner.qualifying_entity": self.observation(False)})
        self.assertFalse(result["facts"]["owner.qualifying_entity"]["value"])
        self.assertEqual(len(result["input_sha256"]), 2)
        self.assertTrue(any("retrieved after" in warning for warning in result["warnings"]))

    def test_override_wrapper_identity_and_query_date_are_strict(self):
        for changes in ({"address_id": "A0002"}, {"as_of": "2026-10-02"}, {"as_of": "2026-02-30"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.override({"owner.qualifying_entity": self.observation()}, **changes)

    def test_postquery_observation_is_not_promoted_to_historical_fact(self):
        record = self.observation()
        record["observed_at"] = "2026-10-04"
        with self.assertRaisesRegex(ValueError, "known historical fact"):
            self.override({"owner.qualifying_entity": record})

    def test_override_date_value_cannot_be_arbitrary_text_or_a_year(self):
        for value in ("1978", "2026-02-30", "recently"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.override({"property.certificate_of_occupancy_date": self.observation(value, "address_property")})
        result = self.override({"property.certificate_of_occupancy_date": self.observation("1978-05-02", "address_property")})
        self.assertEqual(result["facts"]["property.certificate_of_occupancy_date"]["value"], "1978-05-02")

    def test_override_requires_scope_provenance_identity_and_valid_timestamps(self):
        records = []
        for field in ("subject_scope", "reason"):
            record = self.observation()
            record[field] = ""
            records.append(record)
        record = self.observation()
        record["evidence"] = []
        records.append(record)
        record = self.observation()
        record["evidence"][0]["row_id"] = "A0002"
        records.append(record)
        record = self.observation()
        record["observed_at"] = "2026-13-01"
        records.append(record)
        record = self.observation()
        record["evidence"][0]["retrieved_at"] = "yesterday"
        records.append(record)
        record = self.observation()
        record["status"] = "verified"
        records.append(record)
        for record in records:
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.override({"owner.qualifying_entity": record})

    def test_known_fact_cannot_be_silently_changed_erased_or_rescoped(self):
        records = [self.observation("Berkeley", "address_jurisdiction"),
                   self.observation(scope="address_jurisdiction", status="unknown"),
                   self.observation("Los Angeles", "owner_portfolio")]
        for record in records:
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.override({"jurisdiction.city": record})

    def test_explicit_conflict_retains_previous_observation_and_its_evidence(self):
        conflict = self.observation(scope="address_jurisdiction", status="conflicting")
        conflict["reason"] = "A supplied boundary record disagrees with the CSV municipality."
        result = self.override({"jurisdiction.city": conflict})
        fact = result["facts"]["jurisdiction.city"]
        self.assertEqual(fact["status"], "conflicting")
        self.assertNotIn("value", fact)
        self.assertEqual(fact["overridden_observations"][0]["value"], "Los Angeles")
        self.assertEqual(len(fact["evidence"]), 2)

    def test_repeat_override_retains_previous_observation(self):
        result = self.override({"jurisdiction.city": self.observation("Los Angeles", "address_jurisdiction")})
        fact = result["facts"]["jurisdiction.city"]
        self.assertEqual(fact["value"], "Los Angeles")
        self.assertEqual(fact["overridden_observations"][0]["value"], "Los Angeles")

    def test_exact_count_repeat_can_omit_redundant_equal_bounds(self):
        result = self.override({"source.reported_units": self.observation(32, "source_reported_count")})
        fact = result["facts"]["source.reported_units"]
        self.assertEqual(fact["value"], 32)
        self.assertEqual(fact["overridden_observations"][0]["min"], 32)

    def test_boolean_and_numeric_one_are_not_the_same_observation(self):
        with self.assertRaisesRegex(ValueError, "explicit conflicting"):
            self.override({"property.residential_use": self.observation(1, "address_property")})

    def test_existing_conflict_cannot_be_silently_replaced_with_known(self):
        self.row["jurisdiction_status"] = "conflicting"
        self.write_rows([self.row])
        with self.assertRaisesRegex(ValueError, "silently resolved"):
            self.override({"jurisdiction.city": self.observation("Los Angeles", "address_jurisdiction")})

    def test_override_intervals_and_unknown_values_are_validated(self):
        invalid = [dict(self.observation(), status="unknown"),
                   dict(self.observation(), value=None),
                   dict(self.observation(), value=True, min=True, max=True)]
        for changes in ({"min": "2020-02-30", "max": "2020-12-31"}, {"min": 5, "max": 3}):
            record = self.observation()
            del record["value"]
            record.update(changes)
            invalid.append(record)
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.override({"property.some_fact": record})

    def test_nonfinite_and_duplicate_override_json_is_rejected(self):
        texts = ['{"address_id":"A0001","address_id":"A0002","as_of":"2026-10-01","facts":{}}',
                 '{"address_id":"A0001","as_of":"2026-10-01","facts":{"owner.fact":NaN}}']
        for text in texts:
            self.override_path.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load(overrides_path=self.override_path)


if __name__ == "__main__":
    unittest.main()
