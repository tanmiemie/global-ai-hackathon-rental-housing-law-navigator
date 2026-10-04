"""Read authentic extraction checkpoints without manufacturing cache entries.

Explicit resume reuses an existing response only when its stored prompt and
schema match the current extraction inputs. The original configuration-derived
cache key and response provenance are retained even if local settings changed.
This module never writes files, invokes a model, or consults current settings.
"""

from copy import deepcopy
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .model import ModelError


def _reject_constant(value):
    raise ValueError("Non-finite JSON number is not allowed: " + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key: " + key)
        result[key] = value
    return result


def _read_json(path):
    try:
        value = json.loads(path.read_bytes().decode("utf-8"),
                           parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        # JSON exponents such as 1e400 can overflow without invoking parse_constant.
        json.dumps(value, allow_nan=False)
        return value
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Invalid strict JSON in {}: {}".format(path, exc)) from exc


def load_extraction_checkpoint(batch_result_path, model_cache_dir, expected_prompt, extraction_schema):
    """Return ``(raw_output, metadata)`` for an exact, authentic prior extraction.

    Missing checkpoint artifacts, empty model_calls, or changed prompt/schema
    return None. Malformed metadata, unsafe paths, corrupt JSON, or a response
    violating the extraction schema raise ValueError/ModelError. Only the raw
    extraction is reused: downstream preparation, review, and repair must run
    normally. ``checkpoint_only`` batches need no processed documents/candidates.
    Metadata is copied, preserving the old cache key and configuration provenance,
    with ``cached=True``, ``reused_batch_path`` and ``original_cache_key`` added.
    ``original_call`` preserves the first call's metadata without growing nested
    provenance objects on repeated resume runs.
    """
    if not isinstance(expected_prompt, str):
        raise ValueError("expected_prompt must be the exact extraction prompt string.")
    checkpoint_path = Path(batch_result_path)
    try:
        checkpoint = _read_json(checkpoint_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ModelError("Cannot read extraction checkpoint {}: {}".format(checkpoint_path, exc)) from exc
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("model_calls"), list):
        raise ValueError("An extraction checkpoint must contain a model_calls array.")
    if not checkpoint["model_calls"]:
        return None
    metadata = checkpoint["model_calls"][0]
    if not isinstance(metadata, dict):
        raise ValueError("The checkpoint's first model call must contain extraction metadata.")
    key, stage, supplied = (metadata.get(name) for name in ("cache_key", "stage", "response_path"))
    if not isinstance(key, str) or re.fullmatch(r"[0-9a-fA-F]{64}", key) is None:
        raise ValueError("The extraction checkpoint has an invalid cache key.")
    if stage != "extract":
        raise ValueError("The checkpoint's first model call must have stage='extract'.")
    if not isinstance(supplied, str) or not supplied or "\x00" in supplied:
        raise ValueError("The extraction checkpoint requires a valid response_path.")
    try:
        cache_root = Path(model_cache_dir).resolve()
        directory = cache_root / "extract" / key
        response_path = directory / "response.json"
        # Read only constructed paths, never the metadata-supplied path. Reject
        # symlinks within the cache tree so no artifact can redirect this read.
        if directory.resolve() != directory or Path(supplied).resolve() != response_path:
            raise ValueError("The extraction checkpoint response_path is outside its expected cache entry or traverses a symlink.")
        artifacts = [directory / "prompt.txt", directory / "schema.json", response_path]
        for artifact in artifacts:
            if artifact.is_symlink() or artifact.resolve() != artifact:
                raise ValueError("Extraction checkpoint artifacts must not traverse symlinks: {}".format(artifact))
            if not artifact.exists():
                return None
            if not artifact.is_file():
                raise ValueError("Extraction checkpoint artifact is not a regular file: {}".format(artifact))
        # Decode bytes directly: universal-newline normalization would weaken
        # the required exact prompt match for CRLF versus LF source captures.
        stored_prompt = artifacts[0].read_bytes().decode("utf-8")
        if stored_prompt != expected_prompt:
            return None
        stored_schema = _read_json(artifacts[1])
        if stored_schema != extraction_schema:
            return None
        output = _read_json(response_path)
        try:
            Draft202012Validator(extraction_schema).validate(output)
        except ValidationError as exc:
            raise ModelError("Checkpoint response violates the extraction schema: {}".format(exc.message)) from exc
    except FileNotFoundError:
        # A concurrent cache invalidation may remove an otherwise valid response.
        return None
    except (OSError, UnicodeError, RuntimeError) as exc:
        if isinstance(exc, ModelError):
            raise
        raise ModelError("Cannot read extraction cache artifacts: {}".format(exc)) from exc
    resumed_metadata = deepcopy(metadata)
    resumed_metadata["original_call"] = deepcopy(metadata.get("original_call", metadata))
    resumed_metadata["cached"] = True
    resumed_metadata["reused_batch_path"] = str(checkpoint_path.resolve())
    resumed_metadata["original_cache_key"] = key
    return output, resumed_metadata
