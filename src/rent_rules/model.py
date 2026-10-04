"""A resumable, structured model adapter using an authenticated Codex CLI.

No model outputs are manufactured here. Cache entries retain the exact prompt,
response, configuration and source hashes required to audit an extraction.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


def object_schema(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
NULLABLE = {"type": ["string", "null"]}
STRINGS = {"type": "array", "items": STRING}
CATEGORIES = ["rent_increase_limits", "just_cause_eviction", "security_deposits",
              "application_screening_fees", "screening_restrictions", "algorithmic_rent_setting"]
EVIDENCE = object_schema({"field": STRING, "quote": STRING})
CONDITION = object_schema({"fact": STRING, "operator": STRING, "value": STRING,
                           "logic_group": STRING, "description": STRING})
RULE_SCHEMA = object_schema({
    "jurisdiction": STRING, "level": {"enum": ["state", "city"]},
    "category": {"enum": CATEGORIES},
    "status": {"enum": ["in_force", "not_yet_effective", "pending", "failed", "unverified"]},
    "title": STRING, "requirement": STRING, "key_value": NULLABLE,
    "coverage_conditions": NULLABLE, "exemptions": NULLABLE,
    "conditions": {"type": "array", "items": CONDITION},
    "effective_date": NULLABLE, "end_date": NULLABLE, "temporal_notes": NULLABLE,
    "citation": STRING, "quoted_span": STRING,
    "evidence": {"type": "array", "items": EVIDENCE},
    "confidence": {"type": "number"}, "conflict_flag": {"type": "boolean"},
    "conflict_note": NULLABLE, "interaction": NULLABLE, "related_citations": STRINGS,
    "penalty": NULLABLE, "limitations": STRINGS,
})
DOCUMENT_SCHEMA = object_schema({
    "chunk_id": STRING, "doc_id": STRING,
    "document_type": {"enum": ["statute", "ordinance", "official_guidance", "rate_notice",
                                 "bill_status", "draft", "secondary", "irrelevant", "incomplete"]},
    "summary": STRING, "rules": {"type": "array", "items": RULE_SCHEMA}, "issues": STRINGS,
})
EXTRACTION_SCHEMA = object_schema({"documents": {"type": "array", "items": DOCUMENT_SCHEMA}})
REVIEW_SCHEMA = object_schema({"reviews": {"type": "array", "items": object_schema({
    "candidate_id": STRING, "decision": {"enum": ["accept", "review", "reject"]},
    "reason": STRING,
})}})


class ModelError(RuntimeError):
    pass


def _reject_json_constant(value):
    raise ValueError("Non-finite JSON number is not allowed: " + value)


def _read_response(path):
    response = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    # A valid JSON exponent such as 1e400 can also overflow a Python float.
    # Check decoded values without rewriting the original response artifact.
    json.dumps(response, allow_nan=False)
    return response


class CodexModel:
    def __init__(self, cache_dir, executable="codex", model=None, timeout=3600,
                 retries=1, offline=False, reasoning_effort=None):
        if reasoning_effort is not None and reasoning_effort not in {"low", "medium", "high", "xhigh", "ultra"}:
            raise ValueError("Unsupported reasoning_effort: {!r}".format(reasoning_effort))
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.executable = shutil.which(executable) or executable
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.offline = offline
        self.reasoning_effort = reasoning_effort
        self.version = subprocess.run([self.executable, "--version"], capture_output=True,
                                      text=True, check=True).stdout.strip()
        config = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
        self.config_fingerprint = hashlib.sha256(config.read_bytes()).hexdigest() if config.exists() else "absent"

    def invalidate_response(self, metadata, reason):
        """Quarantine a rejected response and permit a fresh online generation.

        Return True after preserving the response and rejection reason, or False
        if that response is already absent. Invalid metadata and filesystem
        failures raise ModelError. Offline adapters always raise ModelError and
        do not move files or perform inference. Callers must decide whether and
        how many times to retry generation after a successful invalidation.
        """
        if self.offline:
            raise ModelError("Cannot invalidate a response in offline mode; no model call was made.")
        if not isinstance(metadata, dict):
            raise ModelError("Response metadata must identify a cache key, stage, and response path.")
        key, stage, supplied = (metadata.get(name) for name in ("cache_key", "stage", "response_path"))
        if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise ModelError("Invalid response cache key.")
        if not isinstance(stage, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", stage) is None:
            raise ModelError("Invalid response cache stage.")
        if not isinstance(supplied, str) or not supplied:
            raise ModelError("Response metadata is missing its response_path.")
        if not isinstance(reason, str) or not reason.strip():
            raise ModelError("A non-empty rejection reason is required.")
        try:
            cache_root = self.cache_dir.resolve()
            directory = cache_root / stage / key
            response_path = directory / "response.json"
            if directory.resolve() != directory or Path(supplied).resolve() != response_path:
                raise ModelError("Response metadata does not identify its cache directory, or traverses a symlink.")
            if response_path.is_symlink():
                raise ModelError("A cached response must not be a symlink.")
            if not directory.is_dir():
                raise ModelError("Response cache directory does not exist: {}".format(directory))
            if not response_path.exists():
                return False
            if not response_path.is_file():
                raise ModelError("Cached response is not a regular file: {}".format(response_path))
            number = 1
            while True:
                rejected = directory / "rejected_response.{}.json".format(number)
                rejection_log = directory / "rejected_response.{}.rejection.json".format(number)
                if not rejected.exists() and not rejection_log.exists():
                    break
                number += 1
            rejection = {
                "cache_key": key, "stage": stage, "reason": reason,
                "rejected_at_unix": time.time(), "response_path": str(response_path),
                "rejected_response_path": str(rejected),
            }
            serialized = json.dumps(rejection, indent=2, allow_nan=False) + "\n"
            response_path.rename(rejected)
            rejection_log.write_text(serialized, encoding="utf-8")
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            if isinstance(exc, ModelError):
                raise
            raise ModelError("Cannot preserve rejected cached response: {}".format(exc)) from exc

    def generate(self, prompt, schema, stage):
        # The CLI default is kept unless the caller explicitly selects a model.
        configuration = {"prompt": prompt, "schema": schema, "model": self.model,
                         "cli_version": self.version,
                         "config_fingerprint": self.config_fingerprint}
        # Omitting this key preserves every pre-existing default cache key.
        if self.reasoning_effort is not None:
            configuration["reasoning_effort"] = self.reasoning_effort
        material = json.dumps(configuration, sort_keys=True, allow_nan=False)
        key = hashlib.sha256(material.encode("utf-8")).hexdigest()
        directory = self.cache_dir / stage / key
        directory.mkdir(parents=True, exist_ok=True)
        response_path = directory / "response.json"
        validator = Draft202012Validator(schema)
        cached_metadata = {"cache_key": key, "cached": True, "stage": stage,
                           "response_path": str(response_path), "cli_version": self.version}
        if self.reasoning_effort is not None:
            cached_metadata["reasoning_effort"] = self.reasoning_effort
        if response_path.exists():
            try:
                response = _read_response(response_path)
                validator.validate(response)
            except (OSError, ValueError, ValidationError) as exc:
                if self.offline:
                    raise ModelError("Invalid cached response in offline mode: {}".format(exc)) from exc
                self.invalidate_response(cached_metadata, "Cached response rejected: {}".format(exc))
            else:
                return response, cached_metadata
        if self.offline:
            raise ModelError("No cached response for {} {}".format(stage, key))
        (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
        schema_path = directory / "schema.json"
        schema_path.write_text(json.dumps(schema, allow_nan=False), encoding="utf-8")
        command = [self.executable, "exec", "--ephemeral", "--sandbox", "read-only",
                   "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                   "--skip-git-repo-check", "--color", "never", "--json",
                   "--output-schema", str(schema_path.resolve()),
                   "--output-last-message", str(response_path.resolve()), "-"]
        if self.model:
            command[2:2] = ["--model", self.model]
        if self.reasoning_effort is not None:
            command[2:2] = ["-c", 'model_reasoning_effort="{}"'.format(self.reasoning_effort)]
        started = time.time()
        for attempt in range(self.retries + 1):
            try:
                # A list of arguments and stdin are used, never shell interpolation.
                with (directory / "events.jsonl").open("w", encoding="utf-8") as events, \
                        (directory / "stderr.log").open("w", encoding="utf-8") as errors:
                    result = subprocess.run(command, input=prompt, text=True, stdout=events,
                                            stderr=errors, timeout=self.timeout)
                if result.returncode:
                    raise ModelError("Codex exited with {}; inspect {}".format(result.returncode, directory))
                response = _read_response(response_path)
                validator.validate(response)
                metadata = {"cache_key": key, "cached": False, "stage": stage,
                            "response_path": str(response_path), "cli_version": self.version,
                            "elapsed_seconds": round(time.time() - started, 2), "attempts": attempt + 1}
                if self.reasoning_effort is not None:
                    metadata["reasoning_effort"] = self.reasoning_effort
                (directory / "metadata.json").write_text(json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8")
                return response, metadata
            except (subprocess.TimeoutExpired, OSError, ValueError, ModelError, ValidationError) as exc:
                if response_path.exists():
                    self.invalidate_response(cached_metadata, "Generation attempt {} rejected: {}".format(attempt + 1, exc))
                if attempt == self.retries:
                    raise ModelError(str(exc)) from exc
                time.sleep(1)
