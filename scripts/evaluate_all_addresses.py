#!/usr/bin/env python3
"""Run address-gated applicability evaluation in resumable, bounded-memory batches."""

import argparse
from collections import Counter
import csv
import gc
import hashlib
import json
from pathlib import Path
import shutil
import time

from rent_rules.evaluator import file_hash, run_lookup, validate_plan_bundle


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def compatible_run(path, ids, identity):
    """Require matching inputs/code and validated public artifacts for reuse."""
    report_path = path / "run_report.json"
    if not report_path.exists():
        return None
    try:
        report = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for key in ("as_of", "mode", "input_sha256", "evaluator_code_sha256"):
        if report.get(key) != identity[key]:
            return None
    if list(report.get("addresses", {})) != ids:
        return None
    for name, field in (("lookups.json", "lookups_sha256"),
                        ("rules.json", "exported_rules_sha256"),
                        ("rule_id_map.json", "rule_id_map_sha256"),
                        ("address_diagnostics.json", "address_diagnostics_sha256"),
                        ("evaluation_audit.json", "evaluation_audit_sha256")):
        if not (path / name).exists() or file_hash(path / name) != report.get(field):
            return None
    return report


def validate_batch_diagnostics(payload, diagnostics, summary, ids, rule_count):
    """Distinguish skipped inputs from evaluated addresses with no returned rules."""
    if diagnostics.get("as_of") != payload["as_of"] or list(diagnostics.get("addresses", {})) != ids:
        raise ValueError("Batch diagnostics address/date coverage mismatch")
    for aid in ids:
        detail = diagnostics["addresses"][aid]
        state = detail.get("status")
        if state not in ("evaluated", "skipped") or summary[aid].get("evaluation_status") != state:
            raise ValueError("Batch evaluation status mismatch: " + aid)
        expected_count = rule_count if state == "evaluated" else 0
        if detail.get("rules_evaluated") != expected_count or summary[aid].get("evaluated_rule_count") != expected_count:
            raise ValueError("Batch evaluated rule count mismatch: " + aid)
        if not isinstance(detail.get("reason"), str):
            raise ValueError("Batch address diagnostic reason must be a string: " + aid)
        if state == "skipped":
            if (payload["lookups"][aid] or summary[aid]["returned_count"] != 0
                    or any(summary[aid]["results"].values()) or summary[aid]["coverage_complete"]
                    or detail.get("reason_code") != "jurisdiction_not_verified"):
                raise ValueError("Skipped address incorrectly contains evaluated results: " + aid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--reuse-run", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    started = time.perf_counter()
    rules_path = Path("outputs/module_a/rules.json")
    plans_path = Path("data/evaluation/rule_plans.json")
    addresses_path = Path("outputs/address_facts/address_enrichment.csv")
    registry_path = Path("data/evaluation/submission_rule_ids.json")
    dependencies_path = Path("data/evaluation/address_field_dependencies.json")
    with addresses_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    ids = [row["address_id"] for row in rows]
    if not ids or len(set(ids)) != len(ids) or any(not key or key != key.strip() for key in ids):
        raise ValueError("Address IDs must be nonempty, unique and exact")
    rules = json.loads(rules_path.read_text())["rules"]
    bundle = json.loads(plans_path.read_text())
    plans, evidence = validate_plan_bundle(rules, bundle, file_hash(rules_path), "2026-10-01", Path("."))
    if len(plans) != len(rules):
        raise ValueError("Full-address run requires complete rule-plan inventory")
    files = (rules_path, plans_path, addresses_path, registry_path, dependencies_path, Path("AGENTS.md"),
             Path("outputs/module_a/applicability_contract.json"))
    identity = {"as_of": "2026-10-01", "mode": "coverage",
                "input_sha256": {str(path): file_hash(path) for path in files},
                "evaluator_code_sha256": {name: file_hash(Path("src/rent_rules") / name)
                    for name in ("evaluator.py", "eval_logic.py", "eval_facts.py", "submission_ids.py", "lookup_output.py", "field_diagnostics.py")}}
    run_identity = {**identity, "address_ids": ids, "batch_size": args.batch_size,
                    "runner_sha256": file_hash(Path(__file__))}
    args.output.mkdir(parents=True, exist_ok=True)
    identity_path = args.output / "run_identity.json"
    if identity_path.exists():
        if json.loads(identity_path.read_text()) != run_identity:
            raise ValueError("Existing run identity is incompatible; use a new output directory")
    else:
        if any(args.output.iterdir()):
            raise ValueError("Output directory contains an unidentified prior run")
        write_json(identity_path, run_identity)
    summary, batches, counts = {}, [], Counter()
    fresh_timings = Counter()
    all_lookups = {}
    all_diagnostics = {}
    address_statuses = Counter()
    for offset in range(0, len(ids), args.batch_size):
        selected = ids[offset:offset + args.batch_size]
        key = "{:03d}_{}_{}".format(offset // args.batch_size + 1, selected[0], selected[-1])
        base = args.output / "batches" / key
        saved = [base] + sorted(base.parent.glob(key + "_retry*")) + args.reuse_run
        report, chosen = None, None
        for candidate in saved:
            report = compatible_run(candidate, selected, identity)
            if report is not None:
                chosen = candidate
                break
        reused = chosen is not None
        if not reused:
            chosen = base
            attempt = 1
            while chosen.exists() and any(chosen.iterdir()):
                attempt += 1
                chosen = base.with_name(base.name + "_retry{:02d}".format(attempt))
            try:
                report = run_lookup(rules_path, plans_path, addresses_path, selected, chosen,
                                    as_of=identity["as_of"], mode=identity["mode"],
                                    rule_id_map_path=registry_path)
            except Exception as exc:
                write_json(args.output / "last_failure.json", {
                    "batch": key, "directory": str(chosen), "error_type": type(exc).__name__,
                    "error": str(exc), "completed_addresses": len(summary)})
                raise
            fresh_timings.update(report["timings"])
            if compatible_run(chosen, selected, identity) is None:
                raise ValueError("Fresh batch failed input/code/artifact integrity validation: " + key)
        payload = json.loads((chosen / "lookups.json").read_text())
        if payload["as_of"] != identity["as_of"] or list(payload["lookups"]) != selected:
            raise ValueError("Batch address/date coverage mismatch")
        diagnostics = json.loads((chosen / "address_diagnostics.json").read_text())
        validate_batch_diagnostics(payload, diagnostics, report["addresses"], selected, len(rules))
        for filename, field in (("rules.json", "exported_rules_sha256"),
                                ("rule_id_map.json", "rule_id_map_sha256")):
            destination = args.output / filename
            if destination.exists():
                if file_hash(destination) != report[field]:
                    raise ValueError("Batch rule/ID exports differ")
            else:
                shutil.copyfile(chosen / filename, destination)
        for aid, entries in payload["lookups"].items():
            if aid in all_lookups:
                raise ValueError("Duplicate address while merging batches")
            all_lookups[aid] = entries
            all_diagnostics[aid] = diagnostics["addresses"][aid]
            summary[aid] = report["addresses"][aid]
            counts.update(entry["result"] for entry in entries)
            address_statuses.update([all_diagnostics[aid]["status"]])
        batches.append({"address_ids": selected, "reused": reused,
                        "directory": str(chosen), "report": str(chosen / "run_report.json"),
                        "audit": str(chosen / "evaluation_audit.json"),
                        "evaluation_audit_sha256": report["evaluation_audit_sha256"],
                        "address_diagnostics": str(chosen / "address_diagnostics.json"),
                        "address_diagnostics_sha256": report["address_diagnostics_sha256"],
                        "lookups_sha256": report["lookups_sha256"],
                        "recorded_runtime_seconds": report["elapsed_before_report_write_seconds"]})
        write_json(args.output / "progress.json", {"completed_addresses": len(summary),
                   "total_addresses": len(ids), "results": dict(counts), "batches": batches,
                   "evaluated_address_count": address_statuses["evaluated"],
                   "skipped_address_count": address_statuses["skipped"]})
        print(json.dumps({"completed": len(summary), "total": len(ids), "reused": reused,
                          "batch": key, "elapsed_seconds": round(time.perf_counter() - started, 2)}), flush=True)
        del payload, diagnostics, report
        gc.collect()
    if list(all_lookups) != ids:
        raise ValueError("Merged output does not cover CSV addresses exactly")
    for filename, expected in identity["input_sha256"].items():
        if file_hash(Path(filename)) != expected:
            raise ValueError("Input changed during batch run: " + filename)
    for filename, expected in identity["evaluator_code_sha256"].items():
        if file_hash(Path("src/rent_rules") / filename) != expected:
            raise ValueError("Evaluator code changed during batch run: " + filename)
    write_json(args.output / "lookups.json", {"as_of": identity["as_of"], "lookups": all_lookups})
    write_json(args.output / "address_diagnostics.json", {"as_of": identity["as_of"], "addresses": all_diagnostics})
    write_json(args.output / "evaluation_audit_index.json", {
        "as_of": identity["as_of"], "mode": identity["mode"], "batches": batches,
        "notes": "Detailed audits are retained per batch; reused audits remain at their original paths."})
    with (args.output / "address_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["address_id", "street_address", "legal_city", "legal_state", "jurisdiction_status",
                  "returned_count", "applies", "unknown", "pending", "not_yet_effective", "superseded", "conflict_flag_count",
                  "evaluation_status", "evaluated_rule_count", "skip_reason"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            aid = row["address_id"]
            writer.writerow({**{key: row[key] for key in fields[:5]},
                             "returned_count": summary[aid]["returned_count"],
                             **{key: summary[aid]["results"].get(key, 0) for key in fields[6:11]},
                             "conflict_flag_count": sum(item["conflict_flag"] for item in all_lookups[aid]),
                             "evaluation_status": all_diagnostics[aid]["status"],
                             "evaluated_rule_count": all_diagnostics[aid]["rules_evaluated"],
                             "skip_reason": all_diagnostics[aid]["reason"] if all_diagnostics[aid]["status"] == "skipped" else ""})
    report = {**identity, "address_count": len(ids), "rule_count": len(rules), "plan_count": len(plans),
              "exact_source_evidence_checks": len(evidence), "addresses": summary,
              "results": dict(counts), "returned_count": sum(counts.values()), "batches": batches,
              "evaluated_address_count": address_statuses["evaluated"],
              "skipped_address_count": address_statuses["skipped"],
              "runtime_model_calls": 0, "fresh_batch_timings_seconds": dict(fresh_timings),
              "reused_addresses": sum(len(batch["address_ids"]) for batch in batches if batch["reused"]),
              "elapsed_seconds": time.perf_counter() - started,
              "measurement_scope": "This invocation only; reused batch historical time is excluded from fresh timings.",
              "lookups_sha256": file_hash(args.output / "lookups.json"),
              "exported_rules_sha256": file_hash(args.output / "rules.json"),
              "rule_id_map_sha256": file_hash(args.output / "rule_id_map.json"),
              "address_diagnostics_sha256": file_hash(args.output / "address_diagnostics.json"),
              "evaluation_audit_index_sha256": file_hash(args.output / "evaluation_audit_index.json"),
              "address_summary_sha256": file_hash(args.output / "address_summary.csv"),
              "limitations": "Addresses without verified jurisdiction are skipped before rule evaluation; their empty lookup arrays do not establish absence of applicable law. Original jurisdiction evidence is retained in address_diagnostics.json. Other unresolved fact/source gates remain explicit. Complete address coverage does not certify legal correctness or complete the separate change-test submission."}
    write_json(args.output / "run_report.json", report)
    print(json.dumps({"output": str(args.output), "addresses": len(ids), "results": dict(counts),
                      "evaluated_addresses": address_statuses["evaluated"], "skipped_addresses": address_statuses["skipped"],
                      "elapsed_seconds": report["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
