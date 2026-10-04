"""Stable short submission IDs without changing evidence or compiled identity."""

from copy import deepcopy
import re


def validate_registry(registry):
    if registry.get("schema_version") != "1.0":
        raise ValueError("Unsupported submission ID registry version")
    mapping = registry.get("internal_to_submission")
    if not isinstance(mapping, dict):
        raise ValueError("Submission ID registry requires internal_to_submission")
    if not all(isinstance(key, str) and key for key in mapping):
        raise ValueError("Internal rule IDs must be nonempty strings")
    if not all(isinstance(value, str) and re.fullmatch(r"r-[0-9]{4,}", value)
               and value == "r-{:04d}".format(int(value[2:])) and int(value[2:]) > 0
               for value in mapping.values()):
        raise ValueError("Submission IDs must be positive canonical decimal IDs, e.g. r-0001")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Duplicate submission rule IDs")
    return mapping


def extend_registry(rules, previous=None):
    """Allocate once in stable internal-ID order; never recycle retired numbers."""
    registry = deepcopy(previous) if previous is not None else {
        "schema_version": "1.0", "internal_to_submission": {}}
    mapping = validate_registry(registry)
    identifiers = [rule["team_rule_id"] for rule in rules]
    if any(not isinstance(key, str) or not key for key in identifiers):
        raise ValueError("Rules require nonempty string identifiers")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate internal rule IDs")
    next_number = max((int(value[2:]) for value in mapping.values()), default=0) + 1
    for key in sorted(set(identifiers) - set(mapping)):
        mapping[key] = "r-{:04d}".format(next_number)
        next_number += 1
    return registry


def export_with_short_ids(rules, lookups, registry):
    """Remap current rule links only; preserve quotes and historical provenance."""
    mapping = validate_registry(registry)
    current = {rule["team_rule_id"] for rule in rules}
    if len(current) != len(rules) or not current <= set(mapping):
        raise ValueError("Submission mapping must cover every unique rule")

    def reference(key):
        if key not in current:
            raise ValueError("Dangling current-rule reference: " + str(key))
        return mapping[key]

    token = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(key) for key in sorted(current)) + r")(?![\w-])") if current else None

    exported_rules = deepcopy(rules)
    for rule in exported_rules:
        rule["team_rule_id"] = reference(rule["team_rule_id"])
        if "overrides" in rule:
            rule["overrides"] = [reference(key) for key in rule["overrides"]]
        if token is not None and isinstance(rule.get("interaction"), str):
            rule["interaction"] = token.sub(lambda match: mapping[match.group()], rule["interaction"])
        for interaction in rule.get("interactions", []):
            for field in ("from_rule_id", "to_rule_id"):
                if field in interaction:
                    interaction[field] = reference(interaction[field])
        # Historical links may name retired inputs that have no current rule.
        for interaction in rule.get("historical_interactions", []):
            for field in ("from_rule_id", "to_rule_id"):
                if interaction.get(field) in current:
                    interaction[field] = mapping[interaction[field]]

    exported_lookups = deepcopy(lookups)
    for entries in exported_lookups["lookups"].values():
        for entry in entries:
            entry["team_rule_id"] = reference(entry["team_rule_id"])
    selected = {key: mapping[key] for key in sorted(current)}
    crosswalk = {"schema_version": "1.0", "internal_to_submission": selected,
                 "submission_to_internal": {value: key for key, value in selected.items()},
                 "notes": [
                     "Submission IDs are team-local decimal labels, not organizer canonical rule numbers.",
                     "rules.json and lookups.json share submission IDs; evaluation_audit.json and compiled plans use internal IDs.",
                     "Case fact keys in explanations and historical input/source provenance retain internal IDs."]}
    return {"rules.json": {"rules": exported_rules}, "lookups.json": exported_lookups,
            "rule_id_map.json": crosswalk}
