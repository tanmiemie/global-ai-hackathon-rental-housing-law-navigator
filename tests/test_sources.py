"""Tests for source provenance, unsafe inputs, and lossless chunk coverage."""

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from rent_rules.sources import (
    SourceDocument, chunk_document, load_sources, source_inventory,
)


FIELDS = [
    "doc_id", "jurisdictions", "url", "source_type", "capture",
    "retrieved_at", "sha256", "text_file", "status",
]
URL = "https://example.test/law"
TIME = "2026-10-01T22:35Z"


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pack = self.root / "pack"
        self.corpus = self.pack / "corpus"
        (self.corpus / "text").mkdir(parents=True)

    def row(self, doc_id="D001", captured=True, **updates):
        row = dict(zip(FIELDS, [
            doc_id, "Example, CA", URL, "official",
            "yes" if captured else "link-only",
            TIME if captured else "", "original-download-hash" if captured else "",
            "text/{}.txt".format(doc_id) if captured else "",
            "ok" if captured else "link-only",
        ]))
        row.update(updates)
        return row

    def manifest(self, rows):
        with (self.corpus / "corpus_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def snapshot(self, doc_id="D001", body="The exact source body.\n", url=URL, timestamp="2026-10-01 22:35 UTC", directory=None):
        text = "SOURCE: {}\nRETRIEVED: {}\n\n{}".format(url, timestamp, body)
        path = (directory or self.corpus / "text") / (doc_id + ".txt")
        path.write_bytes(text.encode("utf-8"))
        return text

    def test_preserves_all_rows_and_exact_bytes_including_crlf(self):
        self.manifest([self.row(), self.row("D002", captured=False)])
        text = self.snapshot(body="Section one.\nUnicode: \u00a7 \u03b1 \U0001f642.\n\n").replace("\n", "\r\n")
        (self.corpus / "text/D001.txt").write_bytes(text.encode("utf-8"))
        sources = load_sources(self.pack)
        self.assertEqual(set(sources), {"D001", "D002"})
        self.assertEqual(sources["D001"].text, text)
        self.assertEqual(sources["D001"].sha256, hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(sources["D001"].retrieved_at, TIME)
        self.assertEqual(sources["D001"].path, (self.corpus / "text/D001.txt").resolve())
        self.assertEqual(sources["D002"].text, "")
        self.assertIsNone(sources["D002"].path)
        self.assertEqual(sources["D002"].sha256, "")
        self.assertEqual(sources["D002"].capture_status, "link-only")

    def test_missing_manifest_or_listed_file_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "manifest.*missing"):
            load_sources(self.pack)
        self.manifest([self.row()])
        with self.assertRaisesRegex(ValueError, "D001.*missing"):
            load_sources(self.pack)

    def test_duplicate_ids_and_unsafe_ids_are_rejected(self):
        self.manifest([self.row(captured=False), self.row(captured=False)])
        with self.assertRaisesRegex(ValueError, "duplicate doc_id"):
            load_sources(self.pack)
        for doc_id in ("../D001", "/D001", "D001.txt", ""):
            with self.subTest(doc_id=doc_id):
                self.manifest([self.row(doc_id, captured=False)])
                with self.assertRaisesRegex(ValueError, "invalid doc_id"):
                    load_sources(self.pack)

    def test_absolute_traversal_and_windows_paths_are_rejected(self):
        for path in ("/tmp/source.txt", "../source.txt", "text/../source.txt", "C:/source.txt", "text\\source.txt"):
            with self.subTest(path=path):
                self.manifest([self.row(text_file=path)])
                with self.assertRaisesRegex(ValueError, "illegal relative path"):
                    load_sources(self.pack)

    def test_symlinks_cannot_escape_source_roots(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (self.corpus / "text/D001.txt").symlink_to(outside)
        self.manifest([self.row()])
        with self.assertRaisesRegex(ValueError, "escapes source directory"):
            load_sources(self.pack)

    def test_conflicting_or_duplicate_headers_are_rejected(self):
        self.manifest([self.row()])
        self.snapshot(url="https://example.test/other")
        with self.assertRaisesRegex(ValueError, "SOURCE header conflicts"):
            load_sources(self.pack)
        self.snapshot(timestamp="2026-10-02T22:35:00Z")
        with self.assertRaisesRegex(ValueError, "RETRIEVED header conflicts"):
            load_sources(self.pack)
        text = self.snapshot()
        (self.corpus / "text/D001.txt").write_text("SOURCE: {}\n{}".format(URL, text), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate SOURCE"):
            load_sources(self.pack)

    def test_missing_headers_and_invalid_timestamp_are_rejected(self):
        self.manifest([self.row()])
        (self.corpus / "text/D001.txt").write_text("A source without provenance.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing capture headers"):
            load_sources(self.pack)
        self.snapshot(timestamp="yesterday")
        with self.assertRaisesRegex(ValueError, "invalid retrieval timestamp"):
            load_sources(self.pack)

    def test_header_shaped_body_lines_are_preserved(self):
        self.manifest([self.row()])
        text = self.snapshot(body="SOURCE: This is part of the document body.\nRETRIEVED: A quoted label.\n")
        source = load_sources(self.pack)["D001"]
        self.assertEqual(source.text, text)
        self.assertTrue(source_inventory({source.doc_id: source})[0]["available"])

    def test_equivalent_timestamp_offsets_are_accepted(self):
        self.manifest([self.row()])
        self.snapshot(timestamp="2026-10-01T15:35:00-07:00")
        self.assertEqual(load_sources(self.pack)["D001"].retrieved_at, TIME)

    def test_supplemental_capture_supplies_its_own_metadata(self):
        self.manifest([self.row(), self.row("D002", captured=False)])
        self.snapshot()
        supplemental = self.root / "supplemental"
        supplemental.mkdir()
        new_text = self.snapshot(
            "D001", body="New capture.\n", directory=supplemental,
            url="https://example.test/redirected", timestamp="2026-10-03 10:00 UTC",
        )
        self.snapshot("D002", body="Previously unavailable.\n", directory=supplemental, timestamp="2026-10-03 10:00 UTC")
        sources = load_sources(self.pack, supplemental)
        self.assertEqual(sources["D001"].text, new_text)
        self.assertEqual(sources["D001"].url, "https://example.test/redirected")
        self.assertEqual(sources["D001"].retrieved_at, "2026-10-03T10:00:00Z")
        self.assertEqual(sources["D001"].capture_status, "supplemental")
        self.assertEqual(sources["D002"].capture_status, "supplemental")
        self.assertEqual(sources["D001"].sha256, hashlib.sha256(new_text.encode("utf-8")).hexdigest())

    def test_supplemental_captures_require_headers_and_safe_paths(self):
        self.manifest([self.row(captured=False)])
        supplemental = self.root / "supplemental"
        supplemental.mkdir()
        path = supplemental / "D001.txt"
        path.write_text("No capture headers", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "supplemental source is missing capture headers"):
            load_sources(self.pack, supplemental)
        path.unlink()
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "escapes source directory"):
            load_sources(self.pack, supplemental)

    def test_inventory_groups_only_identical_bodies(self):
        self.manifest([
            self.row(), self.row("D002", url="https://example.test/mirror"),
            self.row("D003"), self.row("D004", captured=False, capture="check-terms"),
        ])
        self.snapshot(body="Same body.\n")
        self.snapshot("D002", body="Same body.\n", url="https://example.test/mirror")
        self.snapshot("D003", body="Different body.\n")
        inventory = {item["doc_id"]: item for item in source_inventory(load_sources(self.pack))}
        self.assertEqual(inventory["D001"]["duplicate_body_group"], ["D001", "D002"])
        self.assertEqual(inventory["D002"]["duplicate_body_group"], ["D001", "D002"])
        self.assertEqual(inventory["D003"]["duplicate_body_group"], [])
        self.assertNotEqual(inventory["D001"]["sha256"], inventory["D002"]["sha256"])
        self.assertTrue(inventory["D001"]["available"])
        self.assertFalse(inventory["D004"]["available"])
        self.assertEqual(inventory["D004"]["capture_status"], "check-terms")
        self.assertEqual(inventory["D004"]["char_count"], 0)

    def test_invalid_utf8_and_missing_manifest_columns(self):
        self.manifest([self.row()])
        (self.corpus / "text/D001.txt").write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "cannot read UTF-8"):
            load_sources(self.pack)
        (self.corpus / "corpus_manifest.csv").write_text("doc_id,url\nD001,https://example.test\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing columns"):
            load_sources(self.pack)


class ChunkTests(unittest.TestCase):
    def source(self, text):
        return SourceDocument("D001", "CA", URL, "official", TIME, text, None, "", "ok")

    def assert_lossless(self, text, max_chars, overlap):
        chunks = chunk_document(self.source(text), max_chars, overlap)
        self.assertEqual(chunks[0]["start"], 0)
        self.assertEqual(chunks[-1]["end"], len(text))
        covered = [False] * len(text)
        previous_start = -1
        rebuilt = ""
        through = 0
        for chunk in chunks:
            self.assertGreater(chunk["start"], previous_start)
            previous_start = chunk["start"]
            self.assertEqual(chunk["text"], text[chunk["start"]:chunk["end"]])
            self.assertLessEqual(len(chunk["text"]), max_chars)
            self.assertEqual(chunk["doc_id"], "D001")
            for index in range(chunk["start"], chunk["end"]):
                covered[index] = True
            rebuilt += chunk["text"][max(0, through - chunk["start"]):]
            through = chunk["end"]
        self.assertTrue(all(covered))
        self.assertEqual(rebuilt, text)
        self.assertEqual(len(set(chunk["chunk_id"] for chunk in chunks)), len(chunks))
        return chunks

    def test_long_statute_is_covered_through_the_last_character(self):
        text = "SOURCE: {}\r\nRETRIEVED: {}\r\n\r\n".format(URL, TIME)
        text += ("Section \u00a7 123: conditions and exceptions.\r\n" * 5000) + "FINAL CLAUSE."
        chunks = self.assert_lossless(text, 24000, 1200)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(chunks[-1]["text"].endswith("FINAL CLAUSE."))

    def test_long_lines_no_overlap_and_high_overlap(self):
        for text in ("x" * 233, "abc\ndef\n" * 30, "\u03b1\U0001f642\u4e2d\r\n" * 40):
            for size, overlap in ((25, 0), (25, 24), (1, 0), (17, 5)):
                with self.subTest(size=size, overlap=overlap, text=text[:8]):
                    self.assert_lossless(text, size, overlap)

    def test_empty_and_short_sources(self):
        self.assertEqual(chunk_document(self.source("")), [])
        source = self.source("Short source.\n")
        self.assertEqual(chunk_document(source), [{
            "chunk_id": "D001:0001", "doc_id": "D001", "start": 0,
            "end": len(source.text), "text": source.text,
        }])

    def test_invalid_chunk_parameters(self):
        for size, overlap in ((0, 0), (-1, 0), (10, 10), (10, -1), (10, 1.5), (True, 0), (10, False)):
            with self.subTest(size=size, overlap=overlap):
                with self.assertRaises(ValueError):
                    chunk_document(self.source("text"), size, overlap)


if __name__ == "__main__":
    unittest.main()
