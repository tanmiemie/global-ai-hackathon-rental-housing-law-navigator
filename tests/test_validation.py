"""Synthetic tests for structural validation and faithful source matching."""

import copy
import unittest
from types import SimpleNamespace

from rent_rules.validation import locate_quote, validate_export, validate_rule


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["team_rule_id", "status", "source_url", "quoted_span"],
    "properties": {
        "team_rule_id": {"type": "string"},
        "status": {"enum": ["in_force", "pending", "failed", "not_yet_effective"]},
        "source_doc_id": {"type": ["string", "null"]},
        "source_url": {"type": "string"},
        "quoted_span": {"type": "string", "minLength": 20},
        "effective_date": {"type": ["string", "null"], "pattern": r"^\d{4}(-\d{2}(-\d{2})?)?$"},
        "overrides": {"type": "array", "items": {"type": "string"}},
    },
}
QUOTE = "The provider shall send a written receipt."


class QuoteLocationTests(unittest.TestCase):
    def test_exact_offsets_and_lines_include_original_paragraph(self):
        text = "Heading\n" + QUOTE + "\nAnother paragraph.\n"
        result = locate_quote(text, QUOTE)
        self.assertEqual(result["quote"], QUOTE)
        self.assertEqual((result["start"], result["end"]), (8, 8 + len(QUOTE)))
        self.assertEqual((result["start_line"], result["end_line"]), (2, 2))
        self.assertEqual(result["match_type"], "exact")

    def test_whitespace_matching_restores_source_span(self):
        original = "The provider\r\n\r\nshall\t send a written\u00a0receipt."
        text = "Heading\r\n" + original + "\r\nFooter"
        result = locate_quote(text, "  " + QUOTE + "  ")
        self.assertEqual(result["quote"], original)
        self.assertEqual(text[result["start"]:result["end"]], original)
        self.assertEqual((result["start_line"], result["end_line"]), (2, 4))
        self.assertEqual(result["match_type"], "whitespace")

    def test_exact_match_has_priority_over_earlier_normalized_match(self):
        text = QUOTE.replace(" ", "\n") + "\n" + QUOTE
        result = locate_quote(text, QUOTE)
        self.assertEqual(result["start"], text.rindex(QUOTE))
        self.assertEqual(result["match_type"], "exact")

    def test_quote_ending_with_crlf_does_not_claim_the_following_line(self):
        quote = QUOTE + "\r\n"
        result = locate_quote("Heading\r\n" + quote + "Footer", quote)
        self.assertEqual((result["start_line"], result["end_line"]), (2, 2))

    def test_matching_does_not_rewrite_words_or_punctuation(self):
        for quote in (
            QUOTE.replace("shall", "may"),
            QUOTE.lower(),
            QUOTE.replace("written ", ""),
            QUOTE.replace("provider shall", "providershall"),
            QUOTE.replace(".", "!"),
        ):
            with self.subTest(quote=quote):
                self.assertIsNone(locate_quote(QUOTE, quote))
        self.assertIsNone(locate_quote("The pro-\nvider shall send a receipt.", "The provider shall send a receipt."))

    def test_empty_invalid_and_whitespace_quotes_do_not_match(self):
        for quote in ("", " \n\t", None, 5):
            self.assertIsNone(locate_quote(QUOTE, quote))
        self.assertIsNone(locate_quote(None, QUOTE))


class RuleValidationTests(unittest.TestCase):
    def setUp(self):
        self.sources = {"DOC-1": SimpleNamespace(
            text="Source heading\n" + QUOTE + "\n",
            url="https://example.test/document",
            retrieved_at="2026-10-01T22:35Z",
        )}
        self.rule = {
            "team_rule_id": "rule-1",
            "status": "in_force",
            "source_doc_id": "DOC-1",
            "source_url": "https://example.test/document",
            "quoted_span": QUOTE,
            "retrieved_at": "2026-10-01T22:35Z",
            "as_of": "2026-10-01",
            "effective_date": None,
            "overrides": [],
        }

    def codes(self, rule):
        return {issue["code"] for issue in validate_rule(rule, SCHEMA, self.sources)}

    def test_valid_rule_and_export_are_not_mutated(self):
        before = copy.deepcopy(self.rule)
        result = validate_export({"rules": [self.rule]}, SCHEMA, self.sources)
        self.assertEqual(result, {"valid": True, "error_count": 0, "errors": [], "rule_count": 1})
        self.assertEqual(self.rule, before)

    def test_schema_rejects_unknown_enum_and_missing_required_field(self):
        self.rule["status"] = "unknown"
        del self.rule["team_rule_id"]
        self.assertIn("schema", self.codes(self.rule))

    def test_missing_citation_declarations_are_withheld(self):
        for citation in (
            "No legal citation supplied for the reported fictional city ban.",
            "No citation provided.", "No official citation is available in the source.",
            "Citation unknown.", "Official citation: unavailable", "Citation not identified in this excerpt.",
            "unknown", "Unavailable", "None", "N/A", "Not provided",
        ):
            with self.subTest(citation=citation):
                self.assertEqual(self.codes(dict(self.rule, citation=citation)), {"missing_citation"})

    def test_identifiable_citation_or_title_with_a_missing_section_note_is_preserved(self):
        for citation in (
            "Synthetic Municipal Code section 12.34",
            "Fictional Application Receipt Notice",
            "Fictional Application Receipt Notice; no statutory citation supplied.",
            "Fictional Application Receipt Notice (statutory section unavailable)",
            "Fictional Housing Department: Receipt Policy; no citation supplied in this excerpt.",
            "No Citation Required Policy, section 2",
        ):
            with self.subTest(citation=citation):
                self.assertEqual(self.codes(dict(self.rule, citation=citation)), set())

    def test_missing_unknown_or_unhashable_source_id_does_not_crash(self):
        for source_id in (None, "DOC-MISSING", [], {}):
            self.rule["source_doc_id"] = source_id
            self.assertIn("unknown_source", self.codes(self.rule))

    def test_source_url_must_correspond_to_document(self):
        self.rule["source_url"] = "https://example.test/another-document"
        self.assertIn("source_url_mismatch", self.codes(self.rule))

    def test_quote_needs_real_source_words_and_sufficient_evidence(self):
        self.rule["quoted_span"] = QUOTE.replace("shall", "may")
        self.assertIn("quote_not_found", self.codes(self.rule))
        self.rule["quoted_span"] = "receipt"
        self.assertIn("quote_too_short", self.codes(self.rule))
        self.rule["quoted_span"] = "receipt" + (" " * 30)
        self.assertIn("quote_too_short", self.codes(self.rule))
        self.sources["DOC-1"].text = ""
        self.rule["quoted_span"] = QUOTE
        self.assertIn("quote_not_found", self.codes(self.rule))

    def test_only_whitespace_changes_are_accepted_for_matching(self):
        self.rule["quoted_span"] = QUOTE.replace(" ", "\n")
        self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])

    def test_valid_effective_date_precision_and_leap_day(self):
        for value in (None, "2024", "2024-02", "2024-02-29"):
            self.rule["effective_date"] = value
            self.assertNotIn("invalid_effective_date", self.codes(self.rule))

    def test_future_in_force_is_invalid_even_with_exact_date_evidence(self):
        date_quote = "This synthetic provision takes effect on July 1, 2027."
        self.sources["DOC-1"].text += date_quote + "\n"
        self.rule.update(effective_date="2027-07-01", evidence=[{
            "field": "effective_date", "source_doc_id": "DOC-1",
            "source_url": self.sources["DOC-1"].url, "quote": date_quote,
        }])
        before = copy.deepcopy(self.rule)
        self.assertEqual(self.codes(self.rule), {"temporal_status_mismatch"})
        report = validate_export({"rules": [self.rule]}, SCHEMA, self.sources)
        self.assertFalse(report["valid"])
        self.assertEqual(report["error_count"], 1)
        self.assertEqual(self.rule, before)

    def test_operative_status_date_comparison_includes_the_effective_day(self):
        cases = (
            ("in_force", "2026-09-30", True),
            ("in_force", "2026-10-01", True),
            ("in_force", "2026-10-02", False),
            ("not_yet_effective", "2026-09-30", False),
            ("not_yet_effective", "2026-10-01", False),
            ("not_yet_effective", "2026-10-02", True),
        )
        for status, effective_date, valid in cases:
            with self.subTest(status=status, effective_date=effective_date):
                self.rule.update(status=status, effective_date=effective_date)
                expected = set() if valid else {"temporal_status_mismatch"}
                self.assertEqual(self.codes(self.rule), expected)

    def test_pending_and_failed_statuses_are_not_inferred_from_effective_dates(self):
        for status in ("pending", "failed"):
            for effective_date in ("2026-09-30", "2026-10-01", "2026-10-02"):
                with self.subTest(status=status, effective_date=effective_date):
                    self.rule.update(status=status, effective_date=effective_date)
                    self.assertEqual(self.codes(self.rule), set())

    def test_temporal_status_checks_require_two_complete_valid_dates(self):
        for status in ("in_force", "not_yet_effective"):
            for effective_date in (None, "2027", "2027-07", "2025", "2025-07",
                                   "2026-02-30", [], "tomorrow"):
                with self.subTest(status=status, effective_date=effective_date):
                    self.rule.update(status=status, effective_date=effective_date, as_of="2026-10-01")
                    self.assertNotIn("temporal_status_mismatch", self.codes(self.rule))
            for as_of in (None, "2026", "2026-10", "2026-02-30", [], "tomorrow"):
                with self.subTest(status=status, as_of=as_of):
                    self.rule.update(status=status, effective_date="2027-07-01", as_of=as_of)
                    self.assertNotIn("temporal_status_mismatch", self.codes(self.rule))
            self.rule.update(status=status, as_of="2026-10-01")
            self.rule.pop("effective_date")
            self.assertEqual(self.codes(self.rule), set())

    def test_date_regex_is_not_enough(self):
        for value in ("0000", "2026-13", "2026-02-29", "2026-04-31", "\u0662\u0660\u0662\u0666-10-01"):
            self.rule["effective_date"] = value
            self.assertIn("invalid_effective_date", self.codes(self.rule))
        for field, code in (("as_of", "invalid_as_of"), ("retrieved_at", "invalid_retrieved_at")):
            for value in (None, "2026-02-30", "2026-10", "tomorrow"):
                self.rule[field] = value
                self.assertIn(code, self.codes(self.rule))
        for value in ("2026-10-01T25:35Z", "2026-10-01T22:35:00+01:60"):
            self.rule["retrieved_at"] = value
            self.assertIn("invalid_retrieved_at", self.codes(self.rule))

    def test_retrieval_accepts_real_iso_date_and_timestamp(self):
        for value in ("2026-10-01", "2026-10-01T22:35Z", "2026-10-01T22:35:00+00:00"):
            self.rule["retrieved_at"] = value
            self.assertNotIn("invalid_retrieved_at", self.codes(self.rule))

    def test_retrieval_must_match_capture_with_timezone_normalization(self):
        for value in ("2026-10-01", "2026-10-01T22:35:00Z", "2026-10-01T15:35-07:00",
                      "2026-10-02T00:35:00+02:00"):
            with self.subTest(value=value):
                self.rule["retrieved_at"] = value
                self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])
        for value in ("2026-10-02", "2026-10-01T22:36Z", "2026-10-01T22:35:00-07:00",
                      "2026-10-01T22:35:00"):
            with self.subTest(value=value):
                self.rule["retrieved_at"] = value
                self.assertIn("retrieved_at_mismatch", self.codes(self.rule))

    def test_missing_or_invalid_source_capture_time_cannot_be_validated(self):
        for source_value in (None, "2026-02-30", "yesterday"):
            self.sources["DOC-1"].retrieved_at = source_value
            self.assertIn("retrieved_at_mismatch", self.codes(self.rule))

    def test_multi_source_evidence_can_reference_a_second_occurrence(self):
        original = "The provider\nshall send a written\treceipt."
        text = "SOURCE: https://example.test/second\r\nRETRIEVED: 2026-10-01T23:00Z\r\n\r\n" + original + "\n" + original
        self.sources["DOC-2"] = SimpleNamespace(text=text, url="https://example.test/second",
                                              retrieved_at="2026-10-01T23:00Z")
        start = text.rindex(original)
        self.rule.update(source_doc_ids=["DOC-1", "DOC-2"], evidence=[{
            "field": "requirement", "source_doc_id": "DOC-2", "source_url": self.sources["DOC-2"].url,
            "quote": QUOTE, "start": start, "end": start + len(original),
        }])
        before = copy.deepcopy(self.rule)
        self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])
        self.assertEqual(self.rule, before)

    def test_each_evidence_document_and_url_are_checked(self):
        evidence = {"source_doc_id": "DOC-1", "source_url": self.sources["DOC-1"].url, "quote": QUOTE}
        self.rule["evidence"] = [copy.deepcopy(evidence), copy.deepcopy(evidence)]
        self.rule["evidence"][1]["source_url"] = "https://example.test/wrong"
        self.assertIn("evidence_source_url_mismatch", self.codes(self.rule))
        for value in ("DOC-ABSENT", None, [], {}):
            self.rule["evidence"][1] = dict(evidence, source_doc_id=value)
            self.assertIn("unknown_evidence_source", self.codes(self.rule))

    def test_evidence_rejects_changed_words_and_allows_only_whitespace_changes(self):
        evidence = {"source_doc_id": "DOC-1", "source_url": self.sources["DOC-1"].url, "quote": QUOTE}
        self.rule["evidence"] = [evidence]
        evidence["quote"] = QUOTE.replace(" ", "\n")
        self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])
        evidence["quote"] = QUOTE.replace("shall", "may")
        self.assertIn("evidence_quote_not_found", self.codes(self.rule))
        for quote in ("", " \n\t", None, []):
            evidence["quote"] = quote
            self.assertIn("invalid_evidence_quote", self.codes(self.rule))

    def test_evidence_offsets_must_delimit_the_actual_source_passage(self):
        text = self.sources["DOC-1"].text
        start = text.index(QUOTE)
        evidence = {"source_doc_id": "DOC-1", "source_url": self.sources["DOC-1"].url,
                    "quote": QUOTE, "start": start, "end": start + len(QUOTE)}
        self.rule["evidence"] = [evidence]
        self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])
        for wrong_start, wrong_end in ((-1, 5), (5, 5), (10, 2), (0, len(text) + 1),
                                      (False, 5), (0, True), (None, 5), ("0", 5), (0.0, 5)):
            self.rule["evidence"] = [dict(evidence, start=wrong_start, end=wrong_end)]
            self.assertIn("invalid_evidence_offsets", self.codes(self.rule))
        self.rule["evidence"] = [dict(evidence, start=start + 1)]
        self.assertIn("evidence_offset_mismatch", self.codes(self.rule))
        self.rule["evidence"] = [dict(evidence)]
        del self.rule["evidence"][0]["end"]
        self.assertIn("invalid_evidence_offsets", self.codes(self.rule))

    def test_capture_headers_are_not_evidence_with_or_without_offsets(self):
        for newline in ("\n", "\r\n", "\r"):
            text = "\ufeffSOURCE: https://example.test/document" + newline + "RETRIEVED: 2026-10-01T22:35Z" + newline * 2 + QUOTE
            self.sources["DOC-1"].text = text
            header_quote = "RETRIEVED: 2026-10-01T22:35Z"
            self.rule["quoted_span"] = header_quote
            self.assertIn("header_quote", self.codes(self.rule))
            self.rule["quoted_span"] = QUOTE
            evidence = {"source_doc_id": "DOC-1", "source_url": self.sources["DOC-1"].url,
                        "quote": header_quote}
            self.rule["evidence"] = [evidence]
            self.assertIn("header_quote", self.codes(self.rule))
            evidence.update(start=text.index(header_quote), end=text.index(header_quote) + len(header_quote))
            self.assertIn("header_quote", self.codes(self.rule))
            evidence.update(quote=QUOTE, start=text.index(QUOTE), end=text.index(QUOTE) + len(QUOTE))
            self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])

    def test_invalid_evidence_structure_reports_errors_without_crashing(self):
        for value in (None, {}, "invalid"):
            self.rule["evidence"] = value
            self.assertIn("invalid_evidence", self.codes(self.rule))
        for item in (None, [], "invalid"):
            self.rule["evidence"] = [item]
            self.assertIn("invalid_evidence", self.codes(self.rule))

    def test_source_id_list_is_real_and_covers_primary_and_evidence_sources(self):
        for value in (None, "DOC-1", [None], [[]]):
            self.rule["source_doc_ids"] = value
            self.assertIn("invalid_source_doc_ids", self.codes(self.rule))
        self.rule["source_doc_ids"] = ["DOC-ABSENT"]
        self.assertIn("unknown_source", self.codes(self.rule))
        self.assertIn("primary_source_missing", self.codes(self.rule))
        self.rule["source_doc_ids"] = ["DOC-1"]
        self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])
        self.sources["DOC-2"] = copy.deepcopy(self.sources["DOC-1"])
        self.rule["evidence"] = [{"source_doc_id": "DOC-2", "source_url": self.sources["DOC-2"].url,
                                  "quote": QUOTE}]
        self.assertIn("evidence_source_unlisted", self.codes(self.rule))
        self.rule["source_doc_ids"].append("DOC-2")
        self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])

    def test_jurisdiction_must_be_in_manifest_scope_not_just_the_current_source(self):
        self.sources["DOC-1"].jurisdictions = "Example City, CA"
        self.sources["DOC-2"] = SimpleNamespace(jurisdictions="CA; NJ")
        for jurisdiction in ("Example City, CA", "CA", "NJ"):
            self.rule["jurisdiction"] = jurisdiction
            self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])
        for jurisdiction in ("Unlisted City, CA", "Example City", [], None):
            self.rule["jurisdiction"] = jurisdiction
            self.assertIn("out_of_scope_jurisdiction", self.codes(self.rule))

    def test_placeholders_are_found_inside_nested_fields(self):
        self.rule["coverage_conditions"] = {"unit_count": "<insert supported condition>"}
        self.assertIn("placeholder", self.codes(self.rule))
        self.rule["coverage_conditions"] = {"unit_count": "unknown: the source does not specify a count"}
        self.assertNotIn("placeholder", self.codes(self.rule))
        self.rule["source_doc_id"] = "D0xx"
        self.assertIn("placeholder", self.codes(self.rule))

    def test_non_finite_confidence_is_rejected_even_with_numeric_schema_bounds(self):
        schema = copy.deepcopy(SCHEMA)
        schema["properties"]["confidence"] = {"type": "number", "minimum": 0, "maximum": 1}
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.rule["confidence"] = value
                issues = validate_rule(self.rule, schema, self.sources)
                numeric_errors = [issue for issue in issues if issue["code"] == "non_finite_number"]
                self.assertEqual(len(numeric_errors), 1)
                self.assertIn("confidence", numeric_errors[0]["message"])
                report = validate_export({"rules": [self.rule]}, schema, self.sources)
                self.assertFalse(report["valid"])
                self.assertIn("non_finite_number", {issue["code"] for issue in report["errors"]})

    def test_non_finite_numbers_in_nested_optional_extras_report_each_path(self):
        nested = {"amounts": [float("nan"), {"upper": float("inf")}, [float("-inf")]]}
        self.rule["optional_extra"] = nested
        issues = validate_rule(self.rule, SCHEMA, self.sources)
        self.assertEqual(len(issues), 3)
        self.assertEqual({issue["code"] for issue in issues}, {"non_finite_number"})
        for path in ("optional_extra.amounts[0]", "optional_extra.amounts[1].upper", "optional_extra.amounts[2][0]"):
            self.assertTrue(any(path in issue["message"] for issue in issues))
        self.assertIs(self.rule["optional_extra"], nested)
        report = validate_export({"rules": [self.rule]}, SCHEMA, self.sources)
        self.assertFalse(report["valid"])
        self.assertEqual(report["error_count"], 3)

    def test_finite_numbers_and_non_numeric_lookalikes_remain_valid(self):
        self.rule["optional_extra"] = {
            "values": [0.0, -0.0, 1.7976931348623157e308, -1e308, 1e-300, 10 ** 400,
                       True, False, None, "NaN", "Infinity", "-Infinity"],
        }
        before = copy.deepcopy(self.rule)
        self.assertEqual(validate_rule(self.rule, SCHEMA, self.sources), [])
        self.assertTrue(validate_export({"rules": [self.rule]}, SCHEMA, self.sources)["valid"])
        self.assertEqual(self.rule, before)

    def test_export_requires_template_wrapper(self):
        for payload in ([self.rule], {"rules": {}}, None):
            result = validate_export(payload, SCHEMA, self.sources)
            self.assertFalse(result["valid"])
            self.assertEqual(result["errors"][0]["code"], "export_shape")

    def test_export_checks_duplicate_ids_and_missing_override_targets(self):
        second = copy.deepcopy(self.rule)
        second["overrides"] = ["rule-absent"]
        result = validate_export({"rules": [self.rule, second]}, SCHEMA, self.sources)
        self.assertFalse(result["valid"])
        self.assertEqual(result["rule_count"], 2)
        codes = {issue["code"] for issue in result["errors"]}
        self.assertIn("duplicate_rule_id", codes)
        self.assertIn("unknown_override", codes)

    def test_export_allows_references_to_later_records(self):
        second = copy.deepcopy(self.rule)
        second["team_rule_id"] = "rule-2"
        self.rule["overrides"] = ["rule-2"]
        result = validate_export({"rules": [self.rule, second]}, SCHEMA, self.sources)
        self.assertTrue(result["valid"], result["errors"])

    def test_nonobject_rule_and_invalid_override_type_report_errors(self):
        self.rule["overrides"] = [None, ["nested"]]
        result = validate_export({"rules": [self.rule, None]}, SCHEMA, self.sources)
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_count"], len(result["errors"]))


if __name__ == "__main__":
    unittest.main()
