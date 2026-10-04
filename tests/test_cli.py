"""Test CLI output isolation and explicit date checks without model calls."""

from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rent_rules.cli import main
from rent_rules.sources import SourceDocument


QUOTE = "The fictional provider shall send a receipt for each payment."
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["team_rule_id", "status", "source_url", "quoted_span"],
    "properties": {
        "team_rule_id": {"type": "string"},
        "status": {"enum": ["in_force", "pending", "failed", "not_yet_effective"]},
        "source_doc_id": {"type": "string"},
        "source_url": {"type": "string"},
        "quoted_span": {"type": "string", "minLength": 20},
        "effective_date": {"type": ["string", "null"]},
    },
}


class CliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pack = self.root / "pack"
        self.output = self.root / "validation-output"
        (self.pack / "schema").mkdir(parents=True)
        self.output.mkdir()
        (self.pack / "schema/rule_record.schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
        self.source = SourceDocument(
            "SYN001", "CA", "https://example.test/synthetic", "official", "2026-10-01T22:35Z",
            "SOURCE: https://example.test/synthetic\nRETRIEVED: 2026-10-01 22:35 UTC\n\n" + QUOTE,
            None, "synthetic-hash", "ok",
        )
        self.rule = {
            "team_rule_id": "synthetic-rule-1",
            "jurisdiction": "CA",
            "status": "in_force",
            "source_doc_id": self.source.doc_id,
            "source_url": self.source.url,
            "quoted_span": QUOTE,
            "retrieved_at": self.source.retrieved_at,
            "as_of": "2026-09-01",
            "effective_date": None,
        }
        self.extract_report = {
            "valid": True, "rule_count": 1, "candidate_count": 1,
            "processed_source_count": 1, "failed_batch_count": 0,
            "review_item_count": 0, "scope_complete": True,
        }

    def invoke(self, arguments):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(arguments)

    def extract(self, arguments):
        with patch("rent_rules.cli.CodexModel") as model, patch(
                "rent_rules.cli.run", return_value=copy.deepcopy(self.extract_report)) as pipeline:
            result = self.invoke(["extract"] + arguments)
        self.assertEqual(result, 0)
        return model.call_args, pipeline.call_args

    def validate(self, rules, extra=None):
        (self.output / "rules.json").write_text(json.dumps({"rules": rules}), encoding="utf-8")
        arguments = ["validate", "--pack", str(self.pack), "--output", str(self.output)] + (extra or [])
        with patch("rent_rules.cli.load_sources", return_value={self.source.doc_id: self.source}), patch(
                "rent_rules.cli.CodexModel") as model:
            result = self.invoke(arguments)
            model.assert_not_called()
        report = json.loads((self.output / "format_validation.json").read_text(encoding="utf-8"))
        return result, report

    def test_full_extraction_keeps_existing_output_and_date_defaults(self):
        _, pipeline = self.extract([])
        self.assertEqual(pipeline.args[1], Path("outputs/module_a"))
        self.assertEqual(pipeline.kwargs["as_of"], "2026-10-01")
        self.assertIsNone(pipeline.kwargs["selected"])

    def test_targeted_extraction_defaults_to_a_separate_pilot_output(self):
        _, pipeline = self.extract(["--documents", "SYN001,SYN002"])
        self.assertEqual(pipeline.args[1], Path("outputs/pilot"))
        self.assertEqual(pipeline.kwargs["selected"], {"SYN001", "SYN002"})

    def test_explicit_output_is_honored_for_full_and_targeted_runs(self):
        explicit = self.root / "chosen-output"
        for selection in ([], ["--documents", "SYN001"]):
            with self.subTest(selection=selection):
                _, pipeline = self.extract(selection + ["--output", str(explicit)])
                self.assertEqual(pipeline.args[1], explicit)

    def test_reasoning_override_and_explicit_query_date_reach_the_adapter(self):
        model, pipeline = self.extract(["--reasoning-effort", "high", "--as-of", "2027-07-02"])
        self.assertEqual(model.kwargs["reasoning_effort"], "high")
        self.assertEqual(pipeline.kwargs["as_of"], "2027-07-02")

    def test_invalid_extraction_date_fails_before_initializing_the_model(self):
        for value in ("2026-02-30", "2026-10", "20261001", ""):
            with self.subTest(value=value), patch("rent_rules.cli.CodexModel") as model, patch("rent_rules.cli.run") as pipeline:
                with self.assertRaises(SystemExit) as error:
                    self.invoke(["extract", "--as-of", value])
                self.assertEqual(error.exception.code, 1)
                model.assert_not_called()
                pipeline.assert_not_called()

    def test_validation_without_date_checks_the_exports_own_date(self):
        result, report = self.validate([self.rule])
        self.assertEqual(result, 0)
        self.assertTrue(report["valid"], report["errors"])
        self.assertNotIn("requested_as_of", report)

    def test_explicit_matching_validation_date_succeeds(self):
        result, report = self.validate([self.rule], ["--as-of", self.rule["as_of"]])
        self.assertEqual(result, 0)
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["requested_as_of"], self.rule["as_of"])

    def test_explicit_validation_date_checks_each_record_and_reports_all_mismatches(self):
        second = dict(self.rule, team_rule_id="synthetic-rule-2", as_of="2026-10-01")
        third = dict(self.rule, team_rule_id="synthetic-rule-3", as_of="2026-08-01")
        result, report = self.validate([self.rule, second, third], ["--as-of", "2026-10-01"])
        self.assertEqual(result, 1)
        self.assertFalse(report["valid"])
        mismatches = [issue for issue in report["errors"] if issue["code"] == "as_of_mismatch"]
        self.assertEqual(len(mismatches), 2)
        self.assertIn("rules[0].as_of", mismatches[0]["message"])
        self.assertIn("rules[2].as_of", mismatches[1]["message"])
        self.assertIn("2026-10-01", mismatches[0]["message"])
        self.assertEqual(report["error_count"], len(report["errors"]))

    def test_invalid_requested_validation_date_is_a_persisted_report_error(self):
        for value in ("2026-02-29", "2026-04-31", "2026-10", "20261001", "tomorrow", ""):
            with self.subTest(value=value):
                result, report = self.validate([self.rule], ["--as-of", value])
                self.assertEqual(result, 1)
                self.assertFalse(report["valid"])
                self.assertIn("invalid_requested_as_of", {issue["code"] for issue in report["errors"]})
                self.assertEqual(report["error_count"], len(report["errors"]))

    def test_valid_leap_day_is_accepted(self):
        rule = dict(self.rule, as_of="2024-02-29")
        result, report = self.validate([rule], ["--as-of", "2024-02-29"])
        self.assertEqual(result, 0)
        self.assertTrue(report["valid"], report["errors"])

    def test_date_mismatch_does_not_hide_existing_validation_errors(self):
        rule = dict(self.rule, quoted_span="A made-up quote absent from the synthetic source.")
        result, report = self.validate([rule], ["--as-of", "2026-10-01"])
        self.assertEqual(result, 1)
        codes = {issue["code"] for issue in report["errors"]}
        self.assertIn("quote_not_found", codes)
        self.assertIn("as_of_mismatch", codes)
        self.assertEqual(report["error_count"], len(report["errors"]))


if __name__ == "__main__":
    unittest.main()
