"""Exercise cache and process boundaries without making model calls."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rent_rules.model import CodexModel, ModelError, object_schema


class ModelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.schema = object_schema({"ok": {"type": "boolean"}})
        self.responses = [{"ok": True}]
        self.calls = []
        self.environment = patch.dict("os.environ", {"CODEX_HOME": str(self.root / "config")})
        self.environment.start()
        self.process = patch("rent_rules.model.subprocess.run", side_effect=self.fake_process)
        self.process.start()

    def tearDown(self):
        self.process.stop()
        self.environment.stop()
        self.temp.cleanup()

    def fake_process(self, command, **options):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "codex-test-1\n", "")
        self.calls.append((command, options))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        destination = Path(command[command.index("--output-last-message") + 1])
        destination.write_text(json.dumps(response))
        return subprocess.CompletedProcess(command, 0)

    def model(self, **options):
        return CodexModel(self.root / "cache", executable="test-codex", **options)

    def test_cache_replay_avoids_a_second_model_call(self):
        model = self.model()
        first, info = model.generate("Extract the supplied text.", self.schema, "extract")
        second, cached = model.generate("Extract the supplied text.", self.schema, "extract")
        self.assertEqual(first, second)
        self.assertFalse(info["cached"])
        self.assertTrue(cached["cached"])
        self.assertEqual(len(self.calls), 1)

    def test_source_text_is_stdin_not_shell_code(self):
        prompt = 'Source text includes $(do-not-run) and `literal backticks`.'
        self.model(model="explicit-test-model").generate(prompt, self.schema, "extract")
        command, options = self.calls[0]
        self.assertIsInstance(command, list)
        self.assertNotIn(prompt, command)
        self.assertEqual(options["input"], prompt)
        self.assertFalse(options.get("shell", False))
        self.assertEqual(command[-1], "-")
        self.assertIn("read-only", command)
        self.assertIn("explicit-test-model", command)

    def test_offline_missing_response_fails_without_inference(self):
        with self.assertRaises(ModelError):
            self.model(offline=True).generate("Uncached", self.schema, "extract")
        self.assertEqual(self.calls, [])

    def test_prompt_and_configuration_changes_invalidate_cache(self):
        self.responses = [{"ok": True}] * 3
        self.model().generate("First prompt", self.schema, "extract")
        self.model().generate("Second prompt", self.schema, "extract")
        config = self.root / "config" / "config.toml"
        config.parent.mkdir()
        config.write_text('model = "different-test-model"\n')
        self.model().generate("First prompt", self.schema, "extract")
        self.assertEqual(len(self.calls), 3)

    def test_invalid_structured_response_retries_instead_of_being_cached(self):
        self.responses = [{"ok": "not a boolean"}, {"ok": True}]
        with patch("rent_rules.model.time.sleep"):
            result, info = self.model().generate("Extract", self.schema, "extract")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(info["attempts"], 2)

    def test_timeout_is_bounded_and_has_no_success_cache(self):
        self.responses = [subprocess.TimeoutExpired("test-codex", 1)] * 2
        with patch("rent_rules.model.time.sleep"), self.assertRaises(ModelError):
            self.model(timeout=1).generate("Extract", self.schema, "extract")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(list((self.root / "cache").rglob("response.json")), [])

    def test_default_reasoning_preserves_the_original_cache_key(self):
        model = self.model()
        prompt = "A stable default configuration."
        _, metadata = model.generate(prompt, self.schema, "extract")
        legacy_material = json.dumps({
            "prompt": prompt, "schema": self.schema, "model": model.model,
            "cli_version": model.version, "config_fingerprint": model.config_fingerprint,
        }, sort_keys=True)
        self.assertEqual(metadata["cache_key"], hashlib.sha256(legacy_material.encode("utf-8")).hexdigest())
        self.assertFalse(any("model_reasoning_effort=" in item for item in self.calls[0][0]))

    def test_explicit_reasoning_changes_only_the_run_flag_and_cache_key(self):
        self.responses = [{"ok": True}, {"ok": True}]
        prompt = "A shared prompt with an explicit reasoning override."
        _, default = self.model().generate(prompt, self.schema, "extract")
        model = self.model(reasoning_effort="high")
        _, explicit = model.generate(prompt, self.schema, "extract")
        _, replayed = model.generate(prompt, self.schema, "extract")
        command = self.calls[-1][0]
        position = command.index('model_reasoning_effort="high"')
        self.assertEqual(command[position - 1], "-c")
        self.assertNotIn("--model", command)
        self.assertNotEqual(default["cache_key"], explicit["cache_key"])
        self.assertEqual(explicit["reasoning_effort"], "high")
        self.assertTrue(replayed["cached"])
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.root / "config/config.toml").exists())

    def test_invalidation_preserves_rejections_and_allows_fresh_generation(self):
        self.responses = [{"ok": False}, {"ok": True}, {"ok": False}]
        model = self.model()
        first, metadata = model.generate("Retry this logical response.", self.schema, "extract")
        response_path = Path(metadata["response_path"])
        original_bytes = response_path.read_bytes()
        self.assertTrue(model.invalidate_response(metadata, "The response omitted a requested chunk."))
        self.assertFalse(response_path.exists())
        self.assertEqual((response_path.parent / "rejected_response.1.json").read_bytes(), original_bytes)
        rejection = json.loads((response_path.parent / "rejected_response.1.rejection.json").read_text())
        self.assertEqual(rejection["cache_key"], metadata["cache_key"])
        self.assertEqual(rejection["stage"], "extract")
        self.assertEqual(rejection["reason"], "The response omitted a requested chunk.")
        self.assertFalse(model.invalidate_response(metadata, "The response was already removed."))
        second, second_metadata = model.generate("Retry this logical response.", self.schema, "extract")
        self.assertNotEqual(first, second)
        self.assertFalse(second_metadata["cached"])
        self.assertTrue(model.invalidate_response(second_metadata, "A second explicit rejection."))
        self.assertTrue((response_path.parent / "rejected_response.1.json").exists())
        self.assertEqual(json.loads((response_path.parent / "rejected_response.2.json").read_text()), second)
        third, third_metadata = model.generate("Retry this logical response.", self.schema, "extract")
        self.assertEqual(third, {"ok": False})
        self.assertFalse(third_metadata["cached"])
        self.assertEqual(len(self.calls), 3)

    def test_offline_invalidation_neither_moves_the_cache_nor_invokes_a_model(self):
        _, metadata = self.model().generate("Preserve offline evidence.", self.schema, "extract")
        model = self.model(offline=True)
        path = Path(metadata["response_path"])
        original = path.read_bytes()
        with self.assertRaisesRegex(ModelError, "offline mode"):
            model.invalidate_response(metadata, "The response needs another review.")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.glob("rejected_response.*.json")), [])
        self.assertEqual(len(self.calls), 1)

    def test_invalidation_rejects_mismatched_keys_stages_and_external_paths(self):
        model = self.model()
        _, metadata = model.generate("Verify cache ownership.", self.schema, "extract")
        response_path = Path(metadata["response_path"])
        original = response_path.read_bytes()
        outside = self.root / "outside.json"
        outside.write_text('{"ok": false}')
        bad_metadata = [
            dict(metadata, cache_key="../outside"),
            dict(metadata, cache_key="0" * 64),
            dict(metadata, stage="../extract"),
            dict(metadata, stage="review"),
            dict(metadata, response_path=str(outside)),
            {"cache_key": metadata["cache_key"], "stage": "extract"},
        ]
        for bad in bad_metadata:
            with self.subTest(metadata=bad):
                with self.assertRaises(ModelError):
                    model.invalidate_response(bad, "Reject an untrusted metadata path.")
                self.assertEqual(response_path.read_bytes(), original)
                self.assertEqual(outside.read_text(), '{"ok": false}')
        with self.assertRaises(ModelError):
            model.invalidate_response(metadata, "")

    def test_invalidation_rejects_symlinked_cache_directories(self):
        model = self.model()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "response.json").write_text('{"ok": false}')
        stage = model.cache_dir / "extract"
        stage.mkdir()
        key = "a" * 64
        (stage / key).symlink_to(outside, target_is_directory=True)
        metadata = {"cache_key": key, "stage": "extract", "response_path": str(stage / key / "response.json")}
        with self.assertRaisesRegex(ModelError, "symlink"):
            model.invalidate_response(metadata, "Do not move outside evidence.")
        self.assertEqual((outside / "response.json").read_text(), '{"ok": false}')

    def test_nonfinite_live_json_is_rejected_preserved_and_retried(self):
        schema = object_schema({"value": {"type": "number"}})
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=str(value)):
                self.responses = [{"value": value}, {"value": 2}]
                model = self.model()
                with patch("rent_rules.model.time.sleep"):
                    result, metadata = model.generate("Reject live " + str(value), schema, "extract")
                self.assertEqual(result, {"value": 2})
                self.assertEqual(metadata["attempts"], 2)
                directory = Path(metadata["response_path"]).parent
                self.assertEqual((directory / "rejected_response.1.json").read_text(), json.dumps({"value": value}))
                json.dumps(result, allow_nan=False)

    def test_nonfinite_cached_json_is_quarantined_and_replaced_online(self):
        schema = object_schema({"value": {"type": "number"}})
        for token in ("NaN", "Infinity", "-Infinity", "1e400"):
            with self.subTest(token=token):
                self.responses = [{"value": 1}, {"value": 2}]
                model = self.model()
                prompt = "Reject cached " + token
                _, metadata = model.generate(prompt, schema, "extract")
                path = Path(metadata["response_path"])
                bad_json = '{"value": ' + token + '}'
                path.write_text(bad_json)
                result, new_metadata = model.generate(prompt, schema, "extract")
                self.assertEqual(result, {"value": 2})
                self.assertFalse(new_metadata["cached"])
                self.assertEqual((path.parent / "rejected_response.1.json").read_text(), bad_json)
                self.assertEqual(json.loads(path.read_text()), {"value": 2})

    def test_nonfinite_cached_json_is_preserved_and_rejected_offline(self):
        schema = object_schema({"value": {"type": "number"}})
        self.responses = [{"value": 1}]
        _, metadata = self.model().generate("Bad offline cache.", schema, "extract")
        path = Path(metadata["response_path"])
        bad_json = '{"value": NaN}'
        path.write_text(bad_json)
        with self.assertRaisesRegex(ModelError, "Invalid cached response in offline mode"):
            self.model(offline=True).generate("Bad offline cache.", schema, "extract")
        self.assertEqual(path.read_text(), bad_json)
        self.assertEqual(list(path.parent.glob("rejected_response.*.json")), [])
        self.assertEqual(len(self.calls), 1)

    def test_nonfinite_schema_values_fail_before_model_inference(self):
        schema = object_schema({"value": {"type": "number", "minimum": float("nan")}})
        with self.assertRaises(ValueError):
            self.model().generate("Invalid request schema.", schema, "extract")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
