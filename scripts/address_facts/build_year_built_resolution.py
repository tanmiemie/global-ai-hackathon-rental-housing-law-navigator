#!/usr/bin/env python3
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT/'outputs'/'address_facts'
orig = pd.read_csv(REPO_ROOT/'participant-final-no-hour16_v5'/'data'/'sample_addresses.csv', dtype=str, keep_default_na=False)
pub = pd.read_csv(OUT/'public_parcel_resolved.csv', dtype=str, keep_default_na=False)
d = orig[['address_id','street_address','postal_city','state','zip','year_built','source_dataset','retrieved_at']].merge(
    pub[['address_id','resolved_year_built','year_built_status','candidate_count_reported','candidate_capture_status','address_alignment_status','processed_at']],
    on='address_id', how='left')

def norm(x):
    try:
        return str(int(float(x))) if str(x).strip() else ''
    except Exception:
        return str(x).strip()

out = []
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
for _, r in d.iterrows():
    snap = norm(r.year_built)
    current = norm(r.resolved_year_built) if r.year_built_status == 'consistent' else ''
    if snap and current and snap == current:
        value, status, method = current, 'verified_both_sources', 'official_snapshot_and_current_public_parcel'
    elif current and not snap:
        value, status, method = current, 'resolved', 'current_public_parcel'
    elif snap and not current:
        value, status, method = snap, 'resolved', 'provided_official_source_snapshot'
    elif snap and current and snap != current:
        value, status, method = 'unknown', 'conflicting_facts', 'none'
    else:
        value, status, method = 'unknown', 'unknown', 'none'
    out.append({
        'address_id': r.address_id, 'street_address': r.street_address, 'postal_city': r.postal_city,
        'state': r.state, 'zip': r.zip, 'resolved_year_built': value,
        'year_built_resolution_status': status, 'year_built_resolution_method': method,
        'provided_snapshot_year_built': snap, 'current_public_parcel_year_built': current,
        'current_public_parcel_status': r.year_built_status, 'source_dataset': r.source_dataset,
        'source_snapshot_retrieved_at': r.retrieved_at, 'current_query_processed_at': r.processed_at,
        'candidate_count_reported': r.candidate_count_reported,
        'candidate_capture_status': r.candidate_capture_status,
        'address_alignment_status': r.address_alignment_status,
        'decision_note': 'No inference used; unresolved or conflicting facts are unknown.',
        'processed_at': now})

result = pd.DataFrame(out)
result.to_csv(OUT/'address_year_built_resolution.csv', index=False, encoding='utf-8-sig')
print(result.year_built_resolution_status.value_counts().to_dict())
