#!/usr/bin/env python3
"""Generate the five organizer change-test results deterministically."""

import argparse
import csv
from datetime import date
import json
from pathlib import Path


TESTS_PATH = Path("participant-final-no-hour16_v5/dev/change_tests.json")
RULES_PATH = Path("outputs/module_a/rules.json")
ADDRESSES_PATH = Path("outputs/address_facts/address_enrichment.csv")

RULE_ALIASES = {
    "T1": ["r-4fcfc494ad65ecf8a906", "r-6f96ebe74c183e607f78"],
    "T2": ["r-8635dda969781a251d31", "r-72e6795a9b3404d2d4a8"],
    "T3": [
        "r-10e8f85ab60bca6a6246",
        "r-3c8d21243cccf224902e",
        "r-6c3b1b734e206938c6fe",
        "r-7c2cfc7fe63d43dd3b48",
        "r-a771d2ac4dec0e8aaa22",
    ],
    "T4": ["r-8e1e4d30593cc6026c12", "r-46bac03898d3976e0a7b"],
    "T5": ["r-dd62064d271dea9c776b"],
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def ids_for(rows, *, state=None, city=None):
    return [
        row["address_id"]
        for row in rows
        if (state is None or row["legal_state"] == state)
        and (city is None or row["legal_city"] == city)
    ]


def temporal_status(rule, as_of):
    if rule["status"] in {"pending", "failed"}:
        return rule["status"]
    effective = rule.get("effective_date")
    if effective and date.fromisoformat(as_of) < date.fromisoformat(effective):
        return "not_yet_effective"
    return "applies"


def build_changes(rules, rows, tests):
    by_id = {rule["team_rule_id"]: rule for rule in rules}
    if len(by_id) != len(rules):
        raise ValueError("Rule IDs are not unique")
    if [row["address_id"] for row in rows] != [f"A{i:04d}" for i in range(1, 501)]:
        raise ValueError("Expected the exact ordered A0001-A0500 address set")
    if {item["test_id"] for item in tests} != set(RULE_ALIASES):
        raise ValueError("Organizer change-test inventory differs from T1-T5")
    for test_id, rule_ids in RULE_ALIASES.items():
        missing = [rule_id for rule_id in rule_ids if rule_id not in by_id]
        if missing:
            raise ValueError(f"{test_id} mapped rules are missing: {missing}")

    ca = ids_for(rows, state="CA")
    nj = ids_for(rows, state="NJ")
    ma = ids_for(rows, state="MA")
    hoboken = ids_for(rows, state="NJ", city="Hoboken")
    jersey_city = ids_for(rows, state="NJ", city="Jersey City")
    newark = ids_for(rows, state="NJ", city="Newark")
    local_nj = [row["address_id"] for row in rows if row["address_id"] in set(hoboken + jersey_city)]

    if (len(ca), len(nj), len(ma)) != (250, 140, 110):
        raise ValueError("State address counts differ from organizer sample")
    if (len(hoboken), len(jersey_city), len(newark)) != (40, 50, 50):
        raise ValueError("New Jersey city counts differ from organizer sample")
    if set(ca) | set(nj) | set(ma) != {row["address_id"] for row in rows}:
        raise ValueError("State change sets do not cover all 500 addresses")

    t1_rules = [by_id[key] for key in RULE_ALIASES["T1"]]
    t3_rules = [by_id[key] for key in RULE_ALIASES["T3"]]
    t4_rules = [by_id[key] for key in RULE_ALIASES["T4"]]
    t5_rules = [by_id[key] for key in RULE_ALIASES["T5"]]
    if {temporal_status(rule, "2025-12-31") for rule in t1_rules} != {"not_yet_effective"}:
        raise ValueError("T1 rules are not uniformly future-effective on 2025-12-31")
    if {temporal_status(rule, "2026-01-02") for rule in t1_rules} != {"applies"}:
        raise ValueError("T1 rules are not uniformly effective on 2026-01-02")
    if {temporal_status(rule, "2026-10-01") for rule in t3_rules} != {"not_yet_effective"}:
        raise ValueError("T3 rules are not uniformly future-effective on 2026-10-01")
    if {temporal_status(rule, "2027-07-02") for rule in t3_rules} != {"applies"}:
        raise ValueError("T3 rules are not uniformly effective on 2027-07-02")
    if {rule["status"] for rule in t4_rules} != {"pending"}:
        raise ValueError("T4 bills are not both pending")
    if {rule["status"] for rule in t5_rules} != {"failed"}:
        raise ValueError("T5 proposal is not failed")

    changes = {
        "T1": {
            "affected_address_ids": ca,
            "conflict_flag_address_ids": [],
            "notes": (
                "All 250 California addresses change from not_yet_effective on 2025-12-31 "
                "to applies on 2026-01-02. Organizer alias CA-ALG-01 is mapped to the two "
                "reviewed AB 325 provisions effective 2026-01-01; no separate unsupported "
                "SB 763 rule is invented."
            ),
        },
        "T2": {
            "affected_address_ids": local_nj,
            "conflict_flag_address_ids": [],
            "notes": (
                "The affected set is the union of 40 Hoboken and 50 Jersey City addresses. "
                "Each local prohibition is assigned only within its own legal-city boundary; "
                "all 50 Newark addresses are excluded."
            ),
        },
        "T3": {
            "affected_address_ids": nj,
            "conflict_flag_address_ids": local_nj,
            "notes": (
                "All 140 New Jersey addresses change from not_yet_effective on 2026-10-01 "
                "to applies on 2027-07-02. The 90 Hoboken and Jersey City addresses are flagged "
                "for possible state/local conflict and human review; no preemption conclusion is asserted."
            ),
        },
        "T4": {
            "affected_address_ids": ma,
            "conflict_flag_address_ids": [],
            "notes": (
                "S.2983 and H.5222 remain pending on 2026-10-01. All 110 Massachusetts sample "
                "addresses (60 Boston and 50 Cambridge) form the hypothetical affected set if enacted; "
                "they are not reported as current law."
            ),
        },
        "T5": {
            "affected_address_ids": [],
            "conflict_flag_address_ids": [],
            "notes": (
                "Initiative Petition 25-21 was struck on 2026-06-23 and is recorded as failed. "
                "The affected set is empty, and no ballot-question rent cap is reported for Boston or Cambridge."
            ),
        },
    }

    audit = {
        "address_count": len(rows),
        "source_files": {
            "tests": str(TESTS_PATH),
            "rules": str(RULES_PATH),
            "addresses": str(ADDRESSES_PATH),
        },
        "alias_mapping": {
            test_id: [
                {
                    "team_rule_id": rule_id,
                    "title": by_id[rule_id]["title"],
                    "status_at_2026_10_01": by_id[rule_id]["status"],
                    "effective_date": by_id[rule_id].get("effective_date"),
                    "citation": by_id[rule_id]["citation"],
                    "source_doc_id": by_id[rule_id]["source_doc_id"],
                    "source_url": by_id[rule_id]["source_url"],
                    "retrieved_at": by_id[rule_id]["retrieved_at"],
                    "quoted_span": by_id[rule_id]["quoted_span"],
                    "as_of": by_id[rule_id]["as_of"],
                }
                for rule_id in rule_ids
            ]
            for test_id, rule_ids in RULE_ALIASES.items()
        },
        "address_sets": {
            "CA": ca,
            "NJ": nj,
            "MA": ma,
            "Hoboken_NJ": hoboken,
            "Jersey_City_NJ": jersey_city,
            "Newark_NJ_excluded_from_T2": newark,
        },
        "assertions": {
            "T1_before": "not_yet_effective",
            "T1_after": "applies",
            "T2_newark_count": 0,
            "T3_before": "not_yet_effective",
            "T3_after": "applies",
            "T3_possible_conflict_count": len(local_nj),
            "T4_status": "pending",
            "T5_status": "failed",
            "T5_affected_count": 0,
        },
        "counts": {
            test_id: {
                "affected": len(result["affected_address_ids"]),
                "conflict_flagged": len(result["conflict_flag_address_ids"]),
            }
            for test_id, result in changes.items()
        },
        "limitations": (
            "Affected sets describe the supplied deterministic change scenarios, not legal advice, "
            "a violation finding, or proof that every event-specific trigger occurred."
        ),
    }
    return changes, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/module_c"))
    args = parser.parse_args()
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))["rules"]
    tests = json.loads(TESTS_PATH.read_text(encoding="utf-8"))
    with ADDRESSES_PATH.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    changes, audit = build_changes(rules, rows, tests)
    write_json(args.output / "changes.json", changes)
    write_json(args.output / "change_test_audit.json", audit)
    print(json.dumps(audit["counts"], indent=2))


if __name__ == "__main__":
    main()
