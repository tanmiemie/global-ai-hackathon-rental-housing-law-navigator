# Rental Housing Law Navigator — Module A

This repository contains an automatic, evidence-backed rental-law extraction
pipeline and a reviewed **466-rule snapshot** for **2026-10-01**. The snapshot
covers the organizer's six categories across California, New Jersey, and
Massachusetts and their selected cities.

Start with the [latest lookup package](outputs/evaluator_all500_workflow_v3/README.md)
and its [paired rules.json](outputs/evaluator_all500_workflow_v3/rules.json).
The [internal rule snapshot](outputs/module_a/rules.json) remains the evaluator
input. An experimental deterministic evaluator includes executable applicability
plans for **all 466 rules**.
The A0001 pilot and full 500-address lookup are complete; the separate
change-test outputs are unfinished. This prototype is **not legal advice** and
does not establish complete legal coverage.

## Included handoff

| File or directory | Purpose |
| --- | --- |
| [rules.json](outputs/module_a/rules.json) | Reviewed rules, exact quotations, source URLs, applicability conditions, and qualified status assessments |
| [Applicability contract](outputs/module_a/applicability_contract.json) | Evaluation order, missing-fact handling, temporal distinctions, and actor roles |
| [Fact catalog](outputs/module_a/applicability_fact_catalog.json) | Facts referenced by individual rule conditions; not a list of questions every user must answer |
| [Status review report](outputs/module_a/status_verification_report.json) | Status changes, evidence coverage, validation result, and the reviewed export's SHA-256 |
| [Saved validation result](outputs/module_a/format_validation.json) | Successful validation in the original local environment; see the public-clone limitations below |
| [Address fact handoff](ADDRESS_FACT_HANDOFF.md) | Contract for the teammate enriching the 500 sample addresses |
| [Lookup reproduction guide](LOOKUP_REPRODUCTION.md) | Exact workflow v3 inputs, evaluation semantics, runnable commands, and output-hash acceptance checks |
| [Latest lookup package](outputs/evaluator_all500_workflow_v3/README.md) | Losslessly compressed full lookup, paired short-ID rules, diagnostics, statistics, and saved validation |
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

An address selects jurisdictional candidates. The evaluator must then
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

`applicability` describes source-backed natural-language conditions, not
executable predicates. The experimental executable plans are separate artifacts
and do not alter the reviewed rules. Resolve only uncertainty relevant to the
selected branch and retain `unknown` where the official format requires it.

## Run the experimental address evaluator

The [full plans](data/evaluation/rule_plans.json) translate all 466
rules into explicit `all`, `any`, `not`, and typed comparison expressions.
Model-assisted source reading produced these plans; the Python query path makes
**zero model calls**. The evaluator computes fresh decisions after the address
input check. The original seven-rule pilot is preserved separately. Full inventory compilation does not
establish complete legal correctness or complete Module B.

Run from the repository root into a new output directory:

```bash
PYTHONPATH=src .venv/bin/python -m rent_rules.evaluator \
  --rules outputs/module_a/rules.json \
  --plans data/evaluation/rule_plans.json \
  --addresses outputs/address_facts/address_enrichment.csv \
  --address-id A0001 --as-of 2026-10-01 --mode coverage \
  --output outputs/evaluator_A0001_full_run
```

The command reads the full rule snapshot, validates plan and evidence identity,
binds the selected address facts, and executes the reviewed plans. It writes:

- `lookups.json`: the organizer's wrapper and four entry fields, for A0001 only.
- `rules.json`: the matching 466-rule export with short decimal IDs, including
  remapped overrides and rule interactions. Use this file together with the
  `lookups.json` from the same run.
- `rule_id_map.json`: bidirectional mapping between submission IDs and internal
  content-hash IDs used by the plans and audit.
- `evaluation_audit.json`: all rule-routing decisions, branch evaluations,
  provenance-bearing facts, retained conditions, and exact source evidence.
- `address_diagnostics.json`: input-check results for every requested address;
  skipped inputs preserve the original `jurisdiction_evidence_ref` and have
  `rules_evaluated: 0`.
- `run_report.json`: rule/plan coverage, input and code hashes, result counts,
  and measured runtime stages. Compilation and development time are excluded.

Every returned entry includes an explicit `Citation:` in `explanation`, copied
from the rule's citation, source document ID, URL, and retrieval timestamp.
Missing citation metadata stops export. The organizer's four entry fields are
preserved; no additional `citation` field is assumed. An `unknown` result begins
with its unresolved cause: jurisdiction verification, applicability facts,
legal-source questions, conflicting evidence, or an uncompiled plan. Case facts
are displayed with readable definitions and subject scopes where available;
the audit preserves their machine keys. Alternative conditions remain
alternatives, and an unknown rule's citation does not establish applicability.

The saved [first-ten result](outputs/evaluator_first10_workflow_v3/lookups.json)
includes A0001 with 54 `applies` and 107 `unknown` entries from 162 jurisdictional candidates; one
government-only rule is omitted. All 466 inventory records are routed, with no
uncompiled candidates. `applies` describes conditional coverage, not a proven
transaction, violation, or entitlement to a calculated amount.

Submission IDs now use `r-0001` through `r-0466`, following the template's short
format. The organizer schema accepts any unique string; these numbers are local
labels, not official rule numbers. The persistent registry
`data/evaluation/submission_rule_ids.json` assigns each internal rule one stable
label across addresses, filters and repeated runs. New rules must extend this
registry with `rent_rules.submission_ids.extend_registry`; existing numbers are
never reassigned or recycled. `--rule-id-map` selects another registry explicitly.
The internal snapshot at `outputs/module_a/rules.json`, compiled plans, case fact
keys, source quotations and historical input provenance retain their original
identities. This preserves validated caches and source-hash checks. Use the
internal snapshot as evaluator input and the paired short-ID files as output.

For a partial bundle, `unknown` entries with `needs_compilation` identify rules that have no
executable plan yet. They do **not** mean that new address research will fix the
gap. They remain visible to avoid silently dropping unevaluated candidates.
Custom or partial plan bundles require a compatible `--field-dependencies`
manifest; the default manifest is bound to the current full 466-rule bundle.
The audit separates missing facts from legal-source questions for each evaluated
decision.
Existing output directories cannot be overwritten. For a reproducibility check,
use a different `--output` and compare the resulting `lookups.json` bytes.

Each address must have the exact CSV value `jurisdiction_status=verified` before
property facts are bound or rules are evaluated. Other statuses, including
`partial`, produce an empty rule array and a `skipped` address diagnostic with
the original `jurisdiction_evidence_ref`. That empty array means evaluation was
not performed; it does not establish that no laws apply or that the physical
address does not exist. Case overlays and component-level state verification
cannot bypass this input check. Read diagnostics alongside lookup results.

For eligible addresses, supplied `year_built_explanation` and
`unit_validation_explanation` are included when their unverified source fields
are linked to decisive unresolved rule inputs. The versioned
[dependency manifest](data/evaluation/address_field_dependencies.json) links
exact fact keys to explanations without assigning values or changing rule
predicates. It is validated against the rule and plan hashes. Construction
timing and property unit counts retain their subject scopes; construction year
is not a certificate-of-occupancy date and reported units are not owner holdings.
Unknown alternatives do not block a rule when another OR branch establishes
coverage. Dependency notes and their affected facts are retained in the audit.

The default `coverage` mode reports governing conditional protections while
retaining unknown event triggers. `--mode event` additionally requires the event
and event-specific exclusions to be established. Compliance is a separate audit
dimension: failing a required procedure never makes its governing rule cease to
apply. Amount modifiers do not become blanket exemptions. Independent duties
within one record have separate branches; the explanation identifies which are
established. Source gaps block only their relevant stages and branches.

Facts use three-valued logic: true OR unknown is true; false AND unknown is
false; NOT unknown remains unknown. Numeric and date intervals can decide a
threshold only when their whole supported range lies on one side. Missing facts
are never false. The CSV binder preserves verification/conflict status and does
not reinterpret reported units as owner holdings or a construction year as a CO
date. The residential-rental query premise is recorded explicitly as a premise,
not evidence of a particular lease, tenant, transaction, or violation.

The residential-use binder recognizes five explicitly supported, verified
classifications: `multifamily_residential`, `subsidized_multifamily`,
`mixed_use_multifamily`, `specialized_residential`, and `residential_tic`.
Unknown, conflicting, unverified, and unsupported labels do not establish
residential use. The original subtype and its exact source evidence remain in
the audit. Lookup explanations for the four additional subtypes state their
scope: mixed-use conclusions concern the residential rental component; subsidy
program eligibility, institutional licensing, actual tenancy, ownership-based
exemptions, and event triggers still require their own facts. Recognizing a
residential component does not resolve these separate conditions.

Optional `--facts path.json` accepts an evidence-bearing case overlay with
exactly `address_id`, `as_of`, and `facts`. Each fact record requires `status`,
`reason`, `evidence`, `subject_scope`, and `observed_at`, plus a known scalar
`value` or interval `min`/`max`. Each evidence entry requires `path`, `row_id`,
`fields`, and `retrieved_at`. Subject scopes must match the plan's `fact_scopes`.
Conflicting observations cannot silently overwrite known values. A later
retrieval date is distinct from the underlying observation/event date.

Plans are currently reviewed only for **2026-10-01**; other query dates or changed
rule snapshots are rejected instead of silently reusing incompatible legal
versions. The previous model-assisted A0001 pilot is a comparison artifact,
not a correctness oracle or runtime input. Boolean and semantic tests exercise
nested alternatives, exemptions, date boundaries, interval scope, compliance,
and source-gap isolation; they do not certify complete legal coverage.

### Evaluate all addresses

The [complete lookup archive](outputs/evaluator_all500_workflow_v3/lookups.json.gz) contains all 500
CSV addresses in their original order, using the same coverage semantics and
short rule IDs as the first-ten run. The input policy evaluates 477 verified
addresses and skips 23 partial-jurisdiction inputs. It returns 14,331 `applies`
and 40,865 `unknown` entries. Use the paired
[rules](outputs/evaluator_all500_workflow_v3/rules.json),
[address diagnostics](outputs/evaluator_all500_workflow_v3/address_diagnostics.json),
[address summary CSV](outputs/evaluator_all500_workflow_v3/address_summary.csv),
and [interactive statistics](outputs/evaluator_all500_workflow_v3/address_statistics.html).
The [first-ten preview](outputs/evaluator_first10_workflow_v3/lookups.json) has
257 `applies`, 803 `unknown`, and one skipped input (A0009). Prior outputs remain
unchanged. The run preserves the original address CSV and source explanations.
The original full JSON is 167,558,063 bytes and is published as lossless gzip;
follow the [package instructions](outputs/evaluator_all500_workflow_v3/README.md)
to restore and verify its exact bytes. The first-ten JSON is directly readable.
The published package excludes detailed batch audits, caches, old runs, and
failed attempts. Saved reports retain their original local provenance paths.

Workflow v3 repairs residential classification for 29 previously evaluated
addresses with zero `applies`. It changes exactly 570 entries from `unknown` to
`applies`, with identical returned rule membership and conflict flags. The other
448 evaluated addresses retain identical lookup entries; all 23 skipped inputs
retain their original diagnostics. All 477 evaluated addresses now have at
least one `applies`. This is an observed outcome of the verified mapping, not a
requirement imposed on each address or a reason to force an unknown result.

```bash
PYTHONPATH=src .venv/bin/python scripts/evaluate_all_addresses.py \
  --output outputs/evaluator_all500_run
```

The runner calls the same evaluator in sequential batches of ten to bound audit
memory usage. Completed compatible batches are reused after input, code and
artifact hash checks. Repeating the command resumes from saved batches;
incomplete output directories are preserved and retried separately. A fresh
lookup merges every batch, with detailed audit paths in
`evaluation_audit_index.json`. Input identity includes the diagnostic dependency
manifest; artifact validation includes both diagnostics and detailed audit
hashes. Batches produced by earlier binder or workflow code are not compatible
caches.
`run_report.json` separates fresh timings from reused work, and evaluated
addresses from skipped inputs. The summary CSV includes evaluation status,
actual evaluated-rule counts, and skip reasons.
The saved [repair validation](outputs/evaluator_all500_workflow_v3/repair_validation.json)
compares all public entries and audit shards with preserved workflow v2. It
checks the exact expected transition IDs, unchanged unrelated facts and
decisions, source evidence, field explanations, scope notes, citations, retained
event qualifications, and artifact hashes. The
[per-address comparison](outputs/evaluator_all500_workflow_v3/residential_repair_comparison.csv)
lists the before/after counts. The original v2 baseline, counterfactual report,
and complete old/new audit shards are retained locally, not shipped in the
published package. With those historical artifacts available, reproduce the
comparison with:

```bash
PYTHONPATH=src .venv/bin/python scripts/validate_residential_repair.py \
  --output outputs/evaluator_all500_workflow_v3 \
  --baseline outputs/evaluator_all500_workflow_v2 \
  --experiment outputs/zero_applies_review_v1/counterfactual.json
```

The earlier single-fact experiment supplies a regression expectation, not legal
authority. A local 29-address pilot also checked representative real rules in
event mode, where unresolved event facts continued to produce `unknown`.
The earlier address-gate comparison against the citation-only run is also a
local historical validation, excluded from this publication.

Complete lookup coverage does not imply complete legal certainty or complete
the organizer's separate `changes.json` submission.

Historical presentation-only helpers and previous citation-only runs remain
local. Use the evaluator/batch command above to produce current workflow
outputs. The published first-ten preview omits its large detailed audit, so it
is not a reusable batch cache. The reproduction guide creates a fresh smoke
run with an audit before demonstrating compatible reuse.

### Compile the rule inventory

The resumable compiler converts the existing source-reviewed applicability
records into executable coverage and event predicates. It preserves the seven
pilot plans. It uses the configured model through the existing structured model
adapter; these are isolated data-generation calls with no tool execution.

```bash
PYTHONPATH=src .venv/bin/python -m rent_rules.compile_plans \
  --output outputs/evaluator_compilation_run \
  --workers 8 --reasoning-effort medium
```

The compiler preserves responses, failed drafts, source/criterion ledgers,
per-batch measurements, and instruction-sensitive model caches. Repeating a run
reuses compatible cached responses and revalidates them. Source text is
deduplicated by verified spans; repeated predicate templates are expanded by
code. A successful run writes `rule_plans.json` and `compilation_report.json` in
the chosen directory. Use the resulting bundle as the evaluator's `--plans`.
Inspect the compilation report before treating the bundle as complete; failed
batches are not replaced with invented legal unknowns.

New plans execute applicability and event triggers. Compliance procedures,
monetary formulas, and amount-only modifiers remain in `retained_conditions`;
they are not automatically calculated. Each criterion and unresolved source
question has a disposition ledger. Source-dependent exemption definitions stay
inside the relevant exemption, so uncertainty cannot become a confirmed
exemption through Boolean short-circuiting. Additional case facts are scoped to
their rule to prevent inconsistent legal definitions or enum domains from
silently sharing observations.

The coverage and temporal review manifests in `data/evaluation/` are bound to
source-rule hashes. Postprocessing separates assessor-use labels from legal
property types, normalizes construction-year thresholds to date intervals, and
implements reviewed relative-date predicates without replacing actual event
dates with the query date. Missing dates remain missing facts. Some historical
event clocks and calculation-only conditions still have explicit unresolved
computational gates; they are not inferred from address data. See the final
verification report for the retained limitations. Inventory completeness means
every rule has a plan, not that every condition or monetary calculation is
automated or that every rule's scope can be determined from an address alone.
