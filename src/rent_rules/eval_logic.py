"""Strict, evidence-preserving three-valued expression evaluation.

This module knows no legal rules. A caller must distinguish coverage predicates
from event predicates, compliance requirements, and amount calculations.
"""

import copy
import calendar
import datetime
import math
import re
from collections.abc import Mapping


_META = {"label", "evidence_refs"}
_OPS = {"eq", "ne", "lt", "le", "gt", "ge", "in"}
_DATES = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _scalar(value):
    return value is None or isinstance(value, (str, bool, int, float)) and (
        not isinstance(value, float) or math.isfinite(value)
    )


def _ordered_kind(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not _scalar(value):
            raise ValueError("Ordered numeric values must be finite")
        return "number"
    if isinstance(value, str) and _DATES.fullmatch(value):
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            raise ValueError("Invalid ISO calendar date: {!r}".format(value))
        return "date"
    raise ValueError("Ordered values must be finite numbers or full ISO dates")


def validate_expression(expr):
    """Validate the complete DSL tree, raising ValueError for invalid syntax."""
    active = set()

    def walk(node):
        if not isinstance(node, dict):
            raise ValueError("An expression must be an object")
        identity = id(node)
        if identity in active:
            raise ValueError("Expression trees must not contain cycles")
        active.add(identity)
        try:
            if "label" in node and not isinstance(node["label"], str):
                raise ValueError("label must be a string")
            if "evidence_refs" in node and not isinstance(node["evidence_refs"], list):
                raise ValueError("evidence_refs must be a list")
            keys = set(node) - _META
            if keys in ({"all"}, {"any"}):
                children = node[next(iter(keys))]
                if not isinstance(children, list) or not children:
                    raise ValueError("all/any require nonempty expression lists")
                for child in children:
                    walk(child)
            elif keys == {"not"}:
                walk(node["not"])
            elif keys == {"known"}:
                if not isinstance(node["known"], str) or not node["known"]:
                    raise ValueError("known requires a nonempty fact key")
            elif keys == {"date_compare"}:
                item = node["date_compare"]
                if not isinstance(item, dict) or not {"left", "op", "right", "years", "days"} <= set(item) or set(item) - {"left", "op", "right", "years", "days", "months"}:
                    raise ValueError("date_compare requires left, op, right, years, and days")
                if not all(isinstance(item[key], str) and item[key] for key in ("left", "right")):
                    raise ValueError("Date operands must be fact keys")
                if item["op"] not in {"lt", "le", "gt", "ge", "eq", "ne"}:
                    raise ValueError("Invalid date comparison")
                if any(type(item.get(key, 0)) is not int for key in ("years", "months", "days")):
                    raise ValueError("Calendar offsets must be integers")
            elif keys == {"unknown"}:
                detail = node["unknown"]
                if not isinstance(detail, dict) or set(detail) != {"reason", "kind"}:
                    raise ValueError("unknown requires exactly reason and kind")
                if not isinstance(detail["reason"], str) or not detail["reason"]:
                    raise ValueError("unknown requires a nonempty reason")
                if detail["kind"] not in ("source", "uncompiled", "fact"):
                    raise ValueError("Invalid unknown kind")
            elif keys == {"fact", "op", "value"}:
                if not isinstance(node["fact"], str) or not node["fact"]:
                    raise ValueError("fact requires a nonempty key")
                op = node["op"]
                if not isinstance(op, str) or op not in _OPS:
                    raise ValueError("Unknown comparison operator")
                expected = node["value"]
                if op == "in":
                    if not isinstance(expected, list) or not all(_scalar(x) for x in expected):
                        raise ValueError("in requires a list of finite scalar values")
                elif not _scalar(expected):
                    raise ValueError("Comparison value must be a finite scalar")
                elif op in {"lt", "le", "gt", "ge"}:
                    _ordered_kind(expected)
            else:
                raise ValueError("Unknown fields or more than one expression operator")
        finally:
            active.remove(identity)

    walk(expr)


def _equal(left, right):
    # Python considers True equal to 1; the DSL deliberately does not.
    left_number = isinstance(left, (int, float)) and not isinstance(left, bool)
    right_number = isinstance(right, (int, float)) and not isinstance(right, bool)
    if left_number and right_number:
        return left == right
    return type(left) is type(right) and left == right


def _compare(actual, op, expected):
    if op == "eq":
        return _equal(actual, expected)
    if op == "ne":
        return not _equal(actual, expected)
    if op == "in":
        return any(_equal(actual, item) for item in expected)
    if _ordered_kind(actual) != _ordered_kind(expected):
        raise ValueError("Ordered comparison cannot mix numeric and date values")
    if op == "lt":
        return actual < expected
    if op == "le":
        return actual <= expected
    if op == "gt":
        return actual > expected
    return actual >= expected


def _bounds(record):
    lower = record.get("min")
    upper = record.get("max")
    present = [v for v in (lower, upper) if v is not None]
    if not present:
        return None
    kind = _ordered_kind(present[0])
    if any(_ordered_kind(v) != kind for v in present):
        raise ValueError("Interval bounds must use the same comparison kind")
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("Interval min cannot exceed max")
    return lower, upper, kind


def _in_interval(value, bounds):
    lower, upper, kind = bounds
    try:
        if _ordered_kind(value) != kind:
            return False
    except ValueError:
        return False
    return (lower is None or value >= lower) and (upper is None or value <= upper)


def _interval_compare(bounds, op, expected):
    """Use closed bounds; absent endpoints denote unbounded intervals."""
    lower, upper, kind = bounds
    if lower is not None and upper is not None and lower == upper:
        return _compare(lower, op, expected)
    if op == "in":
        candidates = [item for item in expected if _in_interval(item, bounds)]
        if not candidates:
            return False
        # ISO dates are a discrete domain, unlike general numeric intervals.
        if kind == "date" and lower is not None and upper is not None:
            days = (datetime.date.fromisoformat(upper) - datetime.date.fromisoformat(lower)).days + 1
            if len(set(candidates)) == days:
                return True
        return None
    if op in {"eq", "ne"}:
        result = None if _in_interval(expected, bounds) else False
        return None if result is None else (not result if op == "ne" else result)
    if _ordered_kind(expected) != kind:
        raise ValueError("Interval and comparison value must use the same kind")
    if op == "lt":
        return True if upper is not None and upper < expected else (
            False if lower is not None and lower >= expected else None
        )
    if op == "le":
        return True if upper is not None and upper <= expected else (
            False if lower is not None and lower > expected else None
        )
    if op == "gt":
        return True if lower is not None and lower > expected else (
            False if upper is not None and upper <= expected else None
        )
    return True if lower is not None and lower >= expected else (
        False if upper is not None and upper < expected else None
    )


def _fact_record(facts, key):
    if key not in facts:
        return {"status": "unknown", "reason": "Fact not supplied"}, None
    record = facts[key]
    if not isinstance(record, Mapping):
        raise ValueError("Fact {!r} must be a status-bearing object".format(key))
    if record.get("status") not in ("known", "unknown", "conflicting"):
        raise ValueError("Fact {!r} has an invalid status".format(key))
    if "value" in record and not _scalar(record["value"]):
        raise ValueError("Fact values must be finite scalars")
    if "alternatives" in record and (
        not isinstance(record["alternatives"], list)
        or not all(_scalar(item) for item in record["alternatives"])
    ):
        raise ValueError("Fact alternatives must be a list of finite scalars")
    bounds = _bounds(record)
    if record["status"] == "known":
        if "value" not in record and bounds is None:
            raise ValueError("Known facts need a value or at least one interval bound")
        if "value" in record and bounds is not None and not _in_interval(record["value"], bounds):
            raise ValueError("Known fact value is inconsistent with its interval")
    return record, bounds


def _unique(items):
    return list(dict.fromkeys(items))


def _calendar_shift(value, years, days, months=0):
    if value is None:
        return None, None
    original = datetime.date.fromisoformat(value)
    target_month = original.month - 1 + months
    target_year = original.year + years + target_month // 12
    target_month = target_month % 12 + 1
    try:
        target = original.replace(year=target_year, month=target_month)
        targets = [target]
    except ValueError:
        if not 1 <= target_year <= 9999:
            raise ValueError("Calendar offset is outside supported dates")
        last = datetime.date(target_year, target_month, calendar.monthrange(target_year, target_month)[1])
        targets = [last, last + datetime.timedelta(days=1)]
    shifted = [(target + datetime.timedelta(days=days)).isoformat() for target in targets]
    return min(shifted), max(shifted)


def _date_comparison(item, facts):
    records, intervals, missing, conflicts = {}, {}, [], []
    for key in (item["left"], item["right"]):
        record, bounds = _fact_record(facts, key)
        records[key] = dict(record)
        if record["status"] == "known" and "value" in record:
            interval = (record["value"], record["value"])
        elif record["status"] == "conflicting" and record.get("alternatives"):
            interval = (min(record["alternatives"]), max(record["alternatives"]))
        elif bounds is not None:
            interval = bounds[:2]
        else:
            interval = None
        if interval is None:
            missing.append(key)
        else:
            for value in interval:
                if value is not None and _ordered_kind(value) != "date":
                    raise ValueError("Date comparisons require ISO-date observations")
        if record["status"] == "conflicting":
            conflicts.append(key)
        intervals[key] = interval
    trace = {"operator": "date_compare", **item, "observed": records}
    if missing:
        return _result(None, trace, missing=missing, conflicts=conflicts)
    if item["left"] == item["right"] and item["years"] == 0 and item["days"] == 0 and item.get("months", 0) == 0:
        return _result(item["op"] in {"eq", "le", "ge"}, trace)
    low, high = intervals[item["left"]]
    right_low, right_high = intervals[item["right"]]
    shifted_low = _calendar_shift(right_low, item["years"], item["days"], item.get("months", 0))
    shifted_high = _calendar_shift(right_high, item["years"], item["days"], item.get("months", 0))
    rlow, rhigh = shifted_low[0], shifted_high[1]
    trace["shifted_right_interval"] = {"min": rlow, "max": rhigh}
    op = item["op"]
    if op in {"lt", "le"}:
        strict = op == "lt"
        yes = high is not None and rlow is not None and (high < rlow if strict else high <= rlow)
        no = low is not None and rhigh is not None and (low >= rhigh if strict else low > rhigh)
    elif op in {"gt", "ge"}:
        strict = op == "gt"
        yes = low is not None and rhigh is not None and (low > rhigh if strict else low >= rhigh)
        no = high is not None and rlow is not None and (high <= rlow if strict else high < rlow)
    else:
        yes = low is not None and low == high == rlow == rhigh
        no = (high is not None and rlow is not None and high < rlow or
              low is not None and rhigh is not None and low > rhigh)
        if op == "ne":
            yes, no = no, yes
    value = True if yes else False if no else None
    sources = []
    if value is None and (shifted_low[0] != shifted_low[1] or shifted_high[0] != shifted_high[1]):
        sources.append("The supplied rule does not resolve this end-of-month anniversary boundary; the last valid day and first following day are retained.")
    unresolved = [key for key, interval in intervals.items() if interval[0] != interval[1]] if value is None else []
    return _result(value, trace, missing=unresolved, sources=sources,
                   conflicts=conflicts if value is None else [])


def _result(value, trace, missing=(), sources=(), conflicts=()):
    name = "unknown" if value is None else ("true" if value else "false")
    trace["value"] = name
    return {
        "value": name,
        "missing_facts": _unique(missing),
        "source_questions": _unique(sources),
        "conflicts": _unique(conflicts),
        "trace": trace,
    }


def evaluate_expression(expr, facts):
    """Evaluate all nodes while reporting only decision-relevant dependencies.

    Trace children preserve even irrelevant unknowns and observed conflicts.
    Result-level conflicts identify unresolved facts affecting this predicate;
    they are not a legal-source conflict flag for an output rule.
    """
    validate_expression(expr)
    if not isinstance(facts, Mapping):
        raise ValueError("facts must be a mapping")

    def evaluate(node):
        trace = {k: copy.deepcopy(node[k]) for k in sorted(_META) if k in node}
        if "date_compare" in node:
            return _date_comparison(node["date_compare"], facts)
        if "all" in node or "any" in node:
            operator = "all" if "all" in node else "any"
            children = [evaluate(child) for child in node[operator]]
            decisive = "false" if operator == "all" else "true"
            selected = [i for i, child in enumerate(children) if child["value"] == decisive]
            if selected:
                value = decisive == "true"
            else:
                selected = [i for i, child in enumerate(children) if child["value"] == "unknown"]
                value = None if selected else operator == "all"
                if not selected:
                    selected = list(range(len(children)))
            trace.update(operator=operator, children=[x["trace"] for x in children], decisive_children=selected)
            relevant = [children[i] for i in selected]
            return _result(value, trace,
                           (key for child in relevant for key in child["missing_facts"]),
                           (key for child in relevant for key in child["source_questions"]),
                           (key for child in relevant for key in child["conflicts"]))
        if "not" in node:
            child = evaluate(node["not"])
            trace.update(operator="not", children=[child["trace"]])
            value = None if child["value"] == "unknown" else child["value"] == "false"
            return _result(value, trace, child["missing_facts"], child["source_questions"], child["conflicts"])
        if "unknown" in node:
            detail = node["unknown"]
            trace.update(operator="unknown", **detail)
            return _result(None, trace,
                           [detail["reason"]] if detail["kind"] == "fact" else [],
                           [detail["reason"]] if detail["kind"] != "fact" else [])
        key = node.get("fact", node.get("known"))
        record, bounds = _fact_record(facts, key)
        trace.update(operator="fact" if "fact" in node else "known", fact=key,
                     observed=copy.deepcopy(dict(record)), conflict_observed=record["status"] == "conflicting")
        if "known" in node:
            value = True if record["status"] == "known" else None
        else:
            trace.update(op=node["op"], expected=copy.deepcopy(node["value"]))
            if record["status"] == "conflicting":
                results = [_compare(item, node["op"], node["value"]) for item in record.get("alternatives", [])]
                trace["alternative_results"] = ["true" if value else "false" for value in results]
                value = results[0] if results and all(item == results[0] for item in results) else None
            elif record["status"] == "known" and "value" in record:
                value = _compare(record["value"], node["op"], node["value"])
            elif bounds is not None:
                value = _interval_compare(bounds, node["op"], node["value"])
            else:
                value = None
        return _result(value, trace, [key] if value is None else [], (),
                       [key] if value is None and record["status"] == "conflicting" else [])

    return evaluate(expr)
