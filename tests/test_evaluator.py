"""Independent semantic checks for the deterministic rule-plan evaluator."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from rent_rules.evaluator import (
    canonical_hash,
    evaluate_branch,
    evaluate_rule,
    validate_fact_scopes,
    validate_bound_expression,
    validate_plan_bundle,
)


QUERY_DATE = "2026-10-01"
SNAPSHOT_HASH = "a" * 64


class BoundFieldDomainTests(unittest.TestCase):
    def test_legal_type_and_numeric_year_cannot_reinterpret_bound_fields(self):
        for expression in [
            {"fact": "property.use", "op": "eq", "value": "apartment"},
            {"fact": "property.construction_date", "op": "lt", "value": 1980},
            {"fact": "property.residential_use", "op": "eq", "value": "true"},
        ]:
            with self.assertRaises(ValueError):
                validate_bound_expression(expression)
        validate_bound_expression({"fact": "property.construction_date", "op": "lt", "value": "1980-01-01"})


def fact(key, value=True):
    return {"fact": key, "op": "eq", "value": value}


def known(value):
    return {"status": "known", "value": value}


def source_gate(stage="coverage", when=None, conflict=False):
    return {"id": "definition", "stage": stage, "when": when,
            "reason": "The branch definition needs source review.", "conflict": conflict}


def branch(name="main"):
    return {
        "branch_id": name,
        "label": name,
        "coverage": fact("covered"),
        "trigger": fact("event"),
        "exclusions": [],
        "requirements": [{"id": "perform", "when": fact("performed"),
                          "description": "Perform the required action."}],
        "modifiers": [],
        "source_gates": [],
        "duty": "Perform the required action when its event occurs.",
        "retained_conditions": ["Only this named branch is evaluated."],
        "evidence_refs": [{"criterion_id": "scope", "evidence_index": 0}],
    }


def fixture():
    source = "prefix proof suffix"
    evidence = {"source_doc_id": "DTEST", "start": 7, "end": 12, "quote": "proof",
                "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()}
    rule = {"team_rule_id": "synthetic-rule", "title": "Synthetic conditional duty",
            "jurisdiction": "CA", "category": "security_deposits", "status": "in_force",
            "as_of": QUERY_DATE, "conflict_flag": False,
            "applicability": {"rule_kind": "transaction_rule",
                              "criteria": [{"id": "scope", "evidence": [evidence]}]}}
    plan = {"team_rule_id": rule["team_rule_id"], "source_rule_sha256": canonical_hash(rule),
            "notes": [], "branches": [branch()]}
    bundle = {"schema_version": "1.0", "rules_sha256": SNAPSHOT_HASH,
              "supported_as_of": [QUERY_DATE], "plans": [plan],
              "fact_scopes": {"covered": "subject_unit", "event": "subject_transaction",
                              "performed": "subject_transaction"}}
    facts = {"jurisdiction.state": known("CA"), "covered": known(True),
             "event": known(True), "performed": known(True)}
    return rule, plan, bundle, facts, source


class EvaluatorSemanticsTests(unittest.TestCase):
    def test_unmet_compliance_does_not_remove_coverage_or_trigger(self):
        rule, plan, _, facts, _ = fixture()
        facts["performed"] = known(False)
        evaluated = evaluate_branch(plan["branches"][0], facts)
        self.assertEqual(evaluated["coverage_state"], "covered")
        self.assertEqual(evaluated["event"]["value"], "true")
        self.assertEqual(evaluated["compliance"], "noncompliant")
        for mode in ("coverage", "event"):
            self.assertEqual(evaluate_rule(rule, plan, facts, mode)["result"], "applies")

    def test_unresolved_compliance_definition_blocks_noncompliance_finding(self):
        rule, plan, _, facts, _ = fixture()
        current = plan["branches"][0]
        current["source_gates"] = [source_gate(stage="compliance")]
        facts["performed"] = known(False)
        result = evaluate_branch(current, facts)
        self.assertEqual(result["requirement_result"]["value"], "false")
        self.assertEqual(result["compliance_gate"]["value"], "unknown")
        self.assertEqual(result["compliance"], "unknown")
        self.assertEqual(result["coverage_state"], "covered")
        self.assertEqual(evaluate_rule(rule, plan, facts)["result"], "applies")
        # Resolving the gate's relevance to false permits the ordinary finding.
        current["source_gates"][0]["when"] = fact("special_case")
        facts["special_case"] = known(False)
        self.assertEqual(evaluate_branch(current, facts)["compliance"], "noncompliant")

    def test_false_event_suppresses_downstream_source_conflicts_but_keeps_trace(self):
        for stage in ("event", "compliance", "calculation"):
            with self.subTest(stage=stage):
                rule, plan, _, facts, _ = fixture()
                plan["branches"][0]["source_gates"] = [source_gate(stage=stage, conflict=True)]
                facts["event"] = known(False)
                result = evaluate_rule(rule, plan, facts, "coverage")
                self.assertEqual(result["result"], "applies")
                self.assertFalse(result["conflict_flag"])
                self.assertEqual(result["conflict_reasons"], [])
                self.assertNotIn("Relevant source conflict:", result["explanation"])
                self.assertEqual(result["branches"][0]["event"]["value"], "false")
                self.assertEqual(result["branches"][0]["source_gates"][0]["evaluation"]["conflicts"],
                                 ["source:definition"])

    def test_false_event_explanation_does_not_describe_an_unknown_event(self):
        rule, plan, _, facts, _ = fixture()
        facts["event"] = known(False)
        result = evaluate_rule(rule, plan, facts, "coverage")
        self.assertIn("not triggered or is excluded", result["explanation"])

    def test_relevant_amount_conflict_is_explained_in_lookup_text(self):
        rule, plan, _, facts, _ = fixture()
        gate = source_gate(stage="calculation", conflict=True)
        plan["branches"][0]["source_gates"] = [gate]
        result = evaluate_rule(rule, plan, facts, "coverage")
        self.assertTrue(result["conflict_flag"])
        self.assertIn("Relevant source conflict:", result["explanation"])
        self.assertIn(gate["reason"], result["explanation"])
        self.assertNotIn("event and event-specific exclusions are not established", result["explanation"])
        del facts["event"]
        result = evaluate_rule(rule, plan, facts, "coverage")
        self.assertIn("event and event-specific exclusions are not established", result["explanation"])

    def test_known_exclusion_defeats_irrelevant_source_gaps_and_conflicts(self):
        rule, plan, _, facts, _ = fixture()
        current = plan["branches"][0]
        current["exclusions"] = [{"id": "excluded", "stage": "coverage",
                                  "when": fact("exempt"), "reason": "Complete exclusion."}]
        current["source_gates"] = [source_gate(conflict=True)]
        facts["exempt"] = known(True)
        evaluated = evaluate_rule(rule, plan, facts)
        self.assertIsNone(evaluated["result"])
        self.assertEqual(evaluated["decision_basis"], "exempt")
        self.assertEqual(evaluated["source_questions"], [])
        self.assertFalse(evaluated["conflict_flag"])
        detail = evaluated["branches"][0]
        self.assertEqual(detail["coverage"]["value"], "false")
        self.assertEqual(detail["source_gates"][0]["evaluation"]["value"], "unknown")

    def test_unknown_exclusion_is_not_assumed_absent(self):
        current = branch()
        current["exclusions"] = [{"id": "excluded", "stage": "coverage",
                                  "when": fact("exempt"), "reason": "Complete exclusion."}]
        result = evaluate_branch(current, {"covered": known(True), "event": known(True)})
        self.assertEqual(result["coverage_state"], "unknown")
        self.assertEqual(result["coverage"]["missing_facts"], ["exempt"])

    def test_event_exclusion_changes_event_but_not_property_coverage(self):
        current = branch()
        current["exclusions"] = [{"id": "event-only", "stage": "event",
                                  "when": fact("event_exception"), "reason": "Event exception."}]
        facts = {"covered": known(True), "event": known(True), "event_exception": known(True)}
        result = evaluate_branch(current, facts)
        self.assertEqual(result["coverage_state"], "covered")
        self.assertEqual(result["event"]["value"], "false")
        self.assertEqual(result["compliance"], "not_triggered")

    def test_independent_duty_does_not_require_another_branch_event(self):
        rule, plan, _, facts, _ = fixture()
        posting = branch("continuous-posting")
        posting["trigger"] = None
        delivery = branch("individual-delivery")
        delivery["trigger"] = fact("delivery_event")
        plan["branches"] = [posting, delivery]
        result = evaluate_rule(rule, plan, facts, "event")
        self.assertEqual(result["result"], "applies")
        self.assertEqual(result["missing_facts"], [])
        self.assertEqual(result["branches"][0]["event"]["value"], "true")
        self.assertEqual(result["branches"][1]["event"]["value"], "unknown")
        self.assertIn("individual-delivery: unresolved applicability", result["explanation"])
        self.assertEqual(result["branches"][1]["event"]["missing_facts"], ["delivery_event"])

    def test_source_gate_relevance_and_fact_conflict_remain_separate(self):
        current = branch()
        current["source_gates"] = [source_gate(when=fact("special_case"), conflict=True)]
        facts = {"covered": known(True), "event": known(True), "special_case": known(False)}
        result = evaluate_branch(current, facts)
        self.assertEqual(result["coverage"]["value"], "true")
        self.assertEqual(result["coverage"]["source_questions"], [])
        del facts["special_case"]
        result = evaluate_branch(current, facts)
        self.assertEqual(result["coverage"]["value"], "unknown")
        self.assertEqual(result["coverage"]["missing_facts"], ["special_case"])
        self.assertEqual(result["coverage"]["conflicts"], ["source:definition"])

    def test_unknown_calculation_does_not_erase_coverage(self):
        current = branch()
        current["source_gates"] = [source_gate(stage="calculation")]
        current["modifiers"] = [{"id": "amount", "when": fact("amount_branch"),
                                 "effect": {"description": "Select the alternate amount.", "amount": 5}}]
        facts = {"covered": known(True), "event": known(True), "amount_branch": known(True)}
        result = evaluate_branch(current, facts)
        self.assertEqual(result["coverage"]["value"], "true")
        self.assertEqual(result["event"]["value"], "true")
        self.assertEqual(result["resolved_effects"], [])
        self.assertEqual(result["calculation_gate"]["value"], "unknown")

    def test_uncompiled_is_explicit_unknown_not_missing_fact_claim(self):
        rule, _, _, facts, _ = fixture()
        result = evaluate_rule(rule, None, facts)
        self.assertEqual(result["result"], "unknown")
        self.assertEqual(result["decision_basis"], "needs_compilation")
        self.assertTrue(result["compilation_gap"])
        self.assertEqual(result["missing_facts"], [])

    def test_proposal_and_government_routing(self):
        rule, plan, _, facts, _ = fixture()
        for status in ("pending", "not_yet_effective"):
            rule["status"] = status
            self.assertEqual(evaluate_rule(rule, plan, facts)["result"], status)
        rule["status"] = "failed"
        self.assertIsNone(evaluate_rule(rule, None, facts)["result"])
        rule["status"] = "in_force"
        rule["applicability"]["rule_kind"] = "government_or_court_duty"
        result = evaluate_rule(rule, None, facts)
        self.assertIsNone(result["result"])
        self.assertEqual(result["decision_basis"], "government_or_court_actor")

    def test_jurisdiction_is_a_real_gate(self):
        rule, plan, _, facts, _ = fixture()
        facts["jurisdiction.state"] = known("NJ")
        self.assertEqual(evaluate_rule(rule, None, facts)["decision_basis"], "outside_jurisdiction")
        del facts["jurisdiction.state"]
        result = evaluate_rule(rule, plan, facts)
        self.assertEqual(result["result"], "unknown")
        self.assertEqual(result["missing_facts"], ["jurisdiction.state"])

    def test_evaluation_is_repeatable_and_does_not_mutate_inputs(self):
        rule, plan, _, facts, _ = fixture()
        inputs = deepcopy((rule, plan, facts))
        first = evaluate_rule(rule, plan, facts)
        second = evaluate_rule(rule, plan, facts)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual((rule, plan, facts), inputs)


class PlanValidationTests(unittest.TestCase):
    def test_instruction_and_contract_hashes_are_checked_when_files_exist(self):
        rule, _, bundle, _, source = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_path = root / "participant-final-no-hour16_v5/corpus/text/DTEST.txt"
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_text(source, encoding="utf-8")
            # An isolated source fixture need not contain project instructions.
            validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE, root)
            files = [(root / "AGENTS.md", "agents_sha256", "Synthetic instructions.\n"),
                     (root / "outputs/module_a/applicability_contract.json",
                      "applicability_contract_sha256", '{"version":"synthetic"}\n')]
            bundle["compilation"] = {}
            for path, key, content in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                with self.subTest(missing_hash=key), self.assertRaisesRegex(ValueError, "compilation"):
                    validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE, root)
                bundle["compilation"][key] = hashlib.sha256(path.read_bytes()).hexdigest()
                validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE, root)
            for path, key, content in files:
                path.write_text(content + "changed", encoding="utf-8")
                with self.subTest(changed_file=key), self.assertRaisesRegex(ValueError, "compilation"):
                    validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE, root)
                path.write_text(content, encoding="utf-8")
            validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE, root)

    def test_fact_scope_must_match_declared_subject(self):
        _, _, bundle, _, _ = fixture()
        # An absent fact stays unknown; a supplied fact with the wrong identity
        # or counting scope must never be silently reused.
        validate_fact_scopes(bundle, {})
        validate_fact_scopes(bundle, {"covered": dict(known(True), subject_scope="subject_unit")})
        with self.assertRaises(ValueError):
            validate_fact_scopes(bundle, {"covered": dict(known(True), subject_scope="owner_portfolio")})
        with self.assertRaises(ValueError):
            validate_fact_scopes(bundle, {"covered": known(True)})
        del bundle["fact_scopes"]["performed"]
        with self.assertRaises(ValueError):
            validate_fact_scopes(bundle, {})

    def test_valid_bundle_and_evidence_identity(self):
        rule, plan, bundle, _, _ = fixture()
        plans, evidence = validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE)
        self.assertEqual(plans[rule["team_rule_id"]], plan)
        self.assertEqual(list(evidence), ["synthetic-rule:scope:0"])

    def test_snapshot_and_rule_hash_and_query_date_fail_closed(self):
        rule, plan, bundle, _, _ = fixture()
        with self.assertRaises(ValueError):
            validate_plan_bundle([rule], bundle, "b" * 64, QUERY_DATE)
        with self.assertRaises(ValueError):
            validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, "2026-10-02")
        altered = deepcopy(rule)
        altered["title"] = "Changed source rule"
        with self.assertRaises(ValueError):
            validate_plan_bundle([altered], bundle, SNAPSHOT_HASH, QUERY_DATE)
        altered["as_of"] = "2026-10-02"
        plan["source_rule_sha256"] = canonical_hash(altered)
        with self.assertRaises(ValueError):
            validate_plan_bundle([altered], bundle, SNAPSHOT_HASH, QUERY_DATE)

    def test_malformed_plans_and_unknown_criteria_are_rejected(self):
        mutations = [
            lambda p: p["branches"][0].update(unrecognized=True),
            lambda p: p["branches"][0].update(coverage={"all": []}),
            lambda p: p["branches"].append(deepcopy(p["branches"][0])),
            lambda p: p["branches"][0].update(evidence_refs=[]),
            lambda p: p["branches"][0]["evidence_refs"][0].update(criterion_id="invented"),
            lambda p: p["branches"][0]["evidence_refs"][0].update(evidence_index=True),
        ]
        for mutation in mutations:
            rule, plan, bundle, _, _ = fixture()
            mutation(plan)
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE)

    def test_source_span_and_source_hash_checked_against_original_text(self):
        rule, plan, bundle, _, source = fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participant-final-no-hour16_v5/corpus/text/DTEST.txt"
            path.parent.mkdir(parents=True)
            path.write_text(source, encoding="utf-8")
            validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE, directory)
            # The quote is unchanged, but other source bytes changed.
            path.write_text(source + " changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE, directory)
            path.write_text(source, encoding="utf-8")
            rule["applicability"]["criteria"][0]["evidence"][0]["quote"] = "wrong"
            plan["source_rule_sha256"] = canonical_hash(rule)
            with self.assertRaises(ValueError):
                validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE, directory)

    def test_evidence_offsets_must_be_nonnegative_and_within_source(self):
        # Python slices would silently accept each of these malformed spans.
        spans = [(-12, 12, "proof"), (7, 9999, "proof suffix"), (7, 7, "")]
        for start, end, quote in spans:
            rule, plan, bundle, _, source = fixture()
            evidence = rule["applicability"]["criteria"][0]["evidence"][0]
            evidence.update(start=start, end=end, quote=quote)
            plan["source_rule_sha256"] = canonical_hash(rule)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "participant-final-no-hour16_v5/corpus/text/DTEST.txt"
                path.parent.mkdir(parents=True)
                path.write_text(source, encoding="utf-8")
                with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                    validate_plan_bundle([rule], bundle, SNAPSHOT_HASH, QUERY_DATE, directory)


if __name__ == "__main__":
    unittest.main()
