"""Apply explicit source-bound phase reviews without discarding predicates."""

from copy import deepcopy
from pathlib import Path

from .evaluator import canonical_hash, file_hash
from .eval_facts import NORMALIZED_PROPERTY_USE_VALUES


def apply_temporal_reviews(bundle, review, rules):
    """Replace explicitly reviewed computational gaps, preserving legal gaps."""
    result = deepcopy(bundle)
    originals = {rule["team_rule_id"]: rule for rule in rules}
    plans = {plan["team_rule_id"]: plan for plan in result["plans"]}
    changed = []
    for item in review["reviews"]:
        rid = item["team_rule_id"]
        if rid not in plans:
            continue
        if canonical_hash(originals[rid]) != item["source_rule_sha256"]:
            raise ValueError("Temporal review source changed: " + rid)
        plan = plans[rid]
        digest = canonical_hash(item)
        if plan.get("temporal_review", {}).get("review_sha256") == digest:
            continue
        if plan.get("temporal_review"):
            raise ValueError("Incompatible temporal review: " + rid)
        previous = canonical_hash(plan)
        replacements = {r["reason"]: r["expression"] for r in item["replacements"]}
        matched = set()

        def convert(node):
            if isinstance(node, dict):
                reason = node.get("unknown", {}).get("reason")
                if reason in replacements:
                    matched.add(reason)
                    return deepcopy(replacements[reason])
                return {key: convert(value) for key, value in node.items()}
            if isinstance(node, list):
                return [convert(value) for value in node]
            return node

        plan["branches"] = convert(plan["branches"])
        if matched != set(replacements):
            raise ValueError("Temporal review no longer matches compiled expressions: " + rid)
        for branch in plan["branches"]:
            branch["source_gates"] = [g for g in branch["source_gates"]
                                      if g["id"] not in item["remove_duplicate_gate_ids"]]
            for gate_review in item.get("gate_predicates", []):
                for gate in list(branch["source_gates"]):
                    if gate["id"] != gate_review["id"]:
                        continue
                    if gate["stage"] not in {"coverage", "event"}:
                        raise ValueError("Temporal gate must govern coverage or event")
                    condition = deepcopy(gate_review["expression"])
                    if gate["when"] is not None:
                        condition = {"any": [{"not": gate["when"]}, condition]}
                    target = "coverage" if gate["stage"] == "coverage" else "trigger"
                    branch[target] = ({"all": [branch[target], condition]}
                                      if branch[target] is not None else condition)
                    branch["source_gates"].remove(gate)
        for fact in item["added_facts"]:
            result["fact_scopes"][fact["key"]] = fact["subject_scope"]
            plan.setdefault("fact_definitions", []).append(deepcopy(fact))
        plan["temporal_review"] = {"review_sha256": digest,
                                    "pre_review_plan_sha256": previous,
                                    "reason": item["reason"]}
        changed.append(rid)
    result["compilation"]["temporal_review_manifest_sha256"] = canonical_hash(review)
    result["compilation"]["temporal_review_code_sha256"] = file_hash(Path(__file__))
    result["compilation"]["temporal_review_rule_ids"] = sorted(
        p["team_rule_id"] for p in result["plans"] if p.get("temporal_review"))
    return result, changed



def normalize_construction_year_comparisons(bundle):
    """Translate calendar-year thresholds to the binder's full-date domain."""
    result = deepcopy(bundle)
    changed = []
    for plan in result["plans"]:
        conversions = []

        def convert(node):
            if isinstance(node, dict):
                if node.get("fact") == "property.construction_date" and type(node.get("value")) is int:
                    year, op = node["value"], node["op"]
                    if not 1000 <= year <= 9999:
                        raise ValueError("Construction calendar-year threshold requires review: " + plan["team_rule_id"])
                    first, last = "{:04d}-01-01".format(year), "{:04d}-12-31".format(year)
                    if op in {"lt", "ge"}:
                        updated = {**node, "value": first}
                    elif op in {"gt", "le"}:
                        updated = {**node, "value": last}
                    elif op in {"eq", "ne"}:
                        interval = {"all": [{"fact": node["fact"], "op": "ge", "value": first},
                                            {"fact": node["fact"], "op": "le", "value": last}]}
                        updated = interval if op == "eq" else {"not": interval}
                    else:
                        raise ValueError("Unsupported construction-year comparison")
                    conversions.append({"before": deepcopy(node), "after": deepcopy(updated)})
                    return updated
                return {key: convert(value) for key, value in node.items()}
            if isinstance(node, list):
                return [convert(value) for value in node]
            return node

        plan["branches"] = convert(plan["branches"])
        if conversions:
            for fact in plan.get("fact_definitions", []):
                if fact["key"] == "property.construction_date":
                    fact["data_type"] = "date"
                    fact["description"] = "Construction calendar-date interval derived from the verified construction year; not certificate-of-occupancy issuance."
            plan["construction_year_binding_review"] = conversions
            changed.append(plan["team_rule_id"])
    result["compilation"]["construction_year_binding_review_rule_ids"] = sorted(
        plan["team_rule_id"] for plan in result["plans"] if plan.get("construction_year_binding_review"))
    return result, changed


def isolate_property_type_domains(bundle):
    """Keep assessor-use categories separate from statutory unit-type enums.

    No legal classification is inferred. A rule-specific fact can subsequently
    establish the relevant type with its own evidence and declared vocabulary.
    """
    result = deepcopy(bundle)
    changed = []
    for plan in result["plans"]:
        key = "case." + plan["team_rule_id"] + ".legal_property_type"
        comparisons = []

        def visit(node):
            if isinstance(node, dict):
                if node.get("fact") == "property.use":
                    values = node["value"] if isinstance(node["value"], list) else [node["value"]]
                    if any(value not in NORMALIZED_PROPERTY_USE_VALUES for value in values):
                        comparisons.append({"op": node["op"], "value": deepcopy(node["value"])})
                        node["fact"] = key
                for child in node.values():
                    visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)

        visit(plan["branches"])
        if not comparisons:
            continue
        scope = "subject_unit_or_property_legal_type_for_this_rule"
        if key in result["fact_scopes"] and result["fact_scopes"][key] != scope:
            raise ValueError("Legal property-type fact collides with an existing scope")
        result["fact_scopes"][key] = scope
        plan.setdefault("fact_definitions", []).append({
            "key": key, "subject_scope": scope, "data_type": "string",
            "description": "Evidence-backed property/unit type in this rule's stated legal vocabulary. "
                           "A normalized assessor label such as multifamily_residential does not establish "
                           "or disprove a particular apartment, condominium, duplex, ADU, or other legal subtype.",
            "comparisons": comparisons})
        plan["property_type_binding_review"] = {
            "original_key": "property.use", "new_key": key,
            "reason": "Normalized address-use categories and statutory legal-type enums are different domains; no negative classification may be inferred from unequal labels."}
        changed.append(plan["team_rule_id"])
    result["compilation"]["property_type_binding_review_rule_ids"] = sorted(
        plan["team_rule_id"] for plan in result["plans"] if plan.get("property_type_binding_review"))
    return result, changed


def apply_coverage_reviews(bundle, review, rules):
    """Separate reviewed conditional coverage from an actual transaction.

    The manifest selects source-reviewed duties; this function does not infer
    legal scope from keywords. Original coverage is conjoined with the trigger,
    while original exclusions and source gates remain on the event. Thus the
    actual-event result is preserved for a residential rental query.
    """
    if review.get("schema_version") != "1.0" or review.get("as_of") not in bundle["supported_as_of"]:
        raise ValueError("Coverage review version/date differs from plan bundle")
    result = deepcopy(bundle)
    by_id = {rule["team_rule_id"]: rule for rule in rules}
    plans = {plan["team_rule_id"]: plan for plan in result["plans"]}
    applied = []
    for item in review["reviews"]:
        rid = item["team_rule_id"]
        if rid not in plans:
            continue
        if canonical_hash(by_id[rid]) != item["source_rule_sha256"]:
            raise ValueError("Coverage review source changed: " + rid)
        if item["coverage_criterion_id"] not in {c["id"] for c in by_id[rid]["applicability"]["criteria"]}:
            raise ValueError("Coverage review references an absent source criterion")
        plan = plans[rid]
        review_hash = canonical_hash(item)
        if plan.get("coverage_mode_review", {}).get("review_sha256") == review_hash:
            continue
        if plan.get("coverage_mode_review"):
            raise ValueError("Plan already has an incompatible coverage review")
        previous = canonical_hash(plan)
        for branch in plan["branches"]:
            original = deepcopy(branch["coverage"])
            branch["coverage"] = deepcopy(review["coverage"])
            if branch["trigger"] is None:
                branch["trigger"] = original
            else:
                branch["trigger"] = {"all": [original, branch["trigger"]]}
            for exclusion in branch["exclusions"]:
                if exclusion["stage"] == "coverage":
                    exclusion["stage"] = "event"
            for gate in branch["source_gates"]:
                if gate["stage"] == "coverage":
                    gate["stage"] = "event"
            branch["retained_conditions"].append(
                "Conditional residential-rental duty. The payment definition, actual event, "
                "event-date version, procedural prerequisites and exceptions remain necessary "
                "for the event result; coverage alone establishes none of them. Event context: " + item["event_description"])
        plan["coverage_mode_review"] = {
            "review_sha256": review_hash, "pre_review_plan_sha256": previous,
            "coverage_criterion_id": item["coverage_criterion_id"], "reason": item["reason"],
            "effect": "Original applicability predicates retained in the event dimension; governing conditional coverage separated."}
        applied.append(rid)
    result["compilation"]["coverage_mode_review_manifest_sha256"] = canonical_hash(review)
    result["compilation"]["coverage_mode_review_code_sha256"] = file_hash(Path(__file__))
    result["compilation"]["coverage_mode_review_rule_ids"] = sorted(
        plan["team_rule_id"] for plan in result["plans"] if plan.get("coverage_mode_review"))
    return result, applied
