"""Focused tests for the generic expression evaluator, without legal fixtures."""

import itertools
import unittest

from rent_rules.eval_logic import evaluate_expression, validate_expression


def predicate(key="x", op="eq", value=True):
    return {"fact": key, "op": op, "value": value}


class EvalLogicTests(unittest.TestCase):
    def evaluate(self, expression, record=None):
        return evaluate_expression(expression, {} if record is None else {"x": record})

    def test_three_valued_truth_tables(self):
        values = (True, False, None)
        for left, right in itertools.product(values, repeat=2):
            facts = {key: {"status": "known", "value": value} for key, value in (("a", left), ("b", right)) if value is not None}
            for op in ("all", "any"):
                if op == "all":
                    expected = "false" if False in (left, right) else "unknown" if None in (left, right) else "true"
                else:
                    expected = "true" if True in (left, right) else "unknown" if None in (left, right) else "false"
                with self.subTest(left=left, right=right, op=op):
                    result = evaluate_expression({op: [predicate("a"), predicate("b")]}, facts)
                    self.assertEqual(result["value"], expected)
                    self.assertEqual(len(result["trace"]["children"]), 2)

    def test_decisive_dependencies_only_but_complete_trace(self):
        source = {"unknown": {"reason": "Missing definition", "kind": "source"}}
        uncompiled = {"unknown": {"reason": "Branch not compiled", "kind": "uncompiled"}}
        conflict = {"status": "conflicting", "alternatives": [True, False], "evidence": ["source-a", "source-b"]}
        for operator, known_value in (("any", True), ("all", False)):
            result = evaluate_expression({operator: [predicate("decisive"), source, predicate("x"), uncompiled]},
                                         {"decisive": {"status": "known", "value": known_value}, "x": conflict})
            self.assertEqual(result["missing_facts"], [])
            self.assertEqual(result["source_questions"], [])
            self.assertEqual(result["conflicts"], [])
            self.assertEqual(result["trace"]["decisive_children"], [0])
            self.assertEqual(result["trace"]["children"][2]["observed"]["evidence"], ["source-a", "source-b"])
        result = evaluate_expression({"all": [source, uncompiled, predicate()]}, {})
        self.assertEqual(result["source_questions"], ["Missing definition", "Branch not compiled"])
        self.assertEqual(result["missing_facts"], ["x"])

    def test_not_unknown_and_epistemic_known(self):
        self.assertEqual(self.evaluate({"not": predicate()})["value"], "unknown")
        self.assertEqual(self.evaluate({"known": "x"})["value"], "unknown")
        self.assertEqual(self.evaluate({"not": {"known": "x"}})["value"], "unknown")
        self.assertEqual(self.evaluate({"known": "x"}, {"status": "known", "value": None})["value"], "true")

    def test_intervals_and_unbounded_intervals(self):
        cases = [
            ({"min": 3, "max": 8}, "gt", 2, "true"),
            ({"min": 3, "max": 8}, "gt", 8, "false"),
            ({"min": 3, "max": 8}, "gt", 4, "unknown"),
            ({"min": 3}, "ge", 3, "true"),
            ({"min": 3}, "lt", 3, "false"),
            ({"max": 8}, "le", 8, "true"),
            ({"max": 8}, "ge", 8, "unknown"),
            ({"min": None, "max": 8}, "gt", 8, "false"),
            ({"min": 3, "max": 3}, "eq", 3, "true"),
            ({"min": 3, "max": 8}, "ne", 9, "true"),
            ({"min": 3, "max": 8}, "eq", 4, "unknown"),
            ({"min": 3, "max": 8}, "in", [1, 2, 9], "false"),
            ({"min": 3, "max": 8}, "in", [3, 8], "unknown"),
            ({"min": "2020-01-01", "max": "2020-01-03"}, "lt", "2020-02-01", "true"),
            ({"min": "2020-01-01", "max": "2020-01-03"}, "in", ["2020-01-01", "2020-01-02", "2020-01-03"], "true"),
        ]
        for bounds, op, value, expected in cases:
            with self.subTest(bounds=bounds, op=op, value=value):
                record = dict(bounds, status="unknown")
                result = self.evaluate(predicate(op=op, value=value), record)
                self.assertEqual(result["value"], expected)
                self.assertEqual(result["missing_facts"], ["x"] if expected == "unknown" else [])

    def test_conflicts_settle_only_when_predicate_agrees(self):
        expr = predicate(op="gt", value=5)
        for alternatives, expected in (([8, 9], "true"), ([2, 3], "false"), ([2, 8], "unknown"), ([], "unknown")):
            result = self.evaluate(expr, {"status": "conflicting", "alternatives": alternatives, "evidence": ["a", "b"]})
            self.assertEqual(result["value"], expected)
            self.assertEqual(result["conflicts"], ["x"] if expected == "unknown" else [])
            self.assertTrue(result["trace"]["conflict_observed"])
            self.assertEqual(result["trace"]["observed"]["evidence"], ["a", "b"])

    def test_type_aware_equality_and_no_ordered_coercion(self):
        for actual, expected in ((True, 1), ("4", 4), (None, "null")):
            self.assertEqual(self.evaluate(predicate(value=expected), {"status": "known", "value": actual})["value"], "false")
        self.assertEqual(self.evaluate(predicate(value=4.0), {"status": "known", "value": 4})["value"], "true")
        for value in (True, "4", "2020-13-01"):
            with self.assertRaises(ValueError):
                self.evaluate(predicate(op="gt", value=3), {"status": "known", "value": value})

    def test_validation_rejects_malformed_nodes(self):
        invalid = [None, [], {}, {"all": []}, {"any": "x"}, {"not": []},
                   {"all": [predicate()], "any": [predicate()]},
                   {"fact": "x", "op": "contains", "value": 1},
                   {"fact": "x", "op": "eq", "value": []},
                   {"fact": "x", "op": "in", "value": 1},
                   {"fact": "x", "op": "eq", "value": float("nan")},
                   {"fact": "x", "op": "lt", "value": True},
                   {"fact": "x", "op": "lt", "value": "2021-02-29"},
                   {"known": ""}, {"known": "x", "default": False},
                   {"unknown": {"reason": "why", "kind": "guess"}},
                   {"unknown": {"reason": "why", "kind": "fact", "extra": 1}},
                   dict(predicate(), label=4), dict(predicate(), evidence_refs="ref")]
        for expr in invalid:
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                validate_expression(expr)
        cycle = {}
        cycle["not"] = cycle
        with self.assertRaises(ValueError):
            validate_expression(cycle)

    def test_malformed_facts_and_unselected_branches_are_validated(self):
        for record in (5, {}, {"status": "known"}, {"status": "known", "value": []},
                       {"status": "unknown", "min": 8, "max": 2},
                       {"status": "unknown", "min": True},
                       {"status": "unknown", "min": 2, "max": "2020-01-01"},
                       {"status": "known", "value": 1, "min": 2},
                       {"status": "conflicting", "alternatives": "wrong"}):
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.evaluate(predicate(), record)
        with self.assertRaises(ValueError):
            evaluate_expression({"any": [predicate("yes"), predicate("bad", "gt", 1)]},
                                {"yes": {"status": "known", "value": True}, "bad": {"status": "known", "value": "wrong"}})

    def test_metadata_and_input_preservation(self):
        expr = dict(predicate(), label="Coverage", evidence_refs=["criterion-1"])
        record = {"status": "known", "value": True, "evidence": ["record-1"]}
        result = self.evaluate(expr, record)
        self.assertEqual(result["trace"]["label"], "Coverage")
        result["trace"]["evidence_refs"].append("different")
        result["trace"]["observed"]["evidence"].append("different")
        self.assertEqual(expr["evidence_refs"], ["criterion-1"])
        self.assertEqual(record["evidence"], ["record-1"])


if __name__ == "__main__":
    unittest.main()
