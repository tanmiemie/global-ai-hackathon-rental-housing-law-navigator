"""Semantic counterexamples for reviewed plans using wholly synthetic facts.

These fixtures describe invented scenarios, never observations of A0001 or any
other real address. Legal predicates come from the checked-in source-linked plans.
"""

import copy
import json
from pathlib import Path
import unittest

from rent_rules.evaluator import evaluate_branch, evaluate_rule


ROOT = Path(__file__).resolve().parents[1]
IDS = {
    "cap": "r-05d0c98608939365ddba",
    "accounting": "r-121333ef6fa6480276b2",
    "fees": "r-2a8eb5c0efe499d180a4",
    "notice": "r-909c57f2c2c6d76e0b1e",
    "rent": "r-cce550a580dd877d4a97",
    "reusable": "r-22d0e104d5d089430ea2",
    "relocation": "r-ff43968a7ddaa6bf88ae",
}


class EvalSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = json.loads((ROOT / "data/evaluation/pilot_rule_plans.json").read_text())
        cls.plans = {p["team_rule_id"]: p for p in cls.bundle["plans"]}
        cls.rules = {r["team_rule_id"]: r for r in json.loads(
            (ROOT / "outputs/module_a/rules.json").read_text())["rules"]}

    def facts(self, values=None):
        values = dict({
            "jurisdiction.state": "CA",
            "jurisdiction.city": "Los Angeles",
            "property.residential_use": True,
            "property.use": "multifamily_residential",
            "context.residential_rental_lookup": True,
        }, **(values or {}))
        result = {}
        for key, value in values.items():
            scope = self.bundle["fact_scopes"].get(key, "synthetic_test_only")
            result[key] = dict(value) if isinstance(value, dict) else {
                "status": "known", "value": value,
                "subject_scope": scope,
                "evidence": ["Synthetic test observation; not a real address fact."],
            }
        return result

    def evaluate(self, name, values=None, mode="coverage"):
        rid = IDS[name]
        return evaluate_rule(self.rules[rid], self.plans[rid], self.facts(values), mode)

    @staticmethod
    def clause(branch, field, identifier):
        return next(x for x in branch[field] if x["id"] == identifier)

    def cap_event(self, **extra):
        values = {
            "transaction.payment_is_residential_security": True,
            "transaction.security_demanded": True,
            "transaction.security_received": False,
            "transaction.security_demand_date": "2026-09-01",
            "transaction.payment_is_section_1950_6_screening_fee": False,
            "transaction.payment_is_advance_rent": False,
            "transaction.alteration_mutual_agreement": False,
        }
        values.update(extra)
        return values

    def small_owner(self):
        return {
            "owner.is_natural_person": False,
            "owner.is_family_trust_settlor_or_beneficiary": False,
            "owner.entity_type": "llc",
            "owner.all_llc_members_are_natural_persons": True,
            "owner.portfolio_residential_rental_property_count": 2,
            "owner.portfolio_dwelling_units_offered_for_rent": 4,
            "tenant.is_service_member_under_section_400": False,
            "legal_context.section_400_definition_resolved": True,
        }

    def fee_event(self):
        return {
            "transaction.screening_fee_collected": True,
            "transaction.payment_meets_screening_fee_definition": True,
            "transaction.screening_actor": "owner",
            "transaction.applicant_role": "prospective_tenant",
            "transaction.unit_currently_available": True,
            "transaction.fee_excess_over_authorized_actual_costs": 0,
            "transaction.fee_excess_over_applicable_annual_ceiling": 0,
            "property.housing_assistance_program_applies": False,
        }

    def clear_rent_exclusions(self):
        return {
            "property.recorded_affordable_housing_restriction": False,
            "property.affordable_housing_subsidy_agreement": False,
            "property.is_dormitory": False,
            "legal_context.unit_subject_to_public_rent_control": False,
            "property.is_mobilehome": False,
            "property.certificate_of_occupancy_date": "1990-01-01",
            "property.separately_alienable_title": False,
            "property.building_unit_count": 3,
        }

    def test_conditional_security_protection_does_not_require_collection_event(self):
        result = self.evaluate("cap")
        branch = result["branches"][0]
        self.assertEqual(result["result"], "applies")
        self.assertEqual(branch["event"]["value"], "unknown")
        self.assertEqual(branch["compliance"], "unknown")
        self.assertEqual(branch["resolved_effects"], [])

    def test_amount_within_one_month_does_not_need_small_owner_or_service_definition(self):
        values = self.cap_event(**{"transaction.security_amount_in_months_of_rent": 1})
        result = self.evaluate("cap", values, "event")
        branch = result["branches"][0]
        self.assertEqual(result["result"], "applies")
        self.assertEqual(branch["compliance"], "compliant")
        # An amount can be known lawful without resolving the maximum allowance.
        self.assertEqual(branch["resolved_effects"], [])

    def test_demand_after_cutoff_does_not_require_nonexistent_receipt_date(self):
        values = self.cap_event(**{
            "owner.portfolio_residential_rental_property_count": 3,
            "transaction.security_amount_in_months_of_rent": 1.25,
        })
        result = self.evaluate("cap", values, "event")
        self.assertEqual(result["result"], "applies")
        self.assertEqual(result["branches"][0]["compliance"], "noncompliant")
        self.assertNotIn("transaction.security_received_date", result["missing_facts"])

    def test_pre_cutoff_is_an_event_exclusion_not_a_property_exemption(self):
        values = self.cap_event(**{"transaction.security_demand_date": "2024-06-30"})
        self.assertEqual(self.evaluate("cap", values)["result"], "applies")
        event = self.evaluate("cap", values, "event")
        self.assertIsNone(event["result"])
        self.assertEqual(event["branches"][0]["coverage_state"], "covered")

    def test_two_month_allowance_requires_each_nested_owner_condition(self):
        values = self.cap_event(**self.small_owner())
        values["transaction.security_amount_in_months_of_rent"] = 1.5
        initial = self.evaluate("cap", values, "event")["branches"][0]
        self.assertEqual(initial["compliance"], "compliant")
        self.assertEqual(initial["resolved_effects"][0]["cap_months"], 2)
        for key, value in (("owner.all_llc_members_are_natural_persons", False),
                           ("owner.portfolio_residential_rental_property_count", 3),
                           ("owner.portfolio_dwelling_units_offered_for_rent", 5),
                           ("tenant.is_service_member_under_section_400", True)):
            changed = dict(values, **{key: value})
            with self.subTest(condition=key):
                evaluated = self.evaluate("cap", changed, "event")
                self.assertEqual(evaluated["result"], "applies")
                self.assertEqual(evaluated["branches"][0]["compliance"], "noncompliant")
                self.assertEqual(evaluated["branches"][0]["resolved_effects"][0]["cap_months"], 1)

    def test_service_definition_is_material_above_one_month_only(self):
        values = self.cap_event(**self.small_owner())
        values["legal_context.section_400_definition_resolved"] = False
        for amount, compliance in ((1, "compliant"), (1.5, "unknown")):
            with self.subTest(amount=amount):
                values["transaction.security_amount_in_months_of_rent"] = amount
                result = self.evaluate("cap", values, "event")
                self.assertEqual(result["branches"][0]["compliance"], compliance)
                self.assertEqual(result["result"], "applies")

    def test_six_month_advance_requires_both_six_month_conditions(self):
        values = self.cap_event(**{
            "transaction.payment_is_advance_rent": True,
            "transaction.advance_rent_months": 6,
            "tenancy.term_months": 6,
        })
        self.assertIsNone(self.evaluate("cap", values, "event")["result"])
        values["tenancy.term_months"] = 5
        self.assertEqual(self.evaluate("cap", values, "event")["result"], "applies")

    def test_accounting_survives_return_of_current_balance(self):
        values = {
            "transaction.payment_is_residential_security": True,
            "transaction.security_previously_received": True,
            "transaction.security_held": False,
            "tenancy.tenant_vacated": True,
            "transaction.payment_is_section_1950_6_screening_fee": False,
            "transaction.security_demand_date": "2020-01-01",
            "transaction.security_remainder_returned_in_full": True,
        }
        result = self.evaluate("accounting", values, "event")
        self.assertEqual(result["result"], "applies")
        self.assertEqual(result["branches"][0]["event"]["value"], "true")
        self.assertEqual(result["branches"][0]["compliance"], "unknown")

    def test_unfinished_accounting_before_21_days_is_not_a_violation(self):
        values = {
            "transaction.payment_is_residential_security": True,
            "transaction.security_previously_received": True,
            "tenancy.tenant_vacated": True,
            "transaction.payment_is_section_1950_6_screening_fee": False,
            "transaction.calendar_days_since_vacancy": 2,
            "transaction.statement_includes_security_basis": False,
            "transaction.statement_includes_security_amount": False,
            "transaction.statement_includes_security_disposition": False,
            "transaction.security_remainder_returned_in_full": False,
        }
        early = self.evaluate("accounting", values, "event")
        self.assertEqual(early["branches"][0]["compliance"], "unknown")
        values["transaction.calendar_days_since_vacancy"] = 22
        late = self.evaluate("accounting", values, "event")
        self.assertEqual(late["branches"][0]["compliance"], "noncompliant")
        self.assertEqual(late["result"], "applies")

    def test_fee_process_failure_is_noncompliance_not_noncoverage(self):
        values = self.fee_event()
        values["transaction.screening_process_at_collection"] = "neither"
        result = self.evaluate("fees", values, "event")
        self.assertEqual(result["result"], "applies")
        self.assertEqual(result["branches"][0]["compliance"], "noncompliant")

    def test_fee_routes_are_alternatives_not_cumulative(self):
        routes = [
            {"transaction.screening_process_at_collection": "A",
             "transaction.completed_applications_considered_in_receipt_order": True,
             "transaction.screening_criteria_written_with_form": True,
             "transaction.first_qualifying_applicant_approved": True,
             "transaction.fee_only_charged_after_actual_consideration": True},
            {"transaction.screening_process_at_collection": "B",
             "transaction.process_b_offered_full_refund_to_every_unselected_applicant": True,
             "transaction.process_b_offered_earlier_of_7_after_selection_or_30_after_submission": True,
             "transaction.applicant_selected": True},
        ]
        for route in routes:
            with self.subTest(route=route["transaction.screening_process_at_collection"]):
                values = dict(self.fee_event(), **route)
                result = self.evaluate("fees", values, "event")
                self.assertEqual(result["result"], "applies")
                self.assertEqual(result["branches"][0]["compliance"], "compliant")

    def test_process_b_refund_not_due_at_collection(self):
        values = dict(self.fee_event(), **{
            "transaction.screening_process_at_collection": "B",
            "transaction.process_b_offered_full_refund_to_every_unselected_applicant": True,
            "transaction.process_b_offered_earlier_of_7_after_selection_or_30_after_submission": True,
            "transaction.applicant_selected": False,
            "transaction.unselected_fee_fully_refunded": False,
            "transaction.any_tenant_selected": False,
            "transaction.calendar_days_since_application_submission": 2,
        })
        early = self.evaluate("fees", values, "event")
        self.assertEqual(early["branches"][0]["compliance"], "unknown")
        values["transaction.calendar_days_since_application_submission"] = 31
        late = self.evaluate("fees", values, "event")
        self.assertEqual(late["branches"][0]["compliance"], "noncompliant")

    def test_posting_and_delivery_have_independent_triggers(self):
        result = self.evaluate("notice", {
            "procedure.renters_protections_notice_posted": False,
            "procedure.notice_posting_common_area_accessible": True,
        })
        posting, delivery = result["branches"]
        self.assertEqual(result["result"], "applies")
        self.assertEqual(posting["event"]["value"], "true")
        self.assertEqual(posting["compliance"], "noncompliant")
        self.assertEqual(delivery["event"]["value"], "unknown")
        self.assertEqual(delivery["compliance"], "unknown")

    def test_delivery_uses_commencement_or_renewal_not_occupancy_age(self):
        plan = self.plans[IDS["notice"]]["branches"][1]
        facts = self.facts({"tenancy.start_date": "2010-01-01",
                            "tenancy.renewal_date": "2023-01-27",
                            "procedure.renters_protections_notice_delivered": True})
        self.assertEqual(evaluate_branch(plan, facts)["event"]["value"], "true")
        facts = self.facts({"tenancy.start_date": "2010-01-01",
                            "tenancy.renewal_date": "2023-01-26"})
        self.assertEqual(evaluate_branch(plan, facts)["event"]["value"], "false")

    def test_rent_exclusion_dominates_missing_other_exemptions(self):
        result = self.evaluate("rent", {
            "property.is_mobilehome": False,
            "property.certificate_of_occupancy_date": "2020-01-01",
        })
        self.assertIsNone(result["result"])
        self.assertEqual(result["decision_basis"], "exempt")
        self.assertEqual(result["missing_facts"], [])
        self.assertEqual(result["source_questions"], [])

    def test_construction_year_does_not_substitute_for_certificate(self):
        values = self.clear_rent_exclusions()
        del values["property.certificate_of_occupancy_date"]
        values["property.construction_date"] = {
            "status": "unknown", "min": "1927-01-01", "max": "1927-12-31"}
        result = self.evaluate("rent", values)
        self.assertEqual(result["result"], "unknown")
        self.assertIn("property.certificate_of_occupancy_date", result["missing_facts"])

    def test_date_interval_crossing_certificate_cutoff_is_unknown(self):
        values = self.clear_rent_exclusions()
        values["property.certificate_of_occupancy_date"] = {
            "status": "unknown", "min": "2011-01-01", "max": "2011-12-31"}
        result = self.evaluate("rent", values)
        self.assertEqual(result["result"], "unknown")
        self.assertIn("property.certificate_of_occupancy_date", result["missing_facts"])

    def test_mobilehome_fact_is_not_silently_negated_by_assessor_use(self):
        values = self.clear_rent_exclusions()
        values.update({"property.use": "multifamily_residential",
                       "property.is_mobilehome": True,
                       "tenant.is_mobilehome_homeowner_under_section_798_9": True})
        result = self.evaluate("rent", values)
        self.assertIsNone(result["result"])
        self.assertEqual(result["decision_basis"], "exempt")

    def test_new_initial_rent_exception_does_not_delete_property_protection(self):
        values = self.clear_rent_exclusions()
        values.update({"transaction.proposed_or_imposed_rent_increase": True,
                       "transaction.initial_rent_or_increase": "initial_rate",
                       "tenancy.prior_tenant_remains_lawfully": False,
                       "transaction.rent_increase_effective_date": "2026-10-01"})
        self.assertEqual(self.evaluate("rent", values)["result"], "applies")
        self.assertIsNone(self.evaluate("rent", values, "event")["result"])

    def test_reusable_conflict_is_not_cured_by_report_event_facts(self):
        result = self.evaluate("reusable", {
            "transaction.reusable_screening_report_presented": True,
            "transaction.qualifying_reusable_report_used": True,
        }, "event")
        self.assertEqual(result["result"], "unknown")
        self.assertEqual(result["decision_basis"], "needs_source_review")
        self.assertTrue(result["conflict_flag"])
        self.assertEqual(result["missing_facts"], [])

    def test_reported_units_do_not_become_building_or_owner_counts(self):
        result = self.evaluate("relocation", {
            "legal_context.la_rso_coverage": True,
            "source.reported_units": 32,
        })
        self.assertEqual(result["result"], "unknown")
        self.assertIn("property.building_rental_unit_count", result["missing_facts"])
        self.assertTrue(result["source_questions"])

    def test_decisive_large_building_excludes_only_reduced_permission(self):
        result = self.evaluate("relocation", {"property.building_rental_unit_count": 5})
        self.assertIsNone(result["result"])
        self.assertEqual(result["decision_basis"], "out_of_scope_or_not_triggered")
        self.assertNotEqual(result["decision_basis"], "exempt")
        self.assertEqual(result["missing_facts"], [])
        self.assertEqual(result["source_questions"], [])

    def test_manager_conflict_does_not_bleed_into_known_owner_event(self):
        values = {
            "legal_context.la_rso_coverage": True,
            "property.building_rental_unit_count": 4,
            "owner.la_reduced_relocation_ever_used": False,
            "procedure.good_faith_occupancy_eviction": True,
            "procedure.replaces_resident_manager_with_resident_manager": False,
            "procedure.eviction_required_for_natural_disaster_hazard": False,
        }
        owner = self.evaluate("relocation", dict(values, **{
            "procedure.eviction_ground": "owner_occupancy"}))
        self.assertFalse(owner["conflict_flag"])
        manager = self.evaluate("relocation", dict(values, **{
            "procedure.eviction_ground": "resident_manager_occupancy"}))
        self.assertTrue(manager["conflict_flag"])
        for result in (owner, manager):
            self.assertTrue(all(not b["resolved_effects"] for b in result["branches"]))
            self.assertEqual(result["result"], "unknown")

    def test_uncompiled_candidate_is_not_missing_address_fact(self):
        rid = IDS["fees"]
        result = evaluate_rule(self.rules[rid], None, self.facts())
        self.assertEqual(result["result"], "unknown")
        self.assertEqual(result["decision_basis"], "needs_compilation")
        self.assertEqual(result["missing_facts"], [])
        self.assertTrue(result["compilation_gap"])

    def test_evaluation_preserves_plan_data(self):
        before = copy.deepcopy(self.plans[IDS["fees"]])
        self.evaluate("fees", self.fee_event())
        self.assertEqual(self.plans[IDS["fees"]], before)


if __name__ == "__main__":
    unittest.main()
