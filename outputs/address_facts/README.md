# Address Facts

This directory contains the 500-address enrichment handoff for downstream rule
evaluation. It is separate from the unchanged organizer source pack and from
the Module A legal-rule export.

Primary files:

- `address_enrichment.csv`: downstream-ready facts, statuses, sources, and
  explanations. It contains 500 rows and 500 unique `address_id` values.
- `address_fact_evidence_master.csv`: wide audit table retaining the original
  observations and jurisdiction, Census, use-code, use-description, parcel,
  unit, and year-built evidence used by the final output.

Supporting CSV files preserve intermediate interpretations, public-parcel
candidates, and audit logs. Missing or conflicting facts remain `unknown`; the
pipeline does not infer unsupported values.

Rebuild the final two files from the repository root with:

```bash
.venv/bin/python scripts/address_facts/build_year_built_comparison.py
.venv/bin/python scripts/address_facts/build_consolidated_address_facts.py
```

The public-parcel retrieval scripts may use network services and should not be
rerun merely to validate the saved outputs. Source responses can change over
time.
