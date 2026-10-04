"""Render saved applicability decisions without changing their legal predicates.

The organizer's lookup format has four entry fields. Citations and uncertainty
belong in ``explanation``; the detailed evaluation audit retains machine keys.
"""

import re
from functools import lru_cache


_RESULTS = {"applies", "unknown", "pending", "not_yet_effective", "superseded"}
_CITATION_FIELDS = ("citation", "source_doc_id", "source_url", "retrieved_at")
_SHARED_LABELS = {
    "jurisdiction.state": "verified legal state",
    "jurisdiction.city": "verified legal city",
    "property.construction_date": "property construction date (not a certificate-of-occupancy date)",
    "property.residential_use": "residential use of the property",
    "property.certificate_of_occupancy_date": "certificate-of-occupancy date",
    "property.building_unit_count": "unit count for the building",
    "property.building_rental_unit_count": "rental-unit count for the building",
}


def _words(value):
    return value.replace("_", " ").replace(".", " ")


@lru_cache(maxsize=4096)
def _fact_key_pattern(keys):
    return re.compile(r"(?<![\w.-])(?:" + "|".join(
        re.escape(key) for key in keys) + r")(?![\w-])")


def _fact_description(key, definitions, facts):
    definition = definitions.get(key, {})
    label = _SHARED_LABELS.get(key)
    if label is None:
        # Case keys include the internal rule ID, which is not useful to readers.
        label = _words(key.split(".", 2)[-1] if key.startswith("case.") else key)
    details = []
    if definition.get("description"):
        # A definition describes a proposition; it does not assert it is true.
        details.append("definition to establish: " + definition["description"])
    if definition.get("subject_scope"):
        details.append("scope: " + _words(definition["subject_scope"]))
    observation = facts.get(key, {})
    if observation.get("status") in {"unknown", "conflicting"} and observation.get("reason"):
        details.append("evidence issue: " + observation["reason"])
    return label + (" [" + "; ".join(details) + "]" if details else "")


def _readable_explanation(record, plan, facts):
    definitions = {item["key"]: item for item in (plan or {}).get("fact_definitions", [])}
    keys = set(definitions)
    # Include unresolved inputs in every branch, even where another OR branch
    # already establishes coverage. Those retained branch notes remain useful.
    def collect(node):
        if isinstance(node, dict):
            keys.update(node.get("missing_facts", []))
            keys.update(key for key in node.get("conflicts", []) if not key.startswith("source:"))
            for child in node.values():
                if isinstance(child, (dict, list)):
                    collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(record)
    text = record["explanation"].replace(
        "Decisive missing facts:",
        "Unresolved inputs for this branch (alternative conditions may use different inputs):")
    # Replace only complete fact keys, leaving dates, rule prose and source
    # questions intact. These descriptions are not presented as source quotes.
    if keys:
        token = _fact_key_pattern(tuple(sorted(keys, key=lambda key: (-len(key), key))))
        text = token.sub(lambda match: _fact_description(match.group(), definitions, facts), text)
    if re.search(r"case\.r-[0-9a-f]+\.", text):
        raise ValueError("Unrendered internal case fact key in lookup explanation")
    return text


def _unknown_reason(record, rule):
    causes = []
    if record.get("jurisdiction_evaluation", {}).get("value") == "unknown":
        causes.append("the address's legal jurisdiction is not established for " + rule["jurisdiction"])
    if record.get("compilation_gap"):
        causes.append("this candidate rule has no validated executable applicability plan")
    if any(not key.startswith("jurisdiction.") for key in record.get("missing_facts", [])):
        causes.append("the supplied facts do not resolve the relevant applicability conditions or exemptions")
    if record.get("source_questions"):
        causes.append("the legal-source questions listed below remain unresolved")
    conflicts = record.get("evaluation", {}).get("conflicts", [])
    if any(not key.startswith("source:") for key in conflicts):
        causes.append("conflicting fact observations prevent a conclusive decision")
    if any(key.startswith("source:") for key in conflicts):
        causes.append("conflicting legal sources prevent a conclusive decision")
    if not causes:
        raise ValueError("Unknown lookup result requires an explicit unresolved cause")
    return "Unknown because " + "; ".join(causes) + ". "


def format_lookup_entry(rule, record, plan=None, facts=None):
    """Require source attribution and explain uncertainty in the public record.

    This function is shared by fresh evaluation and rendering verified saved
    decisions. It never changes a result, conflict flag, condition or exemption.
    Citation metadata is copied exactly from the source rule, not synthesized.
    """
    if record.get("result") not in _RESULTS:
        raise ValueError("Only returned lookup decisions may be formatted")
    if record.get("team_rule_id") != rule.get("team_rule_id"):
        raise ValueError("Lookup decision and citation must refer to the same rule")
    for field in _CITATION_FIELDS:
        if not isinstance(rule.get(field), str) or not rule[field].strip():
            raise ValueError("Lookup citation requires nonempty rule field: " + field)
    if not isinstance(record.get("explanation"), str) or not record["explanation"].strip():
        raise ValueError("Lookup decision requires an explanation")
    explanation = _readable_explanation(record, plan, facts or {})
    if record["result"] == "unknown":
        explanation = (_unknown_reason(record, rule)
                       + "The branch descriptions below remain conditional on resolving this uncertainty. "
                       + explanation)
    for diagnostic in record.get("address_field_diagnostics", []):
        explanation += " " + diagnostic["explanation"]
    residential_scope = (facts or {}).get("property.residential_use", {}).get("scope_note")
    if residential_scope:
        explanation += " " + residential_scope
    citation = " Citation: {} [document {}; URL {}; retrieved {}].".format(
        *(rule[field] for field in _CITATION_FIELDS))
    if record["result"] == "unknown":
        citation += " This citation identifies the candidate rule; it does not establish applicability to this address."
    return {"team_rule_id": record["team_rule_id"], "result": record["result"],
            "explanation": explanation + citation, "conflict_flag": record["conflict_flag"]}
