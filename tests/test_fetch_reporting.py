"""Keep optional acquisition logs useful for reports and out of legal evidence."""

from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from rent_rules.pipeline import run


class RecordingModel:
    """Return no claims from a synthetic document without network or CLI calls."""

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, schema, stage):
        self.prompts.append(prompt)
        if stage != "extract":
            raise AssertionError("No claims should require another model stage.")
        chunks = json.loads(prompt.split("\nSOURCE_CHUNKS:\n", 1)[1])
        return {"documents": [{
            "chunk_id": chunk["chunk_id"], "doc_id": chunk["doc_id"],
            "document_type": "unrelated", "summary": "Synthetic test source.",
            "rules": [], "issues": [],
        } for chunk in chunks]}, {"stage": stage, "synthetic": True}


class FetchReportingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.pack, self.output, self.supplemental = root / "pack", root / "output", root / "supplemental"
        (self.pack / "corpus/text").mkdir(parents=True)
        (self.pack / "schema").mkdir()
        self.supplemental.mkdir()
        (self.pack / "schema/rule_record.schema.json").write_text('{"type":"object"}', encoding="utf-8")
        rows = []
        for doc_id in ("SYN001", "SYN002", "SYN003"):
            url = "https://example.test/" + doc_id
            available = doc_id == "SYN001"
            rows.append({
                "doc_id": doc_id, "jurisdictions": "CA", "url": url,
                "source_type": "official", "capture": "yes" if available else "no",
                "retrieved_at": "2026-10-01T12:00Z", "sha256": "",
                "text_file": "text/{}.txt".format(doc_id) if available else "",
                "status": "ok" if available else "missing",
            })
            if available:
                (self.pack / "corpus/text" / (doc_id + ".txt")).write_text(
                    "SOURCE: {}\nRETRIEVED: 2026-10-01 12:00 UTC\n\nA synthetic document with no legal claims.\n".format(url),
                    encoding="utf-8",
                )
        with (self.pack / "corpus/corpus_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def write_log(self, entries):
        (self.supplemental / "fetch_log.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")

    def read_output(self, filename):
        return json.loads((self.output / filename).read_text(encoding="utf-8"))

    def run_pipeline(self, supplemental=True):
        model = RecordingModel()
        with redirect_stdout(io.StringIO()):
            report = run(self.pack, self.output, model, workers=1,
                         supplemental_dir=self.supplemental if supplemental else None)
        self.assertEqual(report["failed_batch_count"], 0)
        self.assertEqual(report["available_source_count"], 1)
        self.assertEqual(report["processed_source_count"], 1)
        self.assertFalse(report["scope_complete"])
        self.assertEqual(self.read_output("rules.json"), {"rules": []})
        return report, model

    def test_absent_optional_log_preserves_default_reports(self):
        for supplemental in (True, False):
            with self.subTest(supplemental=supplemental):
                report, _ = self.run_pipeline(supplemental)
                coverage = self.read_output("source_coverage.json")
                queue = self.read_output("review_queue.json")["items"]
                self.assertEqual(set(coverage), {"sources"})
                self.assertNotIn("capture_log_diagnostics", report)
                self.assertTrue(all("capture_attempt" not in item for item in coverage["sources"] + queue))
                self.assertEqual({item["doc_id"] for item in queue}, {"SYN002", "SYN003"})
                self.assertTrue(all(item["type"] == "missing_source" for item in queue))

    def test_capture_metadata_annotates_reports_without_becoming_evidence(self):
        marker = "ADVISORY LOG TEXT MUST NEVER REACH THE MODEL"
        entries = [
            {"doc_id": "SYN001", "state": "fetch_error", "reason": marker,
             "finalurl": "https://example.test/failed"},
            {"doc_id": "SYN002", "state": "captured", "reason": marker,
             "finalurl": "https://example.test/redirected", "timestamp": "2026-10-03T20:00:00Z",
             "text_file": "invented.txt", "body": "Fabricated content cannot supply evidence."},
            {"doc_id": "SYN003", "state": "terms_restricted", "reason": "Synthetic restriction.",
             "finalurl": None},
        ]
        self.write_log(entries)
        report, model = self.run_pipeline()
        self.assertNotIn("capture_log_diagnostics", report)
        self.assertTrue(model.prompts)
        self.assertTrue(all(marker not in prompt and "invented.txt" not in prompt for prompt in model.prompts))
        coverage = {item["doc_id"]: item for item in self.read_output("source_coverage.json")["sources"]}
        queue = {item["doc_id"]: item for item in self.read_output("review_queue.json")["items"]}
        self.assertEqual(coverage["SYN001"]["status"], "processed")
        self.assertEqual(coverage["SYN001"]["capture_attempt"]["status"], "fetch_error")
        self.assertEqual(coverage["SYN002"]["status"], "missing_text")
        self.assertEqual(queue["SYN002"]["source_url"], "https://example.test/SYN002")
        self.assertEqual(queue["SYN002"]["reason"], "No local source body. A URL is not evidence of its contents.")
        expected = {"status": "captured", "reason": marker,
                    "final_url": "https://example.test/redirected", "timestamp": "2026-10-03T20:00:00Z"}
        self.assertEqual(queue["SYN002"]["capture_attempt"], expected)
        self.assertEqual(coverage["SYN002"]["capture_attempt"], expected)
        self.assertIsNone(queue["SYN003"]["capture_attempt"]["final_url"])
        for filename in ("source_inventory.json", "candidates.json", "rules.json", "extraction_audit.jsonl"):
            self.assertNotIn(marker, (self.output / filename).read_text(encoding="utf-8"))

    def test_malformed_log_is_diagnosed_without_aborting_extraction(self):
        malformed = (b"{not json", b"\xff", b"[]", b'{"entries":{}}', b"null")
        for payload in malformed:
            with self.subTest(payload=payload):
                (self.supplemental / "fetch_log.json").write_bytes(payload)
                report, _ = self.run_pipeline()
                diagnostics = report["capture_log_diagnostics"]
                self.assertEqual(len(diagnostics), 1)
                self.assertEqual(diagnostics[0]["type"], "capture_log_diagnostic")
                self.assertTrue(diagnostics[0]["reason"])
                self.assertEqual(self.read_output("source_coverage.json")["diagnostics"], diagnostics)
                queue = self.read_output("review_queue.json")["items"]
                self.assertIn(diagnostics[0], queue)
                self.assertEqual(sum(item["type"] == "missing_source" for item in queue), 2)

    def test_unreadable_log_is_diagnosed_without_aborting_extraction(self):
        (self.supplemental / "fetch_log.json").mkdir()
        report, _ = self.run_pipeline()
        self.assertEqual(report["capture_log_diagnostics"][0]["code"], "unreadable_capture_log")

    def test_invalid_entries_do_not_hide_other_sources_or_valid_metadata(self):
        invalid_entries = [None, [], {"doc_id": []}, {"doc_id": "UNKNOWN"},
                           {"doc_id": "SYN001", "state": {}, "reason": "Invalid status."},
                           {"doc_id": "SYN003", "state": "not_attempted", "reason": "Valid status."}]
        self.write_log(invalid_entries)
        report, _ = self.run_pipeline()
        self.assertEqual(len(report["capture_log_diagnostics"]), 5)
        coverage = {item["doc_id"]: item for item in self.read_output("source_coverage.json")["sources"]}
        self.assertEqual(set(coverage), {"SYN001", "SYN002", "SYN003"})
        self.assertNotIn("capture_attempt", coverage["SYN001"])
        self.assertEqual(coverage["SYN003"]["capture_attempt"]["status"], "not_attempted")

    def test_ambiguous_duplicate_log_records_are_not_silently_selected(self):
        self.write_log([
            {"doc_id": "SYN002", "state": "captured", "reason": "First record."},
            {"doc_id": "SYN002", "state": "http_error", "reason": "Second record."},
            {"doc_id": "SYN002", "state": "fetch_error", "reason": "Third record."},
        ])
        report, _ = self.run_pipeline()
        self.assertEqual({item["code"] for item in report["capture_log_diagnostics"]}, {"duplicate_capture_source"})
        coverage = self.read_output("source_coverage.json")["sources"]
        self.assertTrue(all("capture_attempt" not in item for item in coverage))


if __name__ == "__main__":
    unittest.main()
