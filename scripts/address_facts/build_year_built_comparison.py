from pathlib import Path
from datetime import datetime, timezone

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "outputs" / "address_facts"
ORIGINAL = REPO_ROOT / "participant-final-no-hour16_v5" / "data" / "sample_addresses.csv"
PARCEL = OUT / "public_parcel_resolved.csv"
COMPARISON = OUT / "address_year_built_comparison.csv"
AUDIT = OUT / "year_built_comparison_audit_log.csv"


def normalize_year(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "unknown", "n/a", "na"}:
        return ""
    try:
        number = float(text)
        current_year = datetime.now(timezone.utc).year
        if number < 1700 or number > current_year:
            return ""
        if number.is_integer():
            return str(int(number))
    except ValueError:
        pass
    return text


def clean(value):
    return "" if pd.isna(value) else str(value).strip()


def decide(row):
    original_year = normalize_year(row["original_year_built"])
    parcel_year = normalize_year(row["parcel_year_built_raw"])
    parcel_status = clean(row["parcel_year_built_status"])
    parcel_raw = clean(row["parcel_year_built_raw"])
    parcel_usable = parcel_status == "consistent" and bool(parcel_year)
    if not parcel_usable:
        parcel_year = ""

    original_source = clean(row["original_year_built_source"])
    parcel_source = clean(row["parcel_year_built_source"])

    if original_year and parcel_year and original_year == parcel_year:
        return pd.Series({
            "parcel_year_built": parcel_year,
            "resolved_year_built": original_year,
            "year_built_validation_status": "verified",
            "year_built_resolution_case": "both_consistent",
            "year_built_source": f"original:{original_source}; public_parcel:{parcel_source}",
            "decision_note": "Original and public parcel values agree.",
            "conflict_log": "",
        })
    if original_year and parcel_year and original_year != parcel_year:
        return pd.Series({
            "parcel_year_built": parcel_year,
            "resolved_year_built": "unknown",
            "year_built_validation_status": "conflict",
            "year_built_resolution_case": "both_conflict",
            "year_built_source": "conflicting_sources",
            "decision_note": "Original and public parcel values conflict; final value set to unknown.",
            "conflict_log": f"original={original_year} ({original_source}); public_parcel={parcel_year} ({parcel_source})",
        })
    if original_year:
        return pd.Series({
            "parcel_year_built": "",
            "resolved_year_built": original_year,
            "year_built_validation_status": "verified",
            "year_built_resolution_case": "original_only",
            "year_built_source": f"original:{original_source}",
            "decision_note": "Original value retained because no usable public parcel value was available.",
            "conflict_log": "",
        })
    if parcel_year:
        return pd.Series({
            "parcel_year_built": parcel_year,
            "resolved_year_built": parcel_year,
            "year_built_validation_status": "verified",
            "year_built_resolution_case": "parcel_only",
            "year_built_source": f"public_parcel:{parcel_source}",
            "decision_note": "Public parcel value used because the original value was missing.",
            "conflict_log": "",
        })
    parcel_problem = (
        f" Public parcel reported an unusable year value ({parcel_raw}); it was not inferred or expanded."
        if parcel_status == "consistent" and parcel_raw and not parcel_year else ""
    )
    return pd.Series({
        "parcel_year_built": "",
        "resolved_year_built": "unknown",
        "year_built_validation_status": "unknown",
        "year_built_resolution_case": "both_missing_or_parcel_unusable",
        "year_built_source": "none",
        "decision_note": "No usable year-built value was available from either source." + parcel_problem,
        "conflict_log": "",
    })


def main():
    original = pd.read_csv(ORIGINAL, dtype=str, keep_default_na=False)
    parcel = pd.read_csv(PARCEL, dtype=str, keep_default_na=False)

    original = original.rename(columns={
        "year_built": "original_year_built",
        "source_dataset": "original_year_built_source",
        "retrieved_at": "original_source_retrieved_at",
    })
    parcel = parcel.rename(columns={
        "resolved_year_built": "parcel_year_built_raw",
        "year_built_status": "parcel_year_built_status",
        "source_dataset": "parcel_year_built_source",
        "processed_at": "parcel_query_processed_at",
    })

    parcel_cols = [
        "address_id", "parcel_year_built_raw", "parcel_year_built_status",
        "parcel_year_built_source", "parcel_query_processed_at",
        "candidate_count_reported", "candidate_capture_status",
        "address_alignment_status",
    ]
    merged = original.merge(parcel[parcel_cols], on="address_id", how="left", validate="one_to_one")
    decisions = merged.apply(decide, axis=1)
    comparison = pd.concat([merged, decisions], axis=1)
    comparison["original_year_built"] = comparison["original_year_built"].map(normalize_year)
    comparison["processed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    output_cols = [
        "address_id", "street_address", "postal_city", "state", "zip",
        "original_year_built", "original_year_built_source", "original_source_retrieved_at",
        "parcel_year_built_raw", "parcel_year_built", "parcel_year_built_source", "parcel_year_built_status",
        "parcel_query_processed_at", "resolved_year_built", "year_built_validation_status",
        "year_built_resolution_case", "year_built_source", "decision_note", "conflict_log",
        "candidate_count_reported", "candidate_capture_status", "address_alignment_status",
        "processed_at",
    ]
    comparison[output_cols].to_csv(COMPARISON, index=False)

    audit = comparison.assign(
        audit_id=comparison["address_id"] + ":year_built_comparison",
        field_name="year_built",
        decision_rule=comparison["year_built_resolution_case"],
    )
    audit_cols = [
        "audit_id", "address_id", "field_name", "original_year_built",
        "original_year_built_source", "parcel_year_built_raw", "parcel_year_built", "parcel_year_built_source",
        "parcel_year_built_status", "resolved_year_built", "year_built_validation_status",
        "year_built_resolution_case", "decision_rule", "decision_note", "conflict_log",
        "candidate_count_reported", "candidate_capture_status", "address_alignment_status",
        "processed_at",
    ]
    audit[audit_cols].to_csv(AUDIT, index=False)

    print(comparison["year_built_validation_status"].value_counts(dropna=False).to_string())
    print(comparison["year_built_resolution_case"].value_counts(dropna=False).to_string())
    print(f"comparison={COMPARISON}")
    print(f"audit={AUDIT}")


if __name__ == "__main__":
    main()
