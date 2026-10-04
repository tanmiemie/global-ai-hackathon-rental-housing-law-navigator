"""Bind supplied address observations without inferring legal eligibility.

This module does not fetch sources or resolve conflicting evidence.  A verified
source count remains ``source.reported_units``: its building, parcel, rentable
unit, and owner-portfolio meanings are deliberately not interchangeable.
"""

from copy import deepcopy
import csv
from datetime import date, datetime
import hashlib
import io
import json
from pathlib import Path
import re


DEFAULT_AS_OF = "2026-10-01"
NORMALIZED_PROPERTY_USE_VALUES = {
    "multifamily_residential", "subsidized_multifamily", "mixed_use_multifamily",
    "specialized_residential", "residential_tic",
}
# Reviewed meanings from the supplied property-use/code evidence. This finite
# mapping establishes the residential component of the residential-rental query,
# never programme eligibility, tenancy, licensing, ownership tests or exemptions.
# Keep it separate from the accepted-value set so new labels require review.
RESIDENTIAL_USE_SCOPE_NOTES = {
    "multifamily_residential": None,
    "subsidized_multifamily": (
        "Property scope: the verified subsidized_multifamily classification supports residential use "
        "for this residential-rental query. Current subsidy-program participation, statutory subsidy "
        "qualifications, an actual tenancy, and any exemption remain separate facts."
    ),
    "mixed_use_multifamily": (
        "Property scope: the verified mixed_use_multifamily classification establishes a residential "
        "component. This lookup concerns that residential rental component, not the commercial premises. "
        "The classification alone does not establish a particular lease, tenant, or rental event."
    ),
    "specialized_residential": (
        "Property scope: the verified specialized_residential classification supports residential use "
        "for this residential-rental query. An ordinary tenancy, the form or licensing of a care "
        "institution, and any institutional exemption remain separate facts."
    ),
    "residential_tic": (
        "Property scope: the verified residential_tic classification supports residential use "
        "for this residential-rental query. Tenancy in common is an ownership classification; "
        "it does not establish a tenant, lease, rental activity, or statutory ownership exemption."
    ),
}
# A source rule ID may be one component of an otherwise ordinary fact path.
_FACT_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.(?:r-[a-f0-9]{20}|[a-z][a-z0-9_]*))+$")
_MISSING = {"", "unknown", "none", "null", "not provided"}
_OVERRIDE_KEYS = {"status", "value", "min", "max", "reason", "evidence",
                  "subject_scope", "observed_at"}


def _query_date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("{} must be an ISO calendar date.".format(label))
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("{} is not a valid calendar date.".format(label)) from exc


def _timestamp_date(value, label):
    """Validate an observation/retrieval date without converting it to validity."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be an ISO date, timestamp, or null.".format(label))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return _query_date(value, label)
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value):
        raise ValueError("{} must be an ISO date or timestamp.".format(label))
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value).date()
    except ValueError as exc:
        raise ValueError("{} is not a valid ISO timestamp.".format(label)) from exc


def _present(value):
    return isinstance(value, str) and value.strip().lower() not in _MISSING


def _observation_status(value):
    if value in {"conflict", "conflicting"}:
        return "conflicting"
    return "known" if value == "verified" else "unknown"


def _evidence(path, row, fields, retrieval_field=None):
    evidence = {"path": str(path), "row_id": row["address_id"],
                "fields": list(fields),
                "retrieved_at": (row.get(retrieval_field) or None) if retrieval_field else None}
    return [evidence]


def _record(status, scope, reason, evidence, observed_at=None, **values):
    result = {"status": status, "reason": reason, "evidence": evidence,
              "subject_scope": scope, "observed_at": observed_at}
    result.update(values)
    return result


def _integer(value, label):
    if not _present(value):
        return None
    if not re.fullmatch(r"\d+", value):
        raise ValueError("{} must be a nonnegative integer.".format(label))
    return int(value)


def _bind_csv(row, path, warnings):
    facts = {}
    jurisdiction_evidence = _evidence(path, row, [
        "legal_state", "legal_city", "incorporation_status", "jurisdiction_status",
        "jurisdiction_evidence_ref", "jurisdiction_notes"], "jurisdiction_retrieved_at")
    has_jurisdiction_evidence = _present(row.get("jurisdiction_evidence_ref", ""))
    for component in ("state", "city"):
        value = row.get("legal_" + component, "")
        status = _observation_status(row.get("jurisdiction_status"))
        if component == "state" and status == "unknown":
            # Optional explicit component verification can support a partial row.
            if row.get("state_validation_status") == "verified":
                status = "known"
                jurisdiction_evidence[0]["fields"].append("state_validation_status")
        if status == "known" and (not _present(value) or not has_jurisdiction_evidence):
            status = "unknown"
        reason = ("Verified legal {} with a supplied jurisdiction evidence reference.".format(component)
                  if status == "known" else
                  "Legal {} is not established by component verification and evidence; postal labels are not substituted.".format(component))
        if status == "conflicting":
            reason = "Jurisdiction evidence is conflicting; no legal {} is selected.".format(component)
        facts["jurisdiction." + component] = _record(
            status, "address_jurisdiction", reason, deepcopy(jurisdiction_evidence),
            **({"value": value} if status == "known" else {}))

    use_status = _observation_status(row.get("property_use_validation_status"))
    use = row.get("resolved_property_use", "")
    if use_status == "known" and not _present(use):
        use_status = "unknown"
    use_evidence = _evidence(path, row, [
        "resolved_property_use", "property_use_validation_status", "property_use_source",
        "property_use_explanation", "original_use_code", "original_use_description"],
        "original_source_retrieved_at")
    facts["property.use"] = _record(
        use_status, "address_property", row.get("property_use_explanation") or
        "The supplied normalized property-use observation is retained without deriving legal exemptions.",
        use_evidence, **({"value": use} if use_status == "known" else {}))
    residential_status = (use_status if use_status != "known" or use in RESIDENTIAL_USE_SCOPE_NOTES
                          else "unknown")
    if residential_status == "known":
        residential_reason = (
            "Verified multifamily residential classification; this does not establish a particular lease or occupancy."
            if use == "multifamily_residential" else
            "Verified {} classification supports a residential component; the original subtype is retained and legal eligibility is evaluated separately.".format(use))
    else:
        residential_reason = "Residential use is not established by a verified, supported residential classification."
    facts["property.residential_use"] = _record(
        residential_status, "address_property", residential_reason,
        deepcopy(use_evidence), **({"value": True} if residential_status == "known" else {}))
    if residential_status == "known" and RESIDENTIAL_USE_SCOPE_NOTES[use]:
        facts["property.residential_use"]["scope_note"] = RESIDENTIAL_USE_SCOPE_NOTES[use]

    year_status = _observation_status(row.get("year_built_validation_status"))
    year = row.get("resolved_year_built", "")
    year_values = {}
    if year_status == "known":
        if not _present(year):
            year_status = "unknown"
        else:
            if not re.fullmatch(r"\d{4}", year) or not 1 <= int(year) <= 9999:
                raise ValueError("Verified resolved_year_built must be a valid four-digit year.")
            year_values = {"min": year + "-01-01", "max": year + "-12-31"}
    facts["property.construction_date"] = _record(
        year_status, "address_property",
        (row.get("year_built_explanation") or "No verified construction year was supplied.") +
        " A year supplies calendar-year bounds, not an exact construction day or certificate-of-occupancy date.",
        _evidence(path, row, ["resolved_year_built", "year_built_validation_status",
                            "year_built_source", "year_built_explanation", "year_built_conflict_log"],
                  "original_source_retrieved_at"), **year_values)

    units_status = _observation_status(row.get("unit_validation_status"))
    unit_values = {}
    if units_status == "known":
        value_type = row.get("unit_value_type")
        lower = _integer(row.get("resolved_units_min", ""), "resolved_units_min")
        upper = _integer(row.get("resolved_units_max", ""), "resolved_units_max")
        if value_type == "exact":
            value = _integer(row.get("resolved_units", ""), "resolved_units")
            if value is None or any(bound is not None and bound != value for bound in (lower, upper)):
                raise ValueError("Exact reported units require a value consistent with their bounds.")
            unit_values = {"value": value, "min": value, "max": value}
        elif value_type == "range":
            if lower is None and upper is None:
                raise ValueError("Verified reported-unit range requires at least one bound.")
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("Reported-unit minimum exceeds its maximum.")
            unit_values = {"min": lower, "max": upper}
        else:
            units_status = "unknown"
    facts["source.reported_units"] = _record(
        units_status, "source_reported_count",
        (row.get("unit_validation_explanation") or "No verified reported-unit observation was supplied.") +
        " Counting scope remains source-specific; this is not automatically a building total, rentable-unit count, or owner portfolio.",
        _evidence(path, row, ["resolved_units", "resolved_units_min", "resolved_units_max",
                            "unit_value_type", "unit_validation_status", "unit_source",
                            "unit_validation_explanation", "original_source_dataset"],
                  "original_source_retrieved_at"), **unit_values)
    if row.get("original_source_dataset") == "LA County eGIS parcels":
        warnings.append("LA County reported units can originate from the Units1 source field; no building, parcel-total, or owner-holdings count is inferred.")
    facts["context.residential_rental_lookup"] = _record(
        "known", "query_context",
        "Explicit residential-rental lookup premise; not evidence of a lease, tenant, deposit, or transaction.",
        [{"path": "query_context", "row_id": row["address_id"],
          "fields": ["context.residential_rental_lookup"], "retrieved_at": None}],
        value=True, assumed_query_context=True)
    return facts


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: {}".format(key))
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON value: {}".format(value))


def _validate_bounds(record, label):
    has_value = "value" in record
    has_bounds = "min" in record or "max" in record
    if record["status"] != "known":
        if has_value or has_bounds:
            raise ValueError("{}: unknown/conflicting facts must not expose a decisive value or bounds.".format(label))
        return
    if not has_value and not has_bounds:
        raise ValueError("{}: known fact requires a value or bounds.".format(label))
    if has_value and (record["value"] is None or isinstance(record["value"], (dict, list)) or
                      isinstance(record["value"], str) and not record["value"].strip()):
        raise ValueError("{}: known value must be a non-null scalar.".format(label))
    if has_bounds:
        lower, upper = record.get("min"), record.get("max")
        bounded = [v for v in (lower, upper) if v is not None]
        if not bounded:
            raise ValueError("{}: interval requires at least one bound.".format(label))
        numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bounded)
        if not numeric:
            for bound in bounded:
                _query_date(bound, label + " date bound")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("{}: minimum exceeds maximum.".format(label))
        if has_value and (isinstance(record["value"], bool) or
                          any(record["value"] != bound for bound in bounded)):
            raise ValueError("{}: a value plus bounds must describe the same exact value.".format(label))


def _validate_override(key, record, address_id, as_of, warnings):
    if not isinstance(key, str) or not _FACT_KEY.fullmatch(key):
        raise ValueError("Override fact keys must be namespaced identifiers.")
    if not isinstance(record, dict) or set(record) - _OVERRIDE_KEYS:
        raise ValueError("{}: unsupported override record fields.".format(key))
    required = {"status", "reason", "evidence", "subject_scope", "observed_at"}
    if not required <= set(record):
        raise ValueError("{}: override requires status, reason, evidence, subject_scope, and observed_at.".format(key))
    if not isinstance(record["status"], str) or record["status"] not in {"known", "unknown", "conflicting"}:
        raise ValueError("{}: unsupported fact status.".format(key))
    for name in ("reason", "subject_scope"):
        if not isinstance(record[name], str) or not record[name].strip():
            raise ValueError("{}: {} must be nonempty text.".format(key, name))
    observed = _timestamp_date(record["observed_at"], key + " observed_at")
    if observed is not None and observed > as_of:
        if record["status"] == "known":
            raise ValueError("{}: an observation after as_of cannot establish a known historical fact.".format(key))
        warnings.append("Override {} was observed after the query date; its observation is not automatically a historical fact.".format(key))
    evidence = record["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("{}: override must have provenance evidence.".format(key))
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"path", "row_id", "fields", "retrieved_at"}:
            raise ValueError("{}: evidence requires path, row_id, fields, and retrieved_at.".format(key))
        if not isinstance(item["path"], str) or not item["path"].strip():
            raise ValueError("{}: evidence path must be nonempty.".format(key))
        if item["row_id"] != address_id:
            raise ValueError("{}: evidence row_id must match the selected address.".format(key))
        if not isinstance(item["fields"], list) or not item["fields"] or any(
                not isinstance(field, str) or not field.strip() for field in item["fields"]):
            raise ValueError("{}: evidence fields must name supporting fields.".format(key))
        retrieved = _timestamp_date(item["retrieved_at"], key + " retrieved_at")
        if retrieved is not None and retrieved > as_of:
            warnings.append("Override {} evidence was retrieved after the query date; retrieval is not the underlying event date.".format(key))
    _validate_bounds(record, key)
    if record["status"] == "known" and key.endswith(("_date", ".date")):
        for field in ("value", "min", "max"):
            if record.get(field) is not None:
                _query_date(record[field], key + " " + field)


def _payload(record):
    payload = {key: record[key] for key in ("status", "value", "min", "max", "subject_scope") if key in record}
    if "value" not in payload and payload.get("min") is not None and payload.get("min") == payload.get("max"):
        payload["value"] = payload["min"]
    if "value" in payload:
        # Equality in Python must not silently identify a boolean with 0 or 1.
        payload["value_type"] = type(payload["value"]).__name__
        payload.pop("min", None)
        payload.pop("max", None)
    return payload


def _apply_overrides(path, address_id, as_of, facts, warnings):
    raw = path.read_bytes()
    try:
        wrapper = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
        json.dumps(wrapper, allow_nan=False)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Invalid overrides JSON: {}".format(exc)) from exc
    if not isinstance(wrapper, dict) or set(wrapper) != {"address_id", "as_of", "facts"}:
        raise ValueError("Overrides require exactly address_id, as_of, and facts.")
    if wrapper["address_id"] != address_id:
        raise ValueError("Overrides address_id does not match the requested address.")
    if _query_date(wrapper["as_of"], "Override as_of") != as_of:
        raise ValueError("Overrides as_of does not match the requested query date.")
    if not isinstance(wrapper["facts"], dict):
        raise ValueError("Overrides facts must be an object.")
    for key, supplied in wrapper["facts"].items():
        _validate_override(key, supplied, address_id, as_of, warnings)
        previous = facts.get(key)
        if previous is not None:
            if previous["subject_scope"] != supplied["subject_scope"]:
                raise ValueError("{}: an override cannot change the existing fact's subject scope.".format(key))
            if previous["status"] == "known" and _payload(previous) != _payload(supplied):
                if supplied["status"] != "conflicting":
                    raise ValueError("{}: a different known observation requires an explicit conflicting record.".format(key))
            if previous["status"] == "conflicting" and supplied["status"] != "conflicting":
                raise ValueError("{}: conflicting observations cannot be silently resolved by an override.".format(key))
        replacement = deepcopy(supplied)
        if previous is not None:
            replacement["overridden_observations"] = [deepcopy(previous)]
            if previous.get("assumed_query_context"):
                replacement["assumed_query_context"] = True
            for evidence in previous["evidence"]:
                if evidence not in replacement["evidence"]:
                    replacement["evidence"].append(deepcopy(evidence))
        facts[key] = replacement
    return hashlib.sha256(raw).hexdigest()


def load_address_facts(addresses_path, address_id, as_of=DEFAULT_AS_OF, overrides_path=None,
                       skip_unverified_jurisdiction=False):
    """Return evidence-aware observations for one exact CSV address identifier.

    ``subject_scope`` is a string, intervals use ``min``/``max`` (null is an
    unbounded end), and a precise source count also has ``value``.  ``observed_at``
    stays null unless supplied as an observation date: retrieval and processing
    timestamps are not silently relabeled.  Override scope and provenance are
    explicit caller assertions; this binder never verifies remote documents.

    Overrides use ``{address_id, as_of, facts}`` and the same fact-record shape.
    A known observation cannot be changed or erased without an explicit
    conflicting record.  Previous observations and evidence are always retained.
    Invalid input or ambiguous address identity raises ``ValueError``.
    Lookup callers enable ``skip_unverified_jurisdiction`` to return only the
    original row and provenance when its jurisdiction status is not verified.
    Such rows never reach property binding or case overlays.
    """
    query_date = _query_date(as_of, "as_of")
    if not isinstance(address_id, str) or not address_id or address_id != address_id.strip():
        raise ValueError("address_id must be a nonempty exact identifier.")
    path = Path(addresses_path).resolve()
    raw = path.read_bytes()
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        if not reader.fieldnames or "address_id" not in reader.fieldnames:
            raise ValueError("Address CSV requires an address_id column.")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("Address CSV contains duplicate column names.")
        selected, seen = None, set()
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Address CSV row has an inconsistent column count.")
            row_id = row["address_id"]
            if not row_id or row_id != row_id.strip():
                raise ValueError("Address CSV contains an invalid address_id.")
            if row_id in seen:
                raise ValueError("Duplicate address_id in address CSV: {}".format(row_id))
            seen.add(row_id)
            if row_id == address_id:
                selected = row
    except UnicodeError as exc:
        raise ValueError("Address CSV must be UTF-8 text.") from exc
    if selected is None:
        raise ValueError("Address ID not found (no alias or typo correction applied): {}".format(address_id))
    hashes = {str(path): hashlib.sha256(raw).hexdigest()}
    if skip_unverified_jurisdiction and selected.get("jurisdiction_status") != "verified":
        warnings = []
        if overrides_path is not None:
            override_path = Path(overrides_path).resolve()
            hashes[str(override_path)] = hashlib.sha256(override_path.read_bytes()).hexdigest()
            warnings.append("Fact overlays were not applied: the CSV jurisdiction status is not verified.")
        return {"address_id": address_id, "address": selected, "facts": {},
                "warnings": warnings, "input_sha256": hashes}
    warnings = []
    if selected.get("legal_query_date") and selected["legal_query_date"] != as_of:
        _query_date(selected["legal_query_date"], "CSV legal_query_date")
        warnings.append("CSV legal_query_date {} differs from requested as_of {}; source dates are preserved.".format(selected["legal_query_date"], as_of))
    for field, value in selected.items():
        if value and (field.endswith("retrieved_at") or field.endswith("processed_at")):
            observed_date = _timestamp_date(value, "CSV " + field)
            if observed_date > query_date:
                warnings.append("CSV {}={} is later than as_of {}; retrieval/processing does not establish historical validity.".format(field, value, as_of))
    facts = _bind_csv(selected, path, warnings)
    if overrides_path is not None:
        override_path = Path(overrides_path).resolve()
        hashes[str(override_path)] = _apply_overrides(override_path, address_id, query_date, facts, warnings)
    return {"address_id": address_id, "address": selected, "facts": facts,
            "warnings": list(dict.fromkeys(warnings)), "input_sha256": hashes}
