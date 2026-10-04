# Rental Housing Law Navigator — Module A

This repository contains an automatic, evidence-backed rental-law extraction
pipeline and a reviewed **466-rule snapshot** for **2026-10-01**. The snapshot
covers the organizer's six categories across California, New Jersey, and
Massachusetts and their selected cities.

Start with [rules.json](outputs/module_a/rules.json). The rule inventory is ready
for downstream integration; an address evaluator, the 500-address lookup output,
and change-test results are not implemented here. This prototype is **not legal
advice** and does not establish complete legal coverage.

## Included handoff

| File or directory | Purpose |
| --- | --- |
| [rules.json](outputs/module_a/rules.json) | Reviewed rules, exact quotations, source URLs, applicability conditions, and qualified status assessments |
| [Applicability contract](outputs/module_a/applicability_contract.json) | Evaluation order, missing-fact handling, temporal distinctions, and actor roles |
| [Fact catalog](outputs/module_a/applicability_fact_catalog.json) | Facts referenced by individual rule conditions; not a list of questions every user must answer |
| [Status review report](outputs/module_a/status_verification_report.json) | Status changes, evidence coverage, validation result, and the reviewed export's SHA-256 |
| [Saved validation result](outputs/module_a/format_validation.json) | Successful validation in the original local environment; see the public-clone limitations below |
| [Address fact handoff](ADDRESS_FACT_HANDOFF.md) | Contract for the teammate enriching the 500 sample addresses |
| [Submission plan](SUBMISSION_READINESS_PLAN.md) | Remaining work for address evaluation, change tests, and the submission package |
| [Address profile](outputs/submission_readiness_20261003/address_profile.json) | Input completeness and consistency findings for the supplied address table |
| [Organizer pack](participant-final-no-hour16_v5/README.md) | Original challenge, schema, submission examples, source manifest, and address CSV |
| [Supplemental D074](data/supplemental/D074.txt) | Final San Diego algorithmic-pricing code used to update three draft-derived records |
| [D074 capture log](data/supplemental/D074_capture_log.json) | Retrieval and capture provenance for the included supplemental source |
| [Capture notes](data/supplemental/capture_notes.txt) | Source availability and redistribution boundaries |
| `src/`, `scripts/`, `tests/` | Extraction implementation, optional source fetcher, and automated tests |
| [Address facts](outputs/address_facts/README.md) | 500-address jurisdiction and property-fact enrichment, evidence table, audit files, and deterministic rebuild scripts |

The organizer pack is unchanged. Generated outputs and supplemental material
belong outside it. Some provenance fields in the saved artifacts name local
review files that are intentionally absent from this public handoff; they are
historical references, not required files available in this clone.

## Current result

The export uses the organizer's `{"rules": [...]}` submission wrapper. Its
466 entries use only the official rule-status values:

| Status | Count | Meaning |
| --- | ---: | --- |
| `in_force` | 457 | Operative rule or adopted government direction; property applicability still requires evaluation |
| `not_yet_effective` | 6 | Enacted provision with a future operative date |
| `pending` | 2 | Pending proposal, not an operative obligation |
| `failed` | 1 | Failed proposal, not an operative obligation |

All records include `applicability` and `status_verification` extensions, which
are permitted by the organizer schema. The status review was performed on
**2026-10-03** while retaining the legal query date **2026-10-01**. Retrieval,
enactment, effective, and query dates are separate fields or dated events. An
unverified effective date remains `null` rather than being inferred from a
publication date.

The status report distinguishes **102 externally confirmed**, **333 externally
supported**, **30 corpus-only**, and **1 conflicting-source** assessments. Support
for a current legal framework does not establish every historical version or
resolve every condition. The flagged statewide reusable-screening-report claim
requires source review before a conclusive applicability decision.

Six former pending records were corrected: two Berkeley prohibitions, three San
Diego provisions, and a Los Angeles agency reporting instruction. The Los Angeles
item creates no landlord software ban. San Diego's final text also required a
two-landlord data threshold, an appraisal exclusion, and attorney-fee recovery
for a prevailing landlord **or** tenant. Final source evidence supports those
changes; draft evidence remains identified as historical.

The reviewed snapshot combines an original 340-record extraction with 127
recovered records and one duplicate merge, followed by applicability and status
reviews. The extraction CLI does **not** reproduce these later review stages.
The old cached replay reproduced an earlier 340-record run; its caches and the
local review assemblers are not shipped. There is no demonstrated end-to-end
replay of the current 466-rule snapshot from this public clone.

## Setup

Python **3.8+** is required. Reading the artifacts, validating their official
schema, and running the tests do not require Codex or model calls.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run the following commands from the repository root.

## Validate the public snapshot's format

This checks the submission wrapper and every record against the organizer's
single-record schema. It does not need source captures or network access. It
does **not** validate quotation provenance, legal completeness, or applicability.

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from jsonschema import Draft202012Validator

schema = json.loads(Path("participant-final-no-hour16_v5/schema/rule_record.schema.json").read_text())
payload = json.loads(Path("outputs/module_a/rules.json").read_text())
assert isinstance(payload, dict) and isinstance(payload.get("rules"), list)
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
for rule in payload["rules"]:
    validator.validate(rule)
print("Official schema passed for {} rules".format(len(payload["rules"])))
PY
```

## Source availability and full provenance validation

The manifest lists **87 sources**. The public clone includes the **54 original
text sources plus supplemental D074**, making **55 available source bodies**.
The original local review environment had 60: these 55 plus five supplemental
captures that are not included in this public package. Restricted or unavailable
sources remain coverage gaps; an absent source is not evidence that no law exists.

The saved zero-error validation result was produced with the local source set.
In particular, full validation of this export requires the exact **D037 and
D059** captures, which are not redistributed. A public-clone full validation
will report missing-source evidence for those documents. Keep those failures
visible; do not treat the saved result or the schema-only check as proof that
the clone independently reproduced the provenance validation.

Inspect the available inventory without a model call:

```bash
PYTHONPATH=src .venv/bin/python -m rent_rules inventory \
  --output outputs/public_inventory
```

To run the stricter validator while preserving the shipped validation report,
copy the export to a separate output directory. Missing-source failures are
expected in the public clone:

```bash
mkdir -p outputs/provenance_check
cp outputs/module_a/rules.json outputs/provenance_check/rules.json
PYTHONPATH=src .venv/bin/python -m rent_rules validate \
  --output outputs/provenance_check --as-of 2026-10-01
```

This validator checks schema, quotations, evidence locations, source identity,
rule relationships, and the requested query date. Recovering exact, compatible
source captures is necessary to rerun the original provenance checks. Fetching
a page again can produce different text, offsets, hashes, or retrieval dates;
it does not automatically repair or reproduce the saved evidence.

The optional fetcher is limited to five previously reviewed manifest URLs. Its
plan can be inspected without requests or writes:

```bash
python3 scripts/fetch_sources.py --dry-run
```

A publicly accessible page does not automatically permit redistribution of its
full text. Keep excluded source bodies and model prompts local, and follow the
source-specific restrictions in the capture notes.

## Run a new extraction

Extraction additionally requires an installed, configured, authenticated Codex
CLI. The adapter uses the locally configured model unless `--model` is supplied;
model calls consume the account's normal usage. Authentication stays in the
user's CLI configuration, outside this repository.

Use a separate output and cache directory for new work. The commands below
preserve `outputs/module_a/rules.json`.

```bash
# Confirm the optional extraction prerequisite.
codex login status

# Representative pilot using three organizer-supplied documents.
PYTHONPATH=src .venv/bin/python -m rent_rules extract \
  --documents D009,D057,D078 --as-of 2026-10-01 --workers 1 \
  --output outputs/fresh_run/pilot --cache .cache/fresh_run/pilot

# Separate full run over all source bodies currently available locally.
PYTHONPATH=src .venv/bin/python -m rent_rules extract \
  --as-of 2026-10-01 --workers 3 \
  --output outputs/fresh_run/full --cache .cache/fresh_run/full
```

A fresh run creates a new extraction result; it is not expected to recreate the
466-record reviewed snapshot or its later applicability/status extensions.
It writes its own inventory, coverage, candidate, review, evidence-audit, and
validation artifacts. Failed or filtered units remain visible, and successful
partial results are preserved. Schema validity and rule counts alone do not
demonstrate extraction quality or full coverage.

`--resume` can reuse compatible completed units in a run created locally.
`--offline` requires that run's existing compatible model-response cache; no such
cache is included here. Source, prompt, schema, model configuration, or relevant
instruction changes can invalidate reuse. Cache identity does not yet account
for all project instructions such as `AGENTS.md`; check compatibility before
reuse and choose a fresh cache when needed.

## Extraction method and tests

The pipeline loads manifest sources, retains source identities, and splits long
documents into overlapping source spans. Model extraction is followed by exact
quotation checks, a separate source-context review, a bounded repair attempt,
and consolidation of duplicate candidates and legal interactions. Deterministic
code handles identifiers, evidence locations, serialization, and validation.
Different conditions, exceptions, amounts, and temporal versions must remain
distinct. Unresolved candidates are reported rather than forced into the export.

The ordinary extraction stages and the later reviewed snapshot are separate
capabilities. Local review decisions enriched the published records; those
decisions are not a general automated status-monitoring or address-evaluation
service.

Run the implementation's automated tests without model calls:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

These tests exercise pipeline behavior and synthetic cases. Passing them does
not certify the current legal interpretations or reproduce the reviewed export.

## Using the rules for address evaluation

The teammate's role is to enrich address and property facts with sources. Our
remaining integration work is to evaluate those facts against the rules and
produce the official lookup and change-test outputs. See the handoff and
submission plan linked above.

An address selects jurisdictional candidates. A future evaluator must then
check the actor, legal status, relevant event date, coverage, and complete
exemption branches. Missing facts do not establish that an exemption is absent.
Building age, certificate-of-occupancy date, unit-count scope, ownership, tenancy
dates, and conduct are distinct facts. Legal protection applying to a property
also does not prove that anyone violated it.

The applicability contract uses internal outcomes `applies`, `does_not_apply`,
`needs_information`, and `needs_source_review`. A downstream adapter must map
these to the organizer's lookup format; they are not interchangeable with the
four rule-status enums. Government directions, pending proposals, and failed
proposals must not become ordinary landlord obligations.

`applicability` currently describes source-backed natural-language conditions,
not executable predicates. Resolve only the uncertainty relevant to the selected
branch, preserve the explanation and citations, and retain `unknown` where the
official lookup format requires it. The repository does not yet provide that
evaluator or a completed competition submission.
