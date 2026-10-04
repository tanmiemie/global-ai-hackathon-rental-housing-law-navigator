"""Validate extracted rules against the schema and their source evidence.

These checks establish structure and provenance, not legal correctness. Quote
offsets are zero-based Python string offsets; line numbers are one-based.
"""

import math
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from jsonschema.validators import validator_for


_FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)
_PARTIAL_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$", re.ASCII)
_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d+)?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?$",
    re.ASCII,
)
_PLACEHOLDER = re.compile(
    r"<\s*(?:copy|insert|replace|fill|your|exact)\b[^>]*>"
    r"|\{\{[^{}]+\}\}"
    r"|^\s*(?:TODO|TBD|PLACEHOLDER|REPLACE_ME|INSERT_HERE|D\d*x+|\.{3}|\u2026)\s*$",
    re.IGNORECASE,
)
_MISSING_CITATION = re.compile(
    r"\s*(?:"
    r"(?:none|unknown|unavailable|n/?a|not (?:available|provided|supplied|specified|known))"
    r"|no\s+(?:(?:legal|official|statutory|specific|exact)\s+)*citations?"
    r"(?:\s+(?:is|was|has been))?"
    r"(?:\s+(?:supplied|provided|available|given|identified|found|stated|specified|known))?"
    r"(?:\s+(?:for|in|by|within|from)\b[^\n]*)?"
    r"|(?:(?:legal|official|statutory|specific|exact)\s+)*citation\s*(?:(?:is|was)\s+|[:=-]\s*)?"
    r"(?:unknown|unavailable|not\s+(?:available|provided|supplied|identified|found|specified|known))"
    r"(?:\s+(?:for|in|by|within|from)\b[^\n]*)?"
    r")\s*[.;]?\s*",
    re.IGNORECASE,
)


def _error(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}


def _collapse_with_offsets(text: str) -> Tuple[str, List[int], List[int]]:
    """Collapse only whitespace and retain a map back to the unmodified text."""
    characters = []  # type: List[str]
    starts = []  # type: List[int]
    ends = []  # type: List[int]
    previous_end = None  # type: Optional[int]
    for token in re.finditer(r"\S+", text):
        if previous_end is not None:
            characters.append(" ")
            starts.append(previous_end)
            ends.append(token.start())
        for offset in range(token.start(), token.end()):
            characters.append(text[offset])
            starts.append(offset)
            ends.append(offset + 1)
        previous_end = token.end()
    return "".join(characters), starts, ends


def _quote_location(text: str, start: int, end: int, match_type: str) -> Dict[str, Any]:
    def line_at(offset: int) -> int:
        line = 1
        for newline in re.finditer(r"\r\n|\r|\n", text):
            if newline.end() > offset:
                break
            line += 1
        return line

    return {
        "quote": text[start:end],
        "start": start,
        "end": end,
        "start_line": line_at(start),
        "end_line": line_at(end - 1),
        "match_type": match_type,
    }


def locate_quote(text: str, quote: str) -> Optional[Dict[str, Any]]:
    """Find a quote exactly, or with whitespace differences only.

    Return the original source span so callers can replace a model's normalized
    quote before export. No case folding, punctuation rewriting, dehyphenation,
    omitted-word matching, or fuzzy matching is performed. If multiple spans
    match, the first exact match takes precedence over normalized matches.
    """
    if not isinstance(text, str) or not isinstance(quote, str) or not quote.strip():
        return None
    start = text.find(quote)
    if start >= 0:
        return _quote_location(text, start, start + len(quote), "exact")
    normalized_quote = " ".join(quote.split())
    normalized_text, starts, ends = _collapse_with_offsets(text)
    normalized_start = normalized_text.find(normalized_quote)
    if normalized_start < 0:
        return None
    normalized_end = normalized_start + len(normalized_quote)
    return _quote_location(
        text, starts[normalized_start], ends[normalized_end - 1], "whitespace"
    )


def _valid_date(value: Any, partial: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    if not partial and not _FULL_DATE.fullmatch(value):
        return False
    match = _PARTIAL_DATE.fullmatch(value)
    if match is None:
        return False
    try:
        date(int(match.group(1)), int(match.group(2) or 1), int(match.group(3) or 1))
    except ValueError:
        return False
    return True


def _valid_retrieved_at(value: Any) -> bool:
    if _valid_date(value):
        return True
    if not isinstance(value, str) or not _DATETIME.fullmatch(value):
        return False
    # Python 3.8's fromisoformat does not recognize the Z timezone suffix.
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return True


def _same_retrieval_time(value: Any, source_value: Any) -> bool:
    """Compare instants, or UTC calendar days when only a date is supplied.

    A timezone-free timestamp cannot establish the same instant as an aware
    timestamp. Date-only values retain their intentionally lower precision.
    """
    if not _valid_retrieved_at(value) or not _valid_retrieved_at(source_value):
        return False

    def parse(item):
        if _FULL_DATE.fullmatch(item):
            return date.fromisoformat(item)
        timestamp = datetime.fromisoformat(item[:-1] + "+00:00" if item.endswith("Z") else item)
        return timestamp.astimezone(timezone.utc) if timestamp.tzinfo is not None else timestamp

    actual, expected = parse(value), parse(source_value)
    if isinstance(actual, datetime) and isinstance(expected, datetime):
        if (actual.tzinfo is None) != (expected.tzinfo is None):
            return False
        return actual == expected
    actual_day = actual.date() if isinstance(actual, datetime) else actual
    expected_day = expected.date() if isinstance(expected, datetime) else expected
    return actual_day == expected_day


def _body_start(text: str) -> int:
    """Locate the body after optional leading capture headers, without edits."""
    offset, seen_header, header_closed = 0, False, False
    for line in text.splitlines(keepends=True):
        stripped = line.strip().lstrip("\ufeff") if offset == 0 else line.strip()
        if not stripped:
            header_closed = seen_header
            offset += len(line)
            continue
        if header_closed:
            break
        name, separator, _ = stripped.partition(":")
        if separator and name in {"SOURCE", "RETRIEVED"}:
            seen_header = True
            offset += len(line)
            continue
        break
    return offset if seen_header else 0


def _locate_body_quote(text: str, quote: str) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        return None
    offset = _body_start(text)
    result = locate_quote(text[offset:], quote)
    if result is None:
        return None
    return _quote_location(text, result["start"] + offset, result["end"] + offset, result["match_type"])


def _validate_source_ids(rule: dict, sources: dict) -> List[Dict[str, str]]:
    if "source_doc_ids" not in rule:
        return []
    ids = rule["source_doc_ids"]
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        return [_error("invalid_source_doc_ids", "source_doc_ids must be an array of document ID strings.")]
    errors = []
    for doc_id in ids:
        if doc_id not in sources:
            errors.append(_error("unknown_source", "source_doc_ids contains an unknown document: " + doc_id))
    if rule.get("source_doc_id") not in ids:
        errors.append(_error("primary_source_missing", "source_doc_ids must include source_doc_id."))
    return errors


def _validate_evidence(rule: dict, sources: dict) -> List[Dict[str, str]]:
    if "evidence" not in rule:
        return []
    if not isinstance(rule["evidence"], list):
        return [_error("invalid_evidence", "evidence must be an array of source evidence objects.")]
    errors = []
    listed_ids = rule.get("source_doc_ids")
    for index, item in enumerate(rule["evidence"]):
        label = "evidence[{}]".format(index)
        if not isinstance(item, dict):
            errors.append(_error("invalid_evidence", label + " must be an object."))
            continue
        doc_id = item.get("source_doc_id")
        source = sources.get(doc_id) if isinstance(doc_id, str) else None
        if source is None:
            errors.append(_error("unknown_evidence_source", label + " must identify an existing source_doc_id."))
            continue
        if isinstance(listed_ids, list) and doc_id not in listed_ids:
            errors.append(_error("evidence_source_unlisted", label + " source is absent from source_doc_ids."))
        if item.get("source_url") != getattr(source, "url", None):
            errors.append(_error("evidence_source_url_mismatch", label + " source_url does not match its document."))
        quote, text = item.get("quote"), getattr(source, "text", None)
        if not isinstance(quote, str) or not quote.strip():
            errors.append(_error("invalid_evidence_quote", label + " quote must contain non-whitespace source text."))
            continue
        if not isinstance(text, str):
            errors.append(_error("evidence_quote_not_found", label + " document has no readable source text."))
            continue
        if "start" in item or "end" in item:
            start, end = item.get("start"), item.get("end")
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                errors.append(_error("invalid_evidence_offsets", label + " requires valid integer start/end character offsets."))
                continue
            span = text[start:end]
            if span != quote and " ".join(span.split()) != " ".join(quote.split()):
                errors.append(_error("evidence_offset_mismatch", label + " offsets do not delimit its quoted source text."))
            if start < _body_start(text):
                errors.append(_error("header_quote", label + " must not quote SOURCE/RETRIEVED capture headers."))
        elif _locate_body_quote(text, quote) is None:
            if locate_quote(text, quote) is not None:
                errors.append(_error("header_quote", label + " must not quote SOURCE/RETRIEVED capture headers."))
            else:
                errors.append(_error("evidence_quote_not_found", label + " quote was not found in its source document."))
    return errors


def _allowed_jurisdictions(sources: dict) -> set:
    """Use manifest labels, preserving the comma within city/state names."""
    allowed = set()
    for source in sources.values():
        labels = getattr(source, "jurisdictions", None)
        if isinstance(labels, str):
            allowed.update(label.strip() for label in labels.split(";") if label.strip())
    return allowed


def _strings(value: Any, path: str = "") -> Iterator[Tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, "{}.{}".format(path, key) if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, "{}[{}]".format(path, index))


def _non_finite_paths(value: Any, path: str = "") -> Iterator[str]:
    """Find non-JSON numbers even inside optional fields without schema limits."""
    if isinstance(value, float) and not math.isfinite(value):
        yield path or "$"
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _non_finite_paths(item, "{}.{}".format(path, key) if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _non_finite_paths(item, "{}[{}]".format(path, index))


def validate_rule(rule: dict, schema: dict, sources: dict) -> List[Dict[str, str]]:
    """Validate one record without modifying it or its source documents.

    ``sources`` maps document IDs to objects exposing ``text``, ``url``, and
    ``retrieved_at``. Extraction metadata requires a full ``as_of`` date and a
    ``retrieved_at`` ISO date or timestamp in addition to the organizer schema.
    The schema permits year/month precision for ``effective_date``; that
    precision is preserved rather than inventing a day.
    """
    errors = [
        _error("non_finite_number", "{} contains a non-finite number; JSON numbers must be finite.".format(path))
        for path in _non_finite_paths(rule)
    ]  # type: List[Dict[str, str]]
    validator_class = validator_for(schema)
    validator_class.check_schema(schema)
    validator = validator_class(schema)
    schema_errors = sorted(
        validator.iter_errors(rule),
        key=lambda item: ("/".join(str(part) for part in item.absolute_path), item.message),
    )
    for issue in schema_errors:
        path = "/".join(str(part) for part in issue.absolute_path) or "$"
        errors.append(_error("schema", "{}: {}".format(path, issue.message)))
    if not isinstance(rule, dict):
        return errors

    for field in schema.get("required", []):
        if isinstance(rule.get(field), str) and not rule[field].strip():
            errors.append(_error("empty_field", "{} must not be blank.".format(field)))
    for path, value in _strings(rule):
        if _PLACEHOLDER.search(value):
            errors.append(_error("placeholder", "{} contains a template placeholder.".format(path)))
    citation = rule.get("citation")
    if isinstance(citation, str) and _MISSING_CITATION.fullmatch(citation):
        errors.append(_error(
            "missing_citation",
            "citation declares that no citation is available; supply a source-supported legal reference or exact official policy/notice title, or leave the candidate unresolved.",
        ))
    allowed_jurisdictions = _allowed_jurisdictions(sources)
    if allowed_jurisdictions and (
        not isinstance(rule.get("jurisdiction"), str)
        or rule["jurisdiction"] not in allowed_jurisdictions
    ):
        errors.append(_error("out_of_scope_jurisdiction", "jurisdiction is not present in the supplied manifest scope."))

    if not _valid_date(rule.get("as_of")):
        errors.append(_error("invalid_as_of", "as_of must be a real ISO date (YYYY-MM-DD)."))
    if not _valid_retrieved_at(rule.get("retrieved_at")):
        errors.append(_error("invalid_retrieved_at", "retrieved_at must be a real ISO date or timestamp."))
    effective_date = rule.get("effective_date")
    if effective_date is not None and not _valid_date(effective_date, partial=True):
        errors.append(_error("invalid_effective_date", "effective_date must be a real ISO year, month, or date, or null."))
    # effective_date is the operative date. Compare only complete valid dates;
    # missing or partial dates do not establish when a provision takes effect.
    if _valid_date(effective_date) and _valid_date(rule.get("as_of")):
        operative_day = date.fromisoformat(effective_date)
        query_day = date.fromisoformat(rule["as_of"])
        if rule.get("status") == "in_force" and operative_day > query_day:
            errors.append(_error("temporal_status_mismatch", "in_force requires effective_date on or before as_of."))
        elif rule.get("status") == "not_yet_effective" and operative_day <= query_day:
            errors.append(_error("temporal_status_mismatch", "not_yet_effective requires effective_date after as_of."))

    source_id = rule.get("source_doc_id")
    source = sources.get(source_id) if isinstance(source_id, str) else None
    errors.extend(_validate_source_ids(rule, sources))
    errors.extend(_validate_evidence(rule, sources))
    if source is None:
        errors.append(_error("unknown_source", "source_doc_id must identify a supplied source document."))
        return errors
    if rule.get("source_url") != getattr(source, "url", None):
        errors.append(_error("source_url_mismatch", "source_url does not match source_doc_id's recorded URL."))
    if _valid_retrieved_at(rule.get("retrieved_at")) and not _same_retrieval_time(
        rule["retrieved_at"], getattr(source, "retrieved_at", None)
    ):
        errors.append(_error("retrieved_at_mismatch", "retrieved_at does not correspond to the source document's capture time."))

    quote = rule.get("quoted_span")
    if not isinstance(quote, str) or len(quote) < 20:
        errors.append(_error("quote_too_short", "quoted_span must contain at least 20 characters of source text."))
    else:
        text = getattr(source, "text", None)
        location = _locate_body_quote(text, quote)
        if location is None:
            if locate_quote(text, quote) is not None:
                errors.append(_error("header_quote", "quoted_span must not quote SOURCE/RETRIEVED capture headers."))
            else:
                errors.append(_error("quote_not_found", "quoted_span was not found in the source, even allowing whitespace differences."))
        elif len(location["quote"]) < 20:
            errors.append(_error("quote_too_short", "The matched source span is shorter than 20 characters; whitespace padding is not evidence."))
    return errors


def validate_export(payload: Any, schema: dict, sources: dict) -> Dict[str, Any]:
    """Validate the template wrapper, all records, IDs, and rule references."""
    errors = []  # type: List[Dict[str, str]]
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        errors.append(_error("export_shape", 'The export must be an object containing a "rules" array.'))
        return {"valid": False, "error_count": len(errors), "errors": errors, "rule_count": 0}
    rules = payload["rules"]
    ids = {}  # type: Dict[str, int]
    for index, rule in enumerate(rules):
        for issue in validate_rule(rule, schema, sources):
            errors.append(_error(issue["code"], "rules[{}]: {}".format(index, issue["message"])))
        if not isinstance(rule, dict):
            continue
        rule_id = rule.get("team_rule_id")
        if isinstance(rule_id, str) and rule_id.strip():
            if rule_id in ids:
                errors.append(_error("duplicate_rule_id", "rules[{}].team_rule_id duplicates rules[{}]: {}".format(index, ids[rule_id], rule_id)))
            else:
                ids[rule_id] = index
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or not isinstance(rule.get("overrides", []), list):
            continue
        for target in rule.get("overrides", []):
            if isinstance(target, str) and target not in ids:
                errors.append(_error("unknown_override", "rules[{}].overrides references an absent team_rule_id: {}".format(index, target)))
    return {"valid": not errors, "error_count": len(errors), "errors": errors, "rule_count": len(rules)}
