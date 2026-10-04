"""Date arithmetic must preserve unknown observations and real boundaries."""

import unittest
from rent_rules.eval_logic import evaluate_expression, validate_expression


class RelativeDateTests(unittest.TestCase):
    def evaluate(self, left, right, op="gt", years=-15, days=0):
        expression = {"date_compare": {"left": "certificate", "op": op, "right": "event", "years": years, "days": days}}
        facts = {}
        for key, value in [("certificate", left), ("event", right)]:
            if value is not None:
                facts[key] = value if isinstance(value, dict) else {"status": "known", "value": value}
        return evaluate_expression(expression, facts)

    def test_fifteen_year_anniversary_is_not_recent(self):
        self.assertEqual(self.evaluate("2011-10-01", "2026-10-01")["value"], "false")
        self.assertEqual(self.evaluate("2011-10-02", "2026-10-01")["value"], "true")

    def test_thirty_day_waiting_period_crosses_month_boundary(self):
        self.assertEqual(self.evaluate("2026-10-01", "2026-09-01", "lt", 0, 30)["value"], "false")
        self.assertEqual(self.evaluate("2026-09-30", "2026-09-01", "lt", 0, 30)["value"], "true")

    def test_missing_date_is_information_gap(self):
        result = self.evaluate("2011-10-01", None)
        self.assertEqual(result["value"], "unknown")
        self.assertEqual(result["missing_facts"], ["event"])
        self.assertEqual(result["source_questions"], [])

    def test_year_interval_straddling_cutoff_remains_unknown(self):
        result = self.evaluate({"status": "known", "min": "2011-01-01", "max": "2011-12-31"}, "2026-10-01")
        self.assertEqual(result["value"], "unknown")
        self.assertEqual(result["missing_facts"], ["certificate"])

    def test_conflicting_dates_can_agree_on_predicate(self):
        result = self.evaluate({"status": "conflicting", "alternatives": ["2012-01-01", "2013-01-01"]}, "2026-10-01")
        self.assertEqual(result["value"], "true")
        self.assertEqual(result["conflicts"], [])
        result = self.evaluate({"status": "conflicting", "alternatives": ["2010-01-01", "2013-01-01"]}, "2026-10-01")
        self.assertEqual(result["value"], "unknown")
        self.assertEqual(result["conflicts"], ["certificate"])

    def test_unresolved_leap_anniversary_does_not_guess_boundary(self):
        result = self.evaluate("2009-03-01", "2024-02-29")
        self.assertEqual(result["value"], "unknown")
        self.assertTrue(result["source_questions"])
        self.assertEqual(self.evaluate("2009-03-02", "2024-02-29")["value"], "true")

    def test_malformed_offset_rejected(self):
        with self.assertRaises(ValueError):
            validate_expression({"date_compare": {"left": "a", "right": "b", "op": "lt", "years": True, "days": 0}})

    def test_calendar_months_are_not_fixed_day_counts(self):
        expr = {"date_compare": {"left": "a", "right": "b", "op": "eq", "years": 0, "months": 6, "days": 0}}
        facts = {"a": {"status": "known", "value": "2026-07-15"}, "b": {"status": "known", "value": "2026-01-15"}}
        self.assertEqual(evaluate_expression(expr, facts)["value"], "true")
        facts["a"]["value"] = "2026-08-31"
        facts["b"]["value"] = "2026-02-28"
        self.assertEqual(evaluate_expression(expr, facts)["value"], "false")


if __name__ == "__main__":
    unittest.main()
