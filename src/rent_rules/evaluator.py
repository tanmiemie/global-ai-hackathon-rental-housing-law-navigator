"""Deterministic, evidence-linked evaluation of versioned rule plans.

Natural-language rule records are never parsed as executable predicates here.
Uncompiled candidate rules remain visible with a distinct compilation-gap reason.
"""

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import time

from .eval_facts import load_address_facts, NORMALIZED_PROPERTY_USE_VALUES
from .eval_logic import evaluate_expression, validate_expression
from .lookup_output import format_lookup_entry
from .field_diagnostics import load_field_dependencies, rule_field_diagnostics


DEFAULT_DATE = "2026-10-01"
PLAN_STAGES = {"coverage", "event", "compliance", "calculation"}


def canonical_hash(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_bound_expression(expression):
    """Reject predicates that reinterpret a bound address field's type/domain."""
    validate_expression(expression)

    def visit(node):
        if not isinstance(node, dict):
            return
        key = node.get("fact")
        if key:
            values = node["value"] if node["op"] == "in" else [node["value"]]
            if key in {"property.residential_use", "context.residential_rental_lookup"}:
                require(all(type(value) is bool for value in values), "Bound Boolean fact compared with another type: " + key)
            elif key == "property.use":
                require(all(value in NORMALIZED_PROPERTY_USE_VALUES for value in values),
                        "Legal property types cannot be compared with normalized address-use labels")
            elif key == "property.construction_date":
                for value in values:
                    require(isinstance(value, str), "Construction date predicates require ISO dates, not numeric years")
                    require(date.fromisoformat(value).isoformat() == value, "Construction date predicate is not an ISO date")
        for operator in ("all", "any"):
            for child in node.get(operator, []):
                visit(child)
        if "not" in node:
            visit(node["not"])

    visit(expression)


def expression_fact_keys(expression):
    if expression is None:
        return set()
    if "date_compare" in expression:
        return {expression["date_compare"]["left"], expression["date_compare"]["right"]}
    if "fact" in expression:
        return {expression["fact"]}
    if "known" in expression:
        return {expression["known"]}
    if "not" in expression:
        return expression_fact_keys(expression["not"])
    return {key for operator in ("all", "any") for child in expression.get(operator, [])
            for key in expression_fact_keys(child)}


def plan_fact_keys(plan):
    keys = set()
    for branch in plan["branches"]:
        for expression in [branch["coverage"], branch["trigger"]] + [
                item["when"] for field in ("exclusions", "requirements", "modifiers", "source_gates")
                for item in branch[field]]:
            keys.update(expression_fact_keys(expression))
    return keys


def validate_fact_scopes(bundle, facts):
    scopes = bundle.get("fact_scopes", {})
    needed = {key for plan in bundle["plans"] for key in plan_fact_keys(plan)}
    require(needed <= set(scopes), "Plan fact keys require explicit subject scopes")
    for key in needed.intersection(facts):
        require(facts[key].get("subject_scope") == scopes[key],
                "Fact subject/counting scope mismatch for " + key)


def _literal(value, label):
    return {"value": value, "missing_facts": [], "source_questions": [],
            "conflicts": [], "trace": {"label": label, "value": value}}


def _combine(results, operator):
    """Compose results while retaining only dependencies that can affect the value."""
    if not results:
        return _literal("true" if operator == "all" else "false", "No conditions")
    decisive = "false" if operator == "all" else "true"
    selected = [item for item in results if item["value"] == decisive]
    if selected:
        value = decisive
    elif any(item["value"] == "unknown" for item in results):
        value = "unknown"
        selected = [item for item in results if item["value"] == "unknown"]
    else:
        value = "true" if operator == "all" else "false"
        selected = results
    output = {"value": value, "trace": {"operator": operator, "value": value,
                                          "children": [item["trace"] for item in results]}}
    for key in ("missing_facts", "source_questions", "conflicts"):
        output[key] = sorted({entry for item in selected for entry in item[key]})
    return output


def _negate(result):
    output = deepcopy(result)
    output["value"] = {"true": "false", "false": "true", "unknown": "unknown"}[result["value"]]
    output["trace"] = {"operator": "not", "value": output["value"], "child": result["trace"]}
    return output


def _gate(gate, facts):
    relevance = (evaluate_expression(gate["when"], facts) if gate["when"] is not None
                 else _literal("true", "Unconditional unresolved source gate"))
    if relevance["value"] == "false":
        result = _literal("true", "Source question is irrelevant to this branch")
    else:
        result = {"value": "unknown", "missing_facts": relevance["missing_facts"],
                  "source_questions": sorted(set(relevance["source_questions"] + [gate["reason"]])),
                  "conflicts": sorted(set(relevance["conflicts"] +
                                          (["source:" + gate["id"]] if gate["conflict"] else [])))}
    result["trace"] = {"source_gate_id": gate["id"], "value": result["value"],
                       "reason": gate["reason"], "relevance": relevance["trace"]}
    return result


def evaluate_branch(branch, facts):
    base = evaluate_expression(branch["coverage"], facts)
    exclusions = [{**item, "evaluation": evaluate_expression(item["when"], facts)}
                  for item in branch["exclusions"]]
    gates = [{**item, "evaluation": _gate(item, facts)} for item in branch["source_gates"]]
    coverage_exclusion = _combine([item["evaluation"] for item in exclusions
                                   if item["stage"] == "coverage"], "any")
    coverage = _combine([base, _negate(coverage_exclusion)] +
                        [item["evaluation"] for item in gates if item["stage"] == "coverage"], "all")
    trigger = (evaluate_expression(branch["trigger"], facts) if branch["trigger"] is not None
               else _literal("true", "Continuous duty; no separate event trigger"))
    event_exclusion = _combine([item["evaluation"] for item in exclusions
                                if item["stage"] == "event"], "any")
    event = _combine([coverage, trigger, _negate(event_exclusion)] +
                     [item["evaluation"] for item in gates if item["stage"] == "event"], "all")
    requirements = [{**item, "evaluation": evaluate_expression(item["when"], facts)}
                    for item in branch["requirements"]]
    requirement_result = _combine([item["evaluation"] for item in requirements], "all")
    compliance_gate = _combine([item["evaluation"] for item in gates
                                if item["stage"] == "compliance"], "all")
    if not requirements:
        compliance = "not_evaluated"
    elif event["value"] == "false":
        compliance = "not_triggered"
    elif event["value"] == "unknown" or compliance_gate["value"] != "true":
        compliance = "unknown"
    else:
        compliance = {"true": "compliant", "false": "noncompliant", "unknown": "unknown"}[
            requirement_result["value"]]
    modifiers = [{**item, "evaluation": evaluate_expression(item["when"], facts)}
                 for item in branch["modifiers"]]
    calculation_gate = _combine([item["evaluation"] for item in gates
                                 if item["stage"] == "calculation"], "all")
    resolved_effects = [item["effect"] for item in modifiers if item["evaluation"]["value"] == "true"
                        and calculation_gate["value"] == "true" and event["value"] == "true"]
    if base["value"] == "false":
        coverage_state = "out_of_scope"
    elif coverage_exclusion["value"] == "true":
        coverage_state = "exempt"
    else:
        coverage_state = {"true": "covered", "false": "out_of_scope", "unknown": "unknown"}[
            coverage["value"]]
    return {"branch_id": branch["branch_id"], "label": branch["label"], "duty": branch["duty"],
            "coverage_state": coverage_state, "coverage": coverage, "trigger": trigger,
            "event": event, "exclusions": exclusions, "source_gates": gates,
            "requirements": requirements, "requirement_result": requirement_result,
            "compliance": compliance, "compliance_gate": compliance_gate, "modifiers": modifiers,
            "calculation_gate": calculation_gate, "resolved_effects": resolved_effects,
            "retained_conditions": branch["retained_conditions"],
            "evidence_refs": branch["evidence_refs"]}


def _jurisdiction_expression(jurisdiction):
    parts = [part.strip() for part in jurisdiction.rsplit(",", 1)]
    if len(parts) == 1:
        return {"fact": "jurisdiction.state", "op": "eq", "value": parts[0]}
    return {"all": [{"fact": "jurisdiction.state", "op": "eq", "value": parts[1]},
                    {"fact": "jurisdiction.city", "op": "eq", "value": parts[0]}]}


def evaluate_rule(rule, plan, facts, mode="coverage"):
    require(mode in {"coverage", "event"}, "Unknown evaluation mode")
    jurisdiction = evaluate_expression(_jurisdiction_expression(rule["jurisdiction"]), facts)
    common = {"team_rule_id": rule["team_rule_id"], "title": rule["title"],
              "jurisdiction": rule["jurisdiction"], "category": rule["category"],
              "jurisdiction_evaluation": jurisdiction, "mode": mode, "branches": [],
              "compiled": plan is not None, "source_conflict_flag": rule.get("conflict_flag", False)}
    if jurisdiction["value"] == "false":
        return {**common, "result": None, "decision_basis": "outside_jurisdiction",
                "explanation": "Outside this address's established jurisdiction.", "conflict_flag": False}
    if rule["status"] == "failed":
        return {**common, "result": None, "decision_basis": "failed_proposal",
                "explanation": "A failed proposal creates no operative property obligation.", "conflict_flag": False}
    if rule["applicability"]["rule_kind"] == "government_or_court_duty":
        return {**common, "result": None, "decision_basis": "government_or_court_actor",
                "explanation": "This record addresses government or court action, outside this residential-property lookup.",
                "conflict_flag": False}
    if plan is None:
        return {**common, "result": "unknown", "decision_basis": "needs_compilation",
                "explanation": "Candidate rule: " + rule["title"] + ". No validated executable plan exists for this rule in the selected plan bundle. Its applicability has not been computed; this is a rule-conversion gap, not evidence that the address facts are missing or that the rule does not apply.",
                "conflict_flag": bool(rule.get("conflict_flag")), "missing_facts": [],
                "source_questions": [], "compilation_gap": True}
    branches = [evaluate_branch(branch, facts) for branch in plan["branches"]]
    dimension = "coverage" if mode == "coverage" else "event"
    selected = _combine([branch[dimension] for branch in branches], "any")
    decision = _combine([jurisdiction, selected], "all")
    conflict_reasons = set(decision["conflicts"])
    conflict_descriptions = []
    relevant_branches = [branch for branch in branches if branch[dimension]["value"] != "false"]
    for branch in relevant_branches:
        for gate in branch["source_gates"]:
            if gate["stage"] != "coverage" and branch["event"]["value"] == "false":
                continue
            conflict_reasons.update(gate["evaluation"]["conflicts"])
            if gate["conflict"] and gate["evaluation"]["value"] == "unknown":
                conflict_descriptions.append(gate["reason"])
    if decision["value"] == "false":
        result = None
        basis = "exempt" if branches and all(branch["coverage_state"] == "exempt" for branch in branches) else "out_of_scope_or_not_triggered"
    elif decision["value"] == "unknown":
        result = "unknown"
        basis = "needs_source_review" if decision["source_questions"] else "needs_information"
    else:
        result = {"in_force": "applies", "pending": "pending",
                  "not_yet_effective": "not_yet_effective"}[rule["status"]]
        basis = "coverage_established" if mode == "coverage" else "event_established"
    sentences = []
    for branch in branches:
        value = branch[dimension]["value"]
        if value == "true":
            sentences.append(branch["label"] + ": " + branch["duty"])
            if mode == "coverage" and branch["event"]["value"] == "unknown":
                sentences.append("This is a governing conditional protection; the event and event-specific exclusions are not established.")
            elif mode == "coverage" and branch["event"]["value"] == "false":
                sentences.append("The governing conditional protection is reported, but the supplied event is not triggered or is excluded from this branch.")
            sentences.extend(branch["retained_conditions"])
        elif value == "unknown":
            reasons = branch[dimension]["source_questions"]
            missing = branch[dimension]["missing_facts"]
            sentences.append(branch["label"] + ": unresolved applicability.")
            if reasons:
                sentences.append("Source review needed: " + "; ".join(reasons) + ".")
            if missing:
                sentences.append("Decisive missing facts: " + ", ".join(missing) + ".")
        else:
            sentences.append(branch["label"] + ": " + branch["coverage_state"].replace("_", " ") +
                             (" or event not triggered." if mode == "event" else "."))
    if jurisdiction["value"] == "unknown":
        sentences.append("Administrative jurisdiction remains unresolved: " + ", ".join(jurisdiction["missing_facts"]) + ".")
    if conflict_descriptions:
        sentences.append("Relevant source conflict: " + "; ".join(dict.fromkeys(conflict_descriptions)) + ".")
    fact_conflicts = sorted(reason for reason in conflict_reasons if not reason.startswith("source:"))
    if fact_conflicts:
        sentences.append("Conflicting observations affect: " + ", ".join(fact_conflicts) + ".")
    sentences.append("Coverage does not establish compliance, violation, or an amount currently owed.")
    return {**common, "branches": branches, "result": result, "decision_basis": basis,
            "evaluation": decision, "explanation": " ".join(dict.fromkeys(sentences)),
            "conflict_flag": bool(conflict_reasons), "conflict_reasons": sorted(conflict_reasons),
            "missing_facts": decision["missing_facts"], "source_questions": decision["source_questions"],
            "compilation_gap": False}


def validate_plan_bundle(rules, bundle, raw_rules_hash, as_of, source_root=None):
    require(bundle.get("schema_version") == "1.0", "Unsupported rule-plan schema")
    require(bundle.get("rules_sha256") == raw_rules_hash, "Rule snapshot differs from compiled plans")
    require(as_of in bundle.get("supported_as_of", []), "Query date is not reviewed by this plan bundle")
    date.fromisoformat(as_of)
    if source_root is not None:
        compilation = bundle.get("compilation", {})
        for filename, key in (("AGENTS.md", "agents_sha256"),
                              ("outputs/module_a/applicability_contract.json", "applicability_contract_sha256")):
            path = Path(source_root) / filename
            if path.exists():
                require(compilation.get(key) == file_hash(path),
                        "Model-visible instruction/contract changed since plan compilation: " + filename)
    by_id = {rule["team_rule_id"]: rule for rule in rules}
    require(len(by_id) == len(rules), "Duplicate rule IDs")
    require(isinstance(bundle.get("plans"), list) and bool(bundle["plans"]), "Empty plan bundle")
    require(isinstance(bundle.get("fact_scopes"), dict), "Plan bundle requires fact_scopes")
    require(all(isinstance(key, str) and isinstance(value, str) and value.strip()
                for key, value in bundle["fact_scopes"].items()), "Invalid fact scope definition")
    plans = {}
    verified_evidence = {}
    for plan in bundle["plans"]:
        rule_id = plan["team_rule_id"]
        require(rule_id in by_id and rule_id not in plans, "Unknown or duplicate planned rule " + rule_id)
        rule = by_id[rule_id]
        require(rule["as_of"] == as_of, "Rule status snapshot date differs from query date")
        require(plan.get("source_rule_sha256") == canonical_hash(rule), "Per-rule plan hash mismatch " + rule_id)
        require(isinstance(plan.get("notes"), list), "Plan notes must be an array")
        require(isinstance(plan.get("branches"), list) and bool(plan["branches"]), "Missing rule branches")
        criteria = {item["id"]: item for item in rule["applicability"]["criteria"]}
        require(len(criteria) == len(rule["applicability"]["criteria"]), "Duplicate criterion within one rule")
        branch_ids = set()
        for branch in plan["branches"]:
            required = {"branch_id", "label", "coverage", "trigger", "exclusions", "requirements",
                        "modifiers", "source_gates", "duty", "retained_conditions", "evidence_refs"}
            require(set(branch) == required, "Branch fields differ from executable contract: " + rule_id)
            require(branch["branch_id"] not in branch_ids, "Duplicate branch ID")
            branch_ids.add(branch["branch_id"])
            for key in ("branch_id", "label", "duty"):
                require(isinstance(branch[key], str) and bool(branch[key].strip()), "Missing branch text " + key)
            validate_bound_expression(branch["coverage"])
            if branch["trigger"] is not None:
                validate_bound_expression(branch["trigger"])
            for field in ("exclusions", "requirements", "modifiers", "source_gates", "retained_conditions", "evidence_refs"):
                require(isinstance(branch[field], list), "Expected branch list " + field)
            for field in ("exclusions", "requirements", "modifiers", "source_gates"):
                ids = [item["id"] for item in branch[field]]
                require(len(ids) == len(set(ids)), "Duplicate clause ID in " + field)
                for item in branch[field]:
                    if field == "source_gates":
                        require(set(item) == {"id", "stage", "when", "reason", "conflict"}, "Invalid source gate fields")
                        require(item["stage"] in PLAN_STAGES and type(item["conflict"]) is bool, "Invalid source gate")
                        require(bool(item["reason"]), "Missing source gate reason")
                        if item["when"] is not None:
                            validate_bound_expression(item["when"])
                    else:
                        validate_bound_expression(item["when"])
                    if field == "exclusions":
                        require(set(item) == {"id", "stage", "when", "reason"}, "Invalid exclusion fields")
                        require(item["stage"] in {"coverage", "event"} and bool(item["reason"]), "Invalid exclusion scope")
                    if field == "requirements":
                        require(set(item) == {"id", "when", "description"} and bool(item["description"]), "Invalid requirement")
                    if field == "modifiers":
                        require(set(item) == {"id", "when", "effect"} and isinstance(item["effect"], dict)
                                and bool(item["effect"].get("description")), "Invalid modifier")
            require(bool(branch["evidence_refs"]), "Branch has no supporting evidence references")
            for ref in branch["evidence_refs"]:
                require(set(ref) == {"criterion_id", "evidence_index"}, "Invalid evidence reference fields")
                require(ref["criterion_id"] in criteria, "Unknown criterion in " + rule_id)
                items = criteria[ref["criterion_id"]]["evidence"]
                require(type(ref["evidence_index"]) is int and 0 <= ref["evidence_index"] < len(items), "Invalid evidence index")
                evidence = items[ref["evidence_index"]]
                key = rule_id + ":" + ref["criterion_id"] + ":" + str(ref["evidence_index"])
                if key in verified_evidence:
                    continue
                if source_root is not None:
                    doc = evidence["source_doc_id"]
                    paths = [Path(source_root) / "participant-final-no-hour16_v5/corpus/text" / (doc + ".txt"),
                             Path(source_root) / "data/supplemental" / (doc + ".txt")]
                    exact = False
                    for path in paths:
                        if not path.exists():
                            continue
                        source_bytes = path.read_bytes()
                        source = source_bytes.decode("utf-8")
                        start, end = evidence.get("start"), evidence.get("end")
                        if (type(start) is int and type(end) is int and 0 <= start < end <= len(source)
                                and source[start:end] == evidence["quote"]):
                            expected = evidence.get("source_sha256")
                            if expected is None or hashlib.sha256(source_bytes).hexdigest() == expected:
                                exact = True
                                break
                    require(exact, "Original evidence span/hash failed for " + key)
                verified_evidence[key] = evidence
        plans[rule_id] = plan
    needed = {key for plan in bundle["plans"] for key in plan_fact_keys(plan)}
    require(needed <= set(bundle["fact_scopes"]), "Some executable facts lack declared scopes")
    return plans, verified_evidence


def run_lookup(rules_path, plans_path, addresses_path, address_ids, output, as_of=DEFAULT_DATE,
               mode="coverage", overrides_path=None, source_root=Path("."),
               rule_id_map_path=Path("data/evaluation/submission_rule_ids.json"),
               field_dependencies_path=Path("data/evaluation/address_field_dependencies.json")):
    started = time.perf_counter()
    output = Path(output)
    require(not output.exists() or not any(output.iterdir()), "Output directory is not empty; use a new directory to preserve prior runs")
    require(address_ids and len(address_ids) == len(set(address_ids)), "Address IDs must be nonempty and unique")
    require(len(address_ids) == 1 or overrides_path is None, "Case fact overlays require exactly one address")
    timings = {}
    stamp = time.perf_counter()
    rules_path, plans_path, addresses_path = map(Path, (rules_path, plans_path, addresses_path))
    rules = json.loads(rules_path.read_text(encoding="utf-8"))["rules"]
    timings["load_rules_seconds"] = time.perf_counter() - stamp
    stamp = time.perf_counter()
    bundle = json.loads(plans_path.read_text(encoding="utf-8"))
    plans, evidence = validate_plan_bundle(rules, bundle, file_hash(rules_path), as_of, source_root)
    field_dependencies_path = Path(field_dependencies_path)
    field_dependencies = load_field_dependencies(
        field_dependencies_path, file_hash(rules_path), file_hash(plans_path), plans)
    from .submission_ids import export_with_short_ids, validate_registry
    rule_id_map_path = Path(rule_id_map_path)
    registry = json.loads(rule_id_map_path.read_text(encoding="utf-8"))
    mapping = validate_registry(registry)
    require(all(rule["team_rule_id"] in mapping for rule in rules),
            "Submission ID registry does not cover the current rule snapshot")
    timings["load_and_validate_plans_seconds"] = time.perf_counter() - stamp
    inputs = {str(path): file_hash(path) for path in (
        rules_path, plans_path, addresses_path, rule_id_map_path, field_dependencies_path)}
    if overrides_path is not None:
        inputs[str(overrides_path)] = file_hash(overrides_path)
    for name in ("AGENTS.md", "outputs/module_a/applicability_contract.json"):
        path = Path(source_root) / name
        if path.exists():
            inputs[str(path)] = file_hash(path)
    code_hashes = {name: file_hash(Path(__file__).with_name(name))
                   for name in ("evaluator.py", "eval_logic.py", "eval_facts.py", "submission_ids.py", "lookup_output.py", "field_diagnostics.py")}
    bound_inputs = {}
    decisions = {}
    address_diagnostics = {}
    payload = {"as_of": as_of, "lookups": {}}
    timings["bind_facts_seconds"] = 0.0
    timings["evaluate_seconds"] = 0.0
    for address_id in address_ids:
        stamp = time.perf_counter()
        bound = load_address_facts(addresses_path, address_id, as_of, overrides_path,
                                   skip_unverified_jurisdiction=True)
        bound_inputs[address_id] = bound
        timings["bind_facts_seconds"] += time.perf_counter() - stamp
        row = bound["address"]
        eligible = row.get("jurisdiction_status") == "verified"
        address_diagnostics[address_id] = {
            "status": "evaluated" if eligible else "skipped",
            "jurisdiction_status": row.get("jurisdiction_status", ""),
            "reason_code": None if eligible else "jurisdiction_not_verified",
            "reason": "" if eligible else row.get("jurisdiction_evidence_ref", ""),
            "jurisdiction_evidence_ref": row.get("jurisdiction_evidence_ref", ""),
            "message": ("Address passed the jurisdiction input check." if eligible else
                        "Rule evaluation skipped because jurisdiction_status is not verified. This does not establish that the physical address is nonexistent."),
            "rules_evaluated": len(rules) if eligible else 0,
        }
        if not eligible:
            decisions[address_id] = []
            payload["lookups"][address_id] = []
            continue
        validate_fact_scopes(bundle, bound["facts"])
        stamp = time.perf_counter()
        records = [evaluate_rule(rule, plans.get(rule["team_rule_id"]), bound["facts"], mode) for rule in rules]
        timings["evaluate_seconds"] += time.perf_counter() - stamp
        decisions[address_id] = records
        lookup = []
        for rule, record in zip(rules, records):
            if record["result"] is None:
                continue
            record["address_field_diagnostics"] = rule_field_diagnostics(
                record, plans.get(rule["team_rule_id"]), bound["facts"], row, field_dependencies)
            lookup.append(format_lookup_entry(rule, record, plans.get(rule["team_rule_id"]), bound["facts"]))
        payload["lookups"][address_id] = lookup
    summary = {}
    for address_id, records in decisions.items():
        candidates = [item for item in records if item["decision_basis"] != "outside_jurisdiction"]
        summary[address_id] = {
            "evaluation_status": address_diagnostics[address_id]["status"],
            "evaluated_rule_count": len(records),
            "jurisdiction_candidate_count": len(candidates),
            "compiled_candidate_count": sum(item["compiled"] for item in candidates),
            "returned_count": len(payload["lookups"][address_id]),
            "results": dict(Counter(item["result"] for item in payload["lookups"][address_id])),
            "decision_bases": dict(Counter(item["decision_basis"] for item in candidates)),
            "coverage_complete": (address_diagnostics[address_id]["status"] == "evaluated"
                                  and not any(item.get("compilation_gap") for item in candidates)),
        }
    report = {"as_of": as_of, "mode": mode, "scope": "Versioned executable applicability plans; any missing candidate plans remain explicitly uncompiled.",
              "rule_count": len(rules), "plan_count": len(plans), "addresses": summary,
              "evaluated_address_count": sum(item["status"] == "evaluated" for item in address_diagnostics.values()),
              "skipped_address_count": sum(item["status"] == "skipped" for item in address_diagnostics.values()),
              "exact_source_evidence_checks": len(evidence), "input_sha256": inputs,
              "evaluator_code_sha256": code_hashes, "runtime_model_calls": 0,
              "runtime_model_input_tokens": 0, "runtime_model_output_tokens": 0,
              "timings": timings, "runtime_cache_hits": 0,
              "measurement_scope": "Fresh deterministic runtime only. Conversation-based plan compilation and development time are excluded; their token use is not measured.",
              "warning": "Not legal advice. Format and exact-source checks do not certify legal correctness. This is not a complete 500-address submission."}
    for filename, expected in inputs.items():
        require(file_hash(filename) == expected, "Input changed during evaluation: " + filename)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = export_with_short_ids(rules, payload, registry)
    report["rule_id_format"] = {"submission": "r-0001 style decimal IDs", "internal": "preserved content hashes",
                              "mapping": "rule_id_map.json", "paired_rules": "rules.json"}
    artifacts["evaluation_audit.json"] = {"as_of": as_of, "mode": mode,
                 "facts": bound_inputs, "decisions": decisions, "evidence": evidence,
                 "address_diagnostics": address_diagnostics,
                 "plan_source": str(plans_path), "source_plan_compilation": bundle.get("compilation", {}),
                 "rule_id_mapping": "rule_id_map.json", "rule_id_namespace": "internal"}
    artifacts["address_diagnostics.json"] = {"as_of": as_of, "addresses": address_diagnostics}
    stamp = time.perf_counter()
    for name, value in artifacts.items():
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    timings["serialize_and_write_results_seconds"] = time.perf_counter() - stamp
    report["elapsed_before_report_write_seconds"] = time.perf_counter() - started
    report["lookups_sha256"] = file_hash(output / "lookups.json")
    report["exported_rules_sha256"] = file_hash(output / "rules.json")
    report["rule_id_map_sha256"] = file_hash(output / "rule_id_map.json")
    report["address_diagnostics_sha256"] = file_hash(output / "address_diagnostics.json")
    report["evaluation_audit_sha256"] = file_hash(output / "evaluation_audit.json")
    (output / "run_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate versioned rental-rule plans. Not legal advice.")
    parser.add_argument("--rules", type=Path, default=Path("outputs/module_a/rules.json"))
    parser.add_argument("--plans", type=Path, default=Path("data/evaluation/rule_plans.json"))
    parser.add_argument("--addresses", type=Path, default=Path("outputs/address_facts/address_enrichment.csv"))
    parser.add_argument("--address-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", default=DEFAULT_DATE)
    parser.add_argument("--mode", choices=["coverage", "event"], default="coverage")
    parser.add_argument("--facts", type=Path, help="Optional evidence-bearing case fact overlay for one address.")
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--rule-id-map", type=Path, default=Path("data/evaluation/submission_rule_ids.json"),
                        help="Persistent internal-to-decimal submission ID registry.")
    parser.add_argument("--field-dependencies", type=Path, default=Path("data/evaluation/address_field_dependencies.json"),
                        help="Versioned explanation-only links between missing rule facts and address fields.")
    args = parser.parse_args(argv)
    try:
        report = run_lookup(args.rules, args.plans, args.addresses, args.address_id, args.output,
                            args.as_of, args.mode, args.facts, args.source_root, args.rule_id_map,
                            args.field_dependencies)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, "Error: {}\n".format(exc))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
