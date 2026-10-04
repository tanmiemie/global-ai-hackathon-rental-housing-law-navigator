"""Command-line entry point for extraction and independent output validation."""

import argparse
from datetime import date
import json
from pathlib import Path
import re

from .model import CodexModel
from .pipeline import run, write_json
from .sources import load_sources, source_inventory
from .validation import validate_export


def _valid_full_date(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract cited rental-housing rules. Not legal advice.")
    parser.add_argument("command", choices=["inventory", "extract", "validate"])
    parser.add_argument("--pack", type=Path, default=Path("participant-final-no-hour16_v5"))
    parser.add_argument("--output", type=Path,
                        help="Output directory; defaults to outputs/pilot with --documents, otherwise outputs/module_a.")
    parser.add_argument("--supplemental", type=Path, default=Path("data/supplemental"))
    parser.add_argument("--as-of", default=None,
                        help="Query date (YYYY-MM-DD); extraction defaults to 2026-10-01. Validation checks this date only when supplied.")
    parser.add_argument("--documents", help="Comma-separated IDs for a pilot or targeted rerun.")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-chars", type=int, default=180000)
    parser.add_argument("--batch-chars", type=int, default=48000)
    parser.add_argument("--cache", type=Path, default=Path(".cache/rent_rules"))
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", help="Optional explicit model; otherwise use the configured CLI model.")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh", "ultra"],
                        help="Optional per-run reasoning setting; does not change the CLI configuration.")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--offline", action="store_true", help="Use existing model responses only.")
    parser.add_argument("--resume", action="store_true",
                        help="Reuse completed batch extractions after exact source/prompt/schema verification.")
    args = parser.parse_args(argv)
    if args.output is None:
        args.output = Path("outputs/pilot" if args.documents is not None else "outputs/module_a")
    if args.workers < 1 or args.max_chars <= 1200 or args.batch_chars < 1:
        parser.error("workers and batch-chars must be positive; max-chars must exceed 1200")
    try:
        if args.command == "extract":
            as_of = "2026-10-01" if args.as_of is None else args.as_of
            if not _valid_full_date(as_of):
                raise ValueError("--as-of must be a real full date in YYYY-MM-DD format.")
            model = CodexModel(args.cache, args.codex, args.model, args.timeout, offline=args.offline,
                               reasoning_effort=args.reasoning_effort)
            report = run(args.pack, args.output, model, as_of=as_of,
                         supplemental_dir=args.supplemental,
                         selected=set(args.documents.split(",")) if args.documents else None,
                         workers=args.workers, max_chars=args.max_chars, batch_chars=args.batch_chars,
                         resume=args.resume)
            print(json.dumps({k: report[k] for k in ["valid", "rule_count", "candidate_count",
                             "processed_source_count", "failed_batch_count", "review_item_count", "scope_complete"]}, indent=2))
            return 0 if report["valid"] and not report["failed_batch_count"] and not report.get("model_failure_count", 0) else 1
        sources = load_sources(args.pack, supplemental_dir=args.supplemental)
        if args.command == "inventory":
            inventory = source_inventory(sources)
            write_json(args.output / "source_inventory.json", inventory)
            print(json.dumps({"source_count": len(sources),
                              "available_source_count": sum(bool(s.text) for s in sources.values())}, indent=2))
            return 0
        schema = json.loads((args.pack / "schema/rule_record.schema.json").read_text(encoding="utf-8"))
        payload = json.loads((args.output / "rules.json").read_text(encoding="utf-8"))
        report = validate_export(payload, schema, sources)
        if args.as_of is not None:
            report["requested_as_of"] = args.as_of
            if not _valid_full_date(args.as_of):
                report["errors"].append({
                    "code": "invalid_requested_as_of",
                    "message": "--as-of must be a real full date in YYYY-MM-DD format; received {!r}.".format(args.as_of),
                })
            elif isinstance(payload, dict) and isinstance(payload.get("rules"), list):
                for index, rule in enumerate(payload["rules"]):
                    if isinstance(rule, dict) and rule.get("as_of") != args.as_of:
                        report["errors"].append({
                            "code": "as_of_mismatch",
                            "message": "rules[{}].as_of must match requested --as-of {} (found {!r}).".format(
                                index, args.as_of, rule.get("as_of")),
                        })
            report["error_count"] = len(report["errors"])
            report["valid"] = not report["errors"]
        write_json(args.output / "format_validation.json", report)
        print(json.dumps(report, indent=2))
        return 0 if report["valid"] else 1
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, "Error: {}\n".format(exc))
