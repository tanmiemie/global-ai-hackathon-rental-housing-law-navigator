from pathlib import Path
from datetime import datetime, timezone
import json

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "outputs" / "address_facts"
ORIGINAL = REPO_ROOT / "participant-final-no-hour16_v5" / "data" / "sample_addresses.csv"
EVIDENCE_OUTPUT = OUT / "address_fact_evidence_master.csv"
FINAL_OUTPUT = OUT / "address_enrichment.csv"


def text(value):
    if pd.isna(value):
        return ""
    value = str(value).strip()
    return "" if value.lower() in {"nan", "none", "null"} else value


def positive_int(value):
    value = text(value)
    if not value or value.lower() in {"unknown", "n/a", "na"}:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if number <= 0 or not number.is_integer():
        return None
    return int(number)


def prefix_for_join(frame, prefix, base_identity):
    keep = ["address_id"]
    rename = {}
    for col in frame.columns:
        if col == "address_id" or col in base_identity:
            continue
        keep.append(col)
        rename[col] = f"{prefix}_{col}"
    return frame[keep].rename(columns=rename)


def unit_decision(row):
    constraints = []
    sources = []

    reported = positive_int(row.get("unit_reported_units"))
    if reported is not None:
        constraints.append((reported, reported, f"reported_units={reported}"))
        sources.append("original units")

    code_min = positive_int(row.get("unit_use_code_unit_min"))
    code_max = positive_int(row.get("unit_use_code_unit_max"))
    if code_min is not None or code_max is not None:
        constraints.append((code_min, code_max, f"use_code_range={code_min or ''}..{code_max or ''}"))
        sources.append("official use-code mapping")

    desc_min = positive_int(row.get("unit_description_unit_min"))
    desc_max = positive_int(row.get("unit_description_unit_max"))
    if desc_min is not None or desc_max is not None:
        constraints.append((desc_min, desc_max, f"use_description_range={desc_min or ''}..{desc_max or ''}"))
        sources.append("use-description interpretation")

    parcel_status = text(row.get("parcel_units_status"))
    parcel_value = positive_int(row.get("parcel_resolved_units"))
    if parcel_status == "consistent" and parcel_value is not None:
        constraints.append((parcel_value, parcel_value, f"public_parcel_units={parcel_value}"))
        sources.append("current public parcel lookup")

    if not constraints:
        parcel_note = f" Public parcel status: {parcel_status}." if parcel_status else ""
        return {
            "resolved_units": "unknown", "resolved_units_min": "", "resolved_units_max": "",
            "unit_value_type": "unknown", "unit_validation_status": "missing",
            "unit_source": "none", "unit_validation_explanation": "No usable unit-count evidence was available." + parcel_note,
        }

    lower = max([low for low, _, _ in constraints if low is not None], default=None)
    upper = min([high for _, high, _ in constraints if high is not None], default=None)
    details = "; ".join(label for _, _, label in constraints)
    source_text = "; ".join(dict.fromkeys(sources))

    if lower is not None and upper is not None and lower > upper:
        return {
            "resolved_units": "unknown", "resolved_units_min": "", "resolved_units_max": "",
            "unit_value_type": "unknown", "unit_validation_status": "conflict",
            "unit_source": source_text,
            "unit_validation_explanation": f"Unit evidence conflicts; final value is unknown. Evidence: {details}.",
        }
    if lower is not None and upper is not None and lower == upper:
        return {
            "resolved_units": str(lower), "resolved_units_min": str(lower), "resolved_units_max": str(upper),
            "unit_value_type": "exact", "unit_validation_status": "verified",
            "unit_source": source_text,
            "unit_validation_explanation": f"Compatible evidence supports an exact unit count of {lower}. Evidence: {details}.",
        }
    range_text = f"{lower if lower is not None else ''}..{upper if upper is not None else ''}"
    return {
        "resolved_units": range_text, "resolved_units_min": "" if lower is None else str(lower),
        "resolved_units_max": "" if upper is None else str(upper),
        "unit_value_type": "range", "unit_validation_status": "verified",
        "unit_source": source_text,
        "unit_validation_explanation": f"Compatible evidence supports a unit-count range of {range_text}. Evidence: {details}.",
    }


def main():
    base = pd.read_csv(ORIGINAL, dtype=str, keep_default_na=False)
    base_identity = {"street_address", "postal_city", "state", "zip"}
    sources = [
        ("jur", OUT / "sample_addresses_with_jurisdiction.csv"),
        ("census", OUT / "address_census_jurisdiction_audit.csv"),
        ("usecode", OUT / "address_use_code_interpretation.csv"),
        ("usedesc", OUT / "address_use_description_interpretation.csv"),
        ("unit", OUT / "address_unit_validation.csv"),
        ("parcel", OUT / "public_parcel_resolved.csv"),
        ("year", OUT / "address_year_built_comparison.csv"),
    ]

    evidence = base.copy()
    for prefix, path in sources:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        if len(frame) != 500 or frame["address_id"].nunique() != 500:
            raise ValueError(f"{path.name} is not one row per 500 unique address IDs")
        evidence = evidence.merge(
            prefix_for_join(frame, prefix, base_identity), on="address_id", how="left", validate="one_to_one"
        )
    evidence.to_csv(EVIDENCE_OUTPUT, index=False)

    unit_rows = evidence.apply(unit_decision, axis=1, result_type="expand")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    census_verified = evidence["census_jurisdiction_validation_status"].eq("verified")
    existing_state = evidence["jur_legal_state"].map(text)
    existing_city = evidence["jur_legal_city"].map(text)
    legal_state = evidence["census_census_state_abbreviation"].where(census_verified, existing_state)
    legal_city = evidence["census_census_incorporated_place_name"].where(census_verified, existing_city)

    final = pd.DataFrame({
        "address_id": evidence["address_id"],
        "street_address": evidence["street_address"],
        "postal_city": evidence["postal_city"],
        "original_state": evidence["state"],
        "original_zip": evidence["zip"],
        "legal_state": legal_state,
        "legal_city": legal_city,
        "county": "",
        "incorporation_status": census_verified.map({True: "incorporated", False: "unknown"}),
        "jurisdiction_key": legal_city + ", " + legal_state,
        "jurisdiction_status": census_verified.map({True: "verified", False: "partial"}),
        "jurisdiction_evidence_ref": evidence.apply(
            lambda r: (
                f"Census Geocoder/TIGER-Line; matched_address={text(r['census_census_matched_address'])}; "
                f"place_geoid={text(r['census_census_place_geoid'])}; benchmark={text(r['census_census_benchmark'])}"
                if text(r["census_jurisdiction_validation_status"]) == "verified"
                else "Existing legal-city mapping retained; Census lookup did not independently verify the boundary."
            ), axis=1
        ),
        "jurisdiction_retrieved_at": evidence["census_retrieved_at"],
        "jurisdiction_notes": evidence["census_validation_note"],
        "resolved_year_built": evidence["year_resolved_year_built"],
        "year_built_validation_status": evidence["year_year_built_validation_status"],
        "year_built_source": evidence["year_year_built_source"],
        "year_built_explanation": evidence["year_decision_note"],
        "year_built_conflict_log": evidence["year_conflict_log"],
        "resolved_units": unit_rows["resolved_units"],
        "resolved_units_min": unit_rows["resolved_units_min"],
        "resolved_units_max": unit_rows["resolved_units_max"],
        "unit_value_type": unit_rows["unit_value_type"],
        "unit_validation_status": unit_rows["unit_validation_status"],
        "unit_source": unit_rows["unit_source"],
        "unit_validation_explanation": unit_rows["unit_validation_explanation"],
        "resolved_property_use": evidence["unit_resolved_property_use"],
        "property_use_validation_status": evidence["unit_property_use_validation_status"],
        "property_use_source": evidence.apply(
            lambda r: "; ".join(filter(None, [
                "official use-code mapping" if text(r["usecode_usecode_property_use"]) else "",
                "use-description interpretation" if text(r["usedesc_usedescription_property_use"]) else "",
            ])), axis=1
        ),
        "property_use_explanation": evidence["unit_property_use_validation_explanation"],
        "original_use_code": evidence["use_code"],
        "official_use_code_definition": evidence["usecode_official_code_definition"],
        "use_code_verification_status": evidence["usecode_definition_verification_status"],
        "original_use_description": evidence["use_description"],
        "use_description_interpretation": evidence["usedesc_description_interpretation"],
        "use_description_interpretation_status": evidence["usedesc_description_interpretation_status"],
        "additional_information": evidence.apply(
            lambda r: json.dumps({
                "use_code": text(r["usecode_additional_information"]),
                "use_description": text(r["usedesc_other_information"]),
            }, ensure_ascii=False), axis=1
        ),
        "original_source_dataset": evidence["source_dataset"],
        "original_source_retrieved_at": evidence["retrieved_at"],
        "public_parcel_match_status": evidence["parcel_parcel_match_status"],
        "public_parcel_overall_fact_status": evidence["parcel_overall_fact_status"],
        "public_parcel_processed_at": evidence["parcel_processed_at"],
        "legal_query_date": "2026-10-01",
        "record_status": "processed",
        "processed_at": now,
    })
    final.loc[final["legal_city"].eq("") | final["legal_state"].eq(""), "jurisdiction_status"] = "unknown"
    final.loc[final["jurisdiction_status"].eq("unknown"), "jurisdiction_key"] = ""
    final.to_csv(FINAL_OUTPUT, index=False)

    print(f"evidence_rows={len(evidence)} evidence_columns={len(evidence.columns)}")
    print(f"final_rows={len(final)} final_columns={len(final.columns)}")
    for col in ["jurisdiction_status", "year_built_validation_status", "unit_validation_status", "property_use_validation_status"]:
        print(col, final[col].value_counts(dropna=False).to_dict())


if __name__ == "__main__":
    main()
