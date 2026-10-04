"""Attach supplied address-data explanations to decisive unresolved inputs.

The reviewed manifest links exact executable fact keys to explanation fields.
It never binds values, changes scopes, or changes applicability decisions.
"""

from copy import deepcopy
import json
from pathlib import Path


_GROUPS = {
    "year_built": {
        "status_field": "year_built_validation_status",
        "explanation_field": "year_built_explanation",
        "label": "construction timing",
        "relationship": "construction_timing",
        "scope_note": (
            "The construction year is relevant background only. The rule's exact "
            "construction date, completion date, and subject scope still require "
            "verification; no date or certificate-of-occupancy date is inferred."
        ),
    },
    "units": {
        "status_field": "unit_validation_status",
        "explanation_field": "unit_validation_explanation",
        "label": "property unit count",
        "relationship": "property_unit_count",
        "scope_note": (
            "The source-reported count is relevant background only. Counting scope "
            "and the relevant observation time still require separate verification; "
            "it is not substituted for a building, premises, rental, subset, or "
            "owner-portfolio count."
        ),
    },
}

# These pilot facts predate per-plan fact definitions. Their fixed descriptions
# preserve the executable scope reviewed in the immutable plan snapshot.
_PILOT_DEFINITIONS = {
    "property.building_unit_count": {
        "description": "Number of units in the building containing the subject dwelling.",
        "subject_scope": "property_unit", "data_type": "number",
    },
    "property.building_rental_unit_count": {
        "description": "Number of rental units in the building containing the subject dwelling.",
        "subject_scope": "property_unit", "data_type": "number",
    },
    "property.two_units_in_single_structure": {
        "description": "Whether the two dwelling units are in a single structure.",
        "subject_scope": "property_unit", "data_type": "boolean",
    },
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "Duplicate address-field dependency key: " + key)
        value[key] = item
    return value


def _used_fact_keys(plan):
    keys = set()

    def visit(node):
        if isinstance(node, dict):
            for name in ("fact", "known"):
                if isinstance(node.get(name), str):
                    keys.add(node[name])
            comparison = node.get("date_compare")
            if isinstance(comparison, dict):
                keys.update(comparison[name] for name in ("left", "right"))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(plan.get("branches", []))
    return keys


def load_field_dependencies(path, rules_hash, plans_hash, plans):
    """Validate snapshot-bound metadata and return an exact-fact-key mapping.

    ``plans`` is the evaluator's validated rule-ID-to-plan mapping. Only these
    already validated plans may be used to verify the manifest definitions.
    """
    manifest = json.loads(Path(path).read_text(encoding="utf-8"),
                          object_pairs_hook=_unique_object)
    _require(manifest.get("schema_version") == "1.0", "Unsupported address-field dependency schema")
    _require(manifest.get("rules_sha256") == rules_hash,
             "Address-field dependencies refer to another rule snapshot")
    _require(manifest.get("plans_sha256") == plans_hash,
             "Address-field dependencies refer to another plan snapshot")
    _require(isinstance(plans, dict), "Address-field dependencies require validated plans by rule ID")
    dependencies = manifest.get("dependencies")
    _require(isinstance(dependencies, dict) and bool(dependencies), "Missing address-field dependencies")
    definitions = {}
    used_by = {}
    for rule_id, plan in plans.items():
        _require(rule_id == plan["team_rule_id"], "Plan dictionary rule ID mismatch")
        for key in _used_fact_keys(plan):
            used_by.setdefault(key, set()).add(rule_id)
        for item in plan.get("fact_definitions", []):
            if item["key"] not in dependencies:
                continue
            definition = {name: item[name] for name in ("description", "subject_scope", "data_type")}
            if item["key"] in definitions:
                _require(definitions[item["key"]] == definition,
                         "Inconsistent shared fact definition: " + item["key"])
            definitions[item["key"]] = definition
    expected_fields = {"input_group", "description", "subject_scope", "data_type", "rule_ids"}
    for key, item in dependencies.items():
        _require(isinstance(item, dict) and set(item) == expected_fields,
                 "Invalid address-field dependency fields: " + key)
        _require(item["input_group"] in _GROUPS, "Unknown address input group: " + key)
        _require(key in used_by, "Dependency fact is not used by executable plans: " + key)
        definition = definitions.get(key, _PILOT_DEFINITIONS.get(key))
        _require(definition is not None, "Dependency fact lacks a reviewed definition: " + key)
        _require(all(item[name] == definition[name] for name in
                     ("description", "subject_scope", "data_type")),
                 "Dependency definition differs from reviewed plan: " + key)
        _require(isinstance(item["rule_ids"], list) and
                 len(item["rule_ids"]) == len(set(item["rule_ids"])) and
                 set(item["rule_ids"]) == used_by[key],
                 "Dependency rule membership differs from executable plans: " + key)
    return dependencies


def rule_field_diagnostics(record, plan, facts, address_row, dependencies):
    """Return at most one supplied-data note per relevant unresolved input group.

    Root decisive missing facts already account for coverage/event mode and
    three-valued AND/OR short-circuiting. Branch trace gaps are not expanded.
    """
    if plan is None or record.get("result") is None:
        return []
    _require(record["team_rule_id"] == plan["team_rule_id"], "Diagnostic rule/plan ID mismatch")
    groups = {}
    for key in sorted(set(record.get("missing_facts", []))):
        dependency = dependencies.get(key)
        if dependency is None or record["team_rule_id"] not in dependency["rule_ids"]:
            continue
        if facts.get(key, {}).get("status") == "known":
            # A supplied case observation supersedes the CSV background issue.
            continue
        group = dependency["input_group"]
        config = _GROUPS[group]
        if address_row.get(config["status_field"]) == "verified":
            continue
        groups.setdefault(group, []).append({
            "key": key,
            "description": dependency["description"],
            "subject_scope": dependency["subject_scope"],
            "relationship": config["relationship"],
            "scope_note": config["scope_note"],
        })
    notes = []
    for group in _GROUPS:
        affected = groups.get(group)
        if not affected:
            continue
        config = _GROUPS[group]
        raw_status = address_row.get(config["status_field"])
        raw_explanation = address_row.get(config["explanation_field"])
        # Preserve an existing source string exactly, including whitespace.
        supplied = raw_explanation if isinstance(raw_explanation, str) else None
        reason = (supplied if supplied and supplied.strip() else
                  "No explanation was supplied in this address field.")
        descriptions = "; ".join(
            "{} [scope: {}]".format(item["description"], item["subject_scope"].replace("_", " "))
            for item in affected)
        explanation = (
            "Address input issue for {label}: {status_field}={status}. "
            "Supplied {explanation_field}: {reason} "
            "Related unresolved rule inputs (alternative conditions may use different inputs): "
            "{descriptions} {scope_note}"
        ).format(label=config["label"], status_field=config["status_field"],
                 status=raw_status or "not supplied", explanation_field=config["explanation_field"],
                 reason=reason, descriptions=descriptions, scope_note=config["scope_note"])
        notes.append({
            "input_group": group,
            "status_field": config["status_field"],
            "status": raw_status,
            "explanation_field": config["explanation_field"],
            "source_explanation": supplied,
            "affected_facts": deepcopy(affected),
            "explanation": explanation,
        })
    return notes
