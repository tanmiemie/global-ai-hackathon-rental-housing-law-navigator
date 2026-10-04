"""Read-only checkpoint reuse tests; no real model or network is involved."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rent_rules.model import EXTRACTION_SCHEMA, ModelError
from rent_rules.resume import load_extraction_checkpoint


PROMPT = "Synthetic extraction prompt\nAS_OF: 2026-10-01\nSource hash: abc123\nFictional body.\n"
OUTPUT = {"documents": [{
    "chunk_id": "SYN001:0", "doc_id": "SYN001", "document_type": "irrelevant",
    "summary": "This fictional source contains no supported rental obligation.",
    "rules": [], "issues": [],
}]}


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cache = self.root / "cache"
        self.key = "a1" * 32
        self.entry = self.cache / "extract" / self.key
        self.entry.mkdir(parents=True)
        self.checkpoint = self.root / "batch.json"
        self.metadata = {
            "cache_key": self.key, "stage": "extract", "cached": False,
            "response_path": str(self.entry / "response.json"), "cli_version": "old-cli",
            "reasoning_effort": "high", "elapsed_seconds": 123.45,
            "config_fingerprint": "original-configuration-fingerprint",
        }
        self.write_json(self.checkpoint, {
            "model_calls": [self.metadata, {"stage": "review"}],
            "documents": [{"old": "processed document is not reused"}],
            "candidates": [{"old": "processed candidate is not reused"}],
        })
        (self.entry / "prompt.txt").write_bytes(PROMPT.encode("utf-8"))
        self.write_json(self.entry / "schema.json", EXTRACTION_SCHEMA)
        self.write_json(self.entry / "response.json", OUTPUT)

    def write_json(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def load(self, prompt=PROMPT, schema=EXTRACTION_SCHEMA):
        return load_extraction_checkpoint(self.checkpoint, self.cache, prompt, schema)

    def set_metadata(self, metadata):
        self.write_json(self.checkpoint, {"model_calls": [metadata], "documents": [], "candidates": []})

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_faithful_old_configuration_response_is_reused_without_model_or_mutation(self):
        before = self.snapshot()
        with patch("subprocess.run", side_effect=AssertionError("No subprocess permitted")):
            output, metadata = self.load()
        self.assertEqual(output, OUTPUT)
        self.assertEqual(self.snapshot(), before)
        for key, value in self.metadata.items():
            self.assertEqual(metadata[key], True if key == "cached" else value)
        self.assertEqual(metadata["original_call"], self.metadata)
        self.assertEqual(metadata["original_cache_key"], self.key)
        self.assertEqual(metadata["reused_batch_path"], str(self.checkpoint))
        metadata["cache_key"] = "changed-only-in-return-value"
        output["documents"].clear()
        self.assertEqual(self.load()[0], OUTPUT)
        self.assertEqual(self.load()[1]["cache_key"], self.key)

    def test_repeated_resume_keeps_original_provenance_flat_and_marks_cached(self):
        _, first_metadata = self.load()
        self.set_metadata(first_metadata)
        _, repeated_metadata = self.load()
        self.assertTrue(repeated_metadata["cached"])
        self.assertEqual(repeated_metadata["original_call"], self.metadata)
        self.assertNotIn("original_call", repeated_metadata["original_call"])

    def test_checkpoint_only_does_not_require_processed_documents_or_candidates(self):
        self.write_json(self.checkpoint, {
            "checkpoint_only": True, "model_calls": [self.metadata],
            "documents": [], "candidates": [],
        })
        self.assertEqual(self.load()[0], OUTPUT)

    def test_changed_source_hash_date_or_prompt_format_does_not_reuse(self):
        changed = [PROMPT.replace("abc123", "new-source-hash"),
                   PROMPT.replace("2026-10-01", "2026-10-02"),
                   PROMPT.replace("Fictional body.", "Changed source body."),
                   PROMPT.replace("\n", "\r\n"), PROMPT + " "]
        for prompt in changed:
            with self.subTest(prompt=prompt):
                self.assertIsNone(self.load(prompt=prompt))

    def test_crlf_prompt_is_compared_without_newline_normalization(self):
        prompt = PROMPT.replace("\n", "\r\n")
        (self.entry / "prompt.txt").write_bytes(prompt.encode("utf-8"))
        self.assertIsNone(self.load())
        self.assertEqual(self.load(prompt=prompt)[0], OUTPUT)

    def test_schema_formatting_and_object_key_order_do_not_affect_reuse(self):
        (self.entry / "schema.json").write_text(
            json.dumps(EXTRACTION_SCHEMA, sort_keys=True, indent=4), encoding="utf-8")
        self.assertEqual(self.load()[0], OUTPUT)
        changed = deepcopy(EXTRACTION_SCHEMA)
        changed["description"] = "A changed extraction contract."
        self.assertIsNone(self.load(schema=changed))

    def test_missing_artifacts_or_empty_model_calls_return_none(self):
        for path in [self.checkpoint, self.entry / "response.json", self.entry / "prompt.txt",
                     self.entry / "schema.json"]:
            with self.subTest(path=path.name):
                saved = path.read_bytes()
                path.unlink()
                self.assertIsNone(self.load())
                path.write_bytes(saved)
        self.write_json(self.checkpoint, {"model_calls": []})
        self.assertIsNone(self.load())

    def test_altered_response_schema_is_rejected(self):
        self.write_json(self.entry / "response.json", {"documents": [{"rules": []}]})
        with self.assertRaisesRegex(ModelError, "extraction schema"):
            self.load()

    def test_invalid_metadata_is_rejected_before_reading_any_supplied_path(self):
        outside = self.root / "private.json"
        outside.write_text("private data must never be read", encoding="utf-8")
        invalid = [
            dict(self.metadata, cache_key="../private"),
            dict(self.metadata, cache_key="z" * 64),
            dict(self.metadata, stage="review"),
            dict(self.metadata, response_path=str(outside)),
            dict(self.metadata, response_path=""),
            dict(self.metadata, response_path=None),
            dict(self.metadata, response_path="\x00"),
            "not-metadata",
        ]
        original_read = Path.read_bytes

        def guarded_read(path):
            if path == outside:
                raise AssertionError("An unsafe metadata path was read")
            return original_read(path)

        for metadata in invalid:
            with self.subTest(metadata=metadata):
                self.set_metadata(metadata)
                with patch.object(Path, "read_bytes", guarded_read):
                    with self.assertRaises(ValueError):
                        self.load()

    def test_symlinked_cache_directory_cannot_escape_cache_root(self):
        outside = self.root / "outside-entry"
        self.entry.rename(outside)
        self.entry.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.load()

    def test_each_cache_artifact_rejects_symlink_redirection(self):
        for name in ["response.json", "prompt.txt", "schema.json"]:
            with self.subTest(name=name):
                artifact = self.entry / name
                outside = self.root / (name + ".outside")
                artifact.rename(outside)
                artifact.symlink_to(outside)
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.load()
                artifact.unlink()
                outside.rename(artifact)

    def test_non_finite_and_duplicate_json_are_rejected_in_every_json_artifact(self):
        bad_json = ['{"invalid": NaN}', '{"invalid": Infinity}',
                    '{"invalid": -Infinity}', '{"invalid": 1e400}',
                    '{"duplicate": 1, "duplicate": 2}', '{not-json']
        for path in [self.checkpoint, self.entry / "schema.json", self.entry / "response.json"]:
            saved = path.read_bytes()
            for value in bad_json:
                with self.subTest(path=path.name, value=value):
                    path.write_text(value, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        self.load()
            path.write_bytes(saved)

    def test_malformed_checkpoint_shape_raises(self):
        for value in [[], None, {}, {"model_calls": None}, {"model_calls": "extract"}]:
            with self.subTest(value=value):
                self.write_json(self.checkpoint, value)
                with self.assertRaisesRegex(ValueError, "model_calls"):
                    self.load()

    def test_non_regular_artifact_is_rejected(self):
        response = self.entry / "response.json"
        response.unlink()
        response.mkdir()
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.load()


if __name__ == "__main__":
    unittest.main()
