# Reproduce the Rental-Law Lookup — Workflow v3

This handoff reproduces the accepted **500-address, 466-rule lookup** for
**2026-10-01**, including the jurisdiction input check, residential-use repair,
source citations, and explanations for unresolved results. Run all commands from
the `RentAgent` repository root. Keep the organizer pack unchanged.

The reference result is `outputs/evaluator_all500_workflow_v3/lookups.json`,
published as the lossless
[lookups.json.gz archive](outputs/evaluator_all500_workflow_v3/lookups.json.gz).
See the [package instructions](outputs/evaluator_all500_workflow_v3/README.md)
to decompress and verify the original bytes. Reading the saved result requires
no source captures or model calls; fresh evaluation requires the inputs below.
The older `workflow_v2` files remain local historical comparison artifacts.

## 1. What makes this reproducible

The reproducible route is:

```text
Reviewed rules + reviewed executable plans + original source evidence
                          + fixed address CSV
                                  |
                    Input and evidence validation
                                  |
             Per-address jurisdiction check and fact binding
                                  |
                Deterministic three-valued evaluation
                                  |
           Deterministic explanation, citation, and ID formatting
                                  |
          lookups.json + paired rules + diagnostics + audit
```

AI was used in earlier interpretation and compilation work. The current lookup
query makes **zero model calls**. A colleague or AI coding assistant should run
the reviewed implementation and plans to obtain the same result. The same
model, natural-language rules, and prompt alone do not guarantee identical
decisions or explanation text.

There are two different tasks:

| Task | Reproduction expectation |
| --- | --- |
| Execute this frozen rule snapshot, plans, inputs, and code | Compare public lookup bytes with the hashes in section 6 |
| Re-extract laws, recompile plans, rewrite the evaluator, or regenerate explanations with AI | A new implementation or interpretation; compare it with this reference, but do not assume identical output |

This document starts from the already reviewed rule snapshot. It does not claim
that the extraction CLI recreates all earlier human/model review and repair
stages from raw legal documents. If a colleague has only the raw documents and
an AI model, also supply the files below before requesting exact reproduction.

## 2. Files to hand to the colleague

Preserve these relative paths and original bytes. Copy generated inputs
explicitly: a source-code-only Git checkout may omit them.

| Required file or directory | Role |
| --- | --- |
| `outputs/module_a/rules.json` | Reviewed 466 rules, internal IDs, applicability criteria, exact evidence, status, and citation metadata |
| `data/evaluation/rule_plans.json` | Reviewed executable conditions and branches for all 466 rules, with fact definitions, scopes, and evidence references |
| `outputs/address_facts/address_enrichment.csv` | The fixed 500 address rows, verification statuses, and original explanations |
| `data/evaluation/submission_rule_ids.json` | Persistent mapping to short submission IDs; never regenerate it by sorting hashes |
| `data/evaluation/address_field_dependencies.json` | Exact dependencies that connect decisive missing facts to year/unit explanations |
| `outputs/module_a/applicability_contract.json` | Evaluation contract against which the plans were compiled |
| `AGENTS.md` | Model-visible project instructions whose identity is checked against the compilation |
| `src/rent_rules/` | Evaluator, fact binder, Boolean/date logic, formatter, diagnostics, ID mapping, and package files |
| `scripts/evaluate_all_addresses.py` | Bounded-memory, resumable full-address runner |
| `requirements.txt` | Repository environment dependencies |
| `participant-final-no-hour16_v5/` | Unchanged organizer pack, including original source texts and output templates |
| `data/supplemental/D037.txt`, `D059.txt`, `D074.txt` | Exact supplemental source captures referenced by this plan bundle |
| `outputs/evaluator_all500_workflow_v3/run_identity.json` | Small frozen input/code identity manifest used by the preflight below |

Also include `tests/` to run the regression suite. The reference
`run_report.json`, `repair_validation.json`, public output files, and
`scripts/validate_residential_repair.py` are useful for inspection and deeper
comparisons. Running the historical repair validator additionally needs the
preserved v2 audits and the earlier counterfactual report; these are not needed
to generate a fresh lookup.

The GitHub package includes the final outputs, code, plans, tests, and small
reference identity/validation reports. It excludes old runs, failures, model
caches, and large detailed audits. Saved reports keep their original provenance
and may name unshipped local files. They are historical records, not complete
reusable batch caches. The published first-ten preview also omits its audit;
section 5 creates a new smoke run that can be reused safely.

**Source availability matters.** The current plan validation checks 2,300
evidence references against 51 distinct source bodies. It needs the three
supplemental captures listed above. D037 and D059 exist in the working
environment used for this result but are absent from the public handoff
described in `README.md`. Check that the colleague actually has those exact
captures. Re-downloading a current page can change its text and offsets.
Missing evidence is a failed preflight, not a reason to disable validation.

Do not reformat the input JSON, rewrite CSV values, convert source line endings,
change the instructions, or replace internal rules with the short-ID output
export. File hashes and source character spans are part of the identity.

## 3. Fixed evaluation behavior

These rules describe the implementation contract. The checked-in plans and
code specify the exact per-rule predicates and explanation wording.

### Query identity and address input check

- Query date: `2026-10-01`, regardless of execution date.
- Mode: `coverage`.
- No case-fact overlay and no live address enrichment in the reference run.
- Address IDs are exact CSV strings: `A0001`, `A0002`, and so on. Do not change
  `A0001` to `A00001` or infer an ID from a street address.
- For each row, check the literal value `jurisdiction_status == "verified"`
  before binding property facts or evaluating rules for that address.
- Any other value, including `partial`, produces `lookups[address_id] = []`.
  In `address_diagnostics.json`, set `status` to `skipped`,
  `rules_evaluated` to `0`, and preserve the original
  `jurisdiction_evidence_ref` as the reason. The empty lookup does not establish
  that the address is nonexistent or that no laws apply.

Global rule/plan/source validation still occurs before processing the rows.
The address check prevents per-address evaluation, not validation of the input
bundle itself.

### Bind verified facts without inventing legal eligibility

`property_use_validation_status=verified` and one of the following exact
`resolved_property_use` values establish the residential component of this
residential-rental lookup:

| Value | Interpretation and retained limit |
| --- | --- |
| `multifamily_residential` | Residential use; no specific tenancy inferred |
| `subsidized_multifamily` | Residential use; current program participation and statutory subsidy qualifications remain separate |
| `mixed_use_multifamily` | The residential rental component only; conclusions do not extend to the commercial premises |
| `specialized_residential` | Residential use; ordinary tenancy, care-institution licensing, and institutional exemptions remain separate |
| `residential_tic` | Residential use; tenancy-in-common ownership does not establish a tenant, lease, or ownership exemption |

Unknown, unverified, conflicting, and unsupported classifications do not become
known residential use. Keep the original subtype, evidence, and any conflict.
For the four additional subtypes, preserve the formatter's property-scope note
in every returned entry.

A verified construction year binds a construction-date interval covering that
year; it is not a certificate-of-occupancy date. A source-reported unit count
remains `source.reported_units`; it does not automatically become a building
count, rental-unit count, or the owner's portfolio size. A residential-rental
query premise does not prove an actual lease, deposit, notice, eviction, or
violation. Use the binder's fact names, scopes, and statuses exactly.

### Evaluate Boolean expressions, branches, and exemptions

Use the compiled `all` (AND), `any` (OR), `not`, and typed comparison predicates.
Conditions nested under AND remain cumulative; alternatives under OR remain
alternatives. The truth values are `true`, `false`, and `unknown`.

| A | B | A AND B | A OR B |
| --- | --- | --- | --- |
| true | true | true | true |
| true | false | false | true |
| true | unknown | unknown | true |
| false | false | false | false |
| false | unknown | false | unknown |
| unknown | unknown | unknown | unknown |

The operators are symmetric; `NOT unknown` is `unknown`. Missing information
does not mean false. Numeric/date intervals establish a comparison only when
their supported range determines it. Never force an `applies` to meet a
per-address count target.

For each branch, the implementation combines:

```text
coverage = coverage conditions
           AND NOT(any coverage-stage exclusion)
           AND applicable coverage-stage source gates

event = coverage AND event trigger
        AND NOT(any event-stage exclusion)
        AND applicable event-stage source gates

rule decision = jurisdiction AND any(branch decision for the selected mode)
```

A source gate is an unresolved legal-source question, not an ordinary satisfied
condition. If relevance is false, the gate contributes true. If relevance is
true or unknown, the gate contributes unknown. The complete stage follows the
three-valued AND rules, so an independently false condition can still make the
stage false. Keep fact uncertainty and source uncertainty distinct.
Independent rule branches combine by OR. An exemption affecting one branch
does not erase an independently established branch. A definitely established
exemption excludes its affected branch; a decisive unresolved exemption remains
unknown. Preserve branch details and retained conditions in the audit and
explanation.

In `coverage` mode, `applies` means the governing conditional protection is
established. Event triggers and event-specific exclusions may remain unresolved
and must be described. `event` mode requires those additional conditions and
will produce different results. Compliance and amount calculations are separate
dimensions: failure to comply does not make the governing duty disappear, and
an amount modifier is not automatically a blanket exemption.

### Public results and explanations

For an established decision, rule status `in_force` maps to `applies`; `pending`
and `not_yet_effective` retain those status labels. A decisive unresolved
condition or source question returns `unknown`. Rules outside the established
jurisdiction, failed proposals, government/court-only duties, and definitely
excluded decisions are omitted from the public array, with reasons in the
audit. All 466 inventory rules are routed for each eligible address; a smaller
returned array does not imply truncation.

`exempt` is an internal audit basis, not a public result. `conflict_flag` is a
separate Boolean, not `result: "conflict"`; it is not an additional row count.
Preserve the evaluator's stage-sensitive conflict logic. The public formatter
also accepts `superseded`, but this snapshot's evaluator does not emit that
result. Do not invent supersession output.

For a decisive missing rule input, consult the fixed field-dependency manifest.
Only when the relevant CSV source field is unverified and the fact remains
unresolved, include its original `year_built_explanation` or
`unit_validation_explanation`. Do not attach every missing property field to
every rule. An unknown alternative does not block a true OR branch.

Each lookup entry has exactly these four fields:

```text
team_rule_id, result, explanation, conflict_flag
```

The top-level JSON has `as_of` and `lookups`, where `lookups` maps exact address
IDs to arrays. Keep IDs from the stable registry, currently `r-0001` through
`r-0466`. Pair this lookup with the `rules.json` exported by the same run.

Every returned entry, including `unknown`, contains this citation in its
`explanation`, copied from that rule's metadata:

```text
Citation: {citation} [document {source_doc_id}; URL {source_url}; retrieved {retrieved_at}].
```

An `unknown` begins with `Unknown because ...`, gives its decisive unresolved
cause, and identifies missing facts or source questions with relevant details.
It also retains the statement that its citation identifies a candidate rule
and does not establish applicability. Missing citation metadata or a missing
unknown cause fails export. Do not add a new top-level `citation` field or ask
a model to rewrite the existing explanations.

For byte equality, preserve CSV address order, source-rule order within each
address, the formatter's English wording, UTF-8 JSON with `ensure_ascii=False`,
two-space indentation, and the final newline. Let the existing serializer do
this.

## 4. Environment and preflight

The recorded working environment used Python 3.8.9. The repository documents
Python 3.8+; use the project environment and verify the output hashes on the
colleague's machine. From the repository root, if an environment is needed:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

No model API key, model configuration, or new web retrieval is needed for the
lookup run. Installing dependencies is a separate environment setup step.

Run this read-only preflight. It anchors the input/code manifest, verifies every
listed file, validates all executable plans and their original evidence, and
checks the field-dependency manifest:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path
from rent_rules.evaluator import validate_plan_bundle
from rent_rules.field_diagnostics import load_field_dependencies

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

identity_path = Path('outputs/evaluator_all500_workflow_v3/run_identity.json')
assert sha(identity_path) == 'd65cfbf07a73ec07463c76df257a4ab107a1b43bcf407d1f1564237b3132aafb'
identity = json.loads(identity_path.read_text(encoding='utf-8'))
assert identity['as_of'] == '2026-10-01'
assert identity['mode'] == 'coverage'
for path, expected in identity['input_sha256'].items():
    assert sha(path) == expected, 'Input differs: ' + path
for name, expected in identity['evaluator_code_sha256'].items():
    assert sha(Path('src/rent_rules') / name) == expected, 'Code differs: ' + name
assert sha('scripts/evaluate_all_addresses.py') == identity['runner_sha256']
rules_path = Path('outputs/module_a/rules.json')
plans_path = Path('data/evaluation/rule_plans.json')
rules = json.loads(rules_path.read_text(encoding='utf-8'))['rules']
bundle = json.loads(plans_path.read_text(encoding='utf-8'))
plans, evidence = validate_plan_bundle(
    rules, bundle, sha(rules_path), '2026-10-01', Path('.'))
load_field_dependencies(
    Path('data/evaluation/address_field_dependencies.json'),
    sha(rules_path), sha(plans_path), plans)
assert len(rules) == len(plans) == 466
assert len(evidence) == 2300
assert len({item['source_doc_id'] for item in evidence.values()}) == 51
print('Preflight passed: fixed inputs/code, 466 plans, 2300 evidence checks.')
PY
```

For regression testing, if `tests/` was included:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

The accepted implementation passed 316 tests. Counts and test results document
this snapshot; they do not establish complete legal coverage.

## 5. Generate fresh outputs

### First-ten smoke run

Use a new, empty output directory. This explicitly selects A0001 through A0010
in the order of the first ten CSV rows and exercises both the skip behavior
(A0009) and the residential-use repair (A0006).

```bash
PYTHONPATH=src .venv/bin/python -m rent_rules.evaluator \
  --rules outputs/module_a/rules.json \
  --plans data/evaluation/rule_plans.json \
  --addresses outputs/address_facts/address_enrichment.csv \
  --rule-id-map data/evaluation/submission_rule_ids.json \
  --field-dependencies data/evaluation/address_field_dependencies.json \
  --source-root . --as-of 2026-10-01 --mode coverage \
  --address-id A0001 --address-id A0002 --address-id A0003 \
  --address-id A0004 --address-id A0005 --address-id A0006 \
  --address-id A0007 --address-id A0008 --address-id A0009 \
  --address-id A0010 \
  --output outputs/reproduction_first10_v3
```

Verify before scaling:

```bash
.venv/bin/python - <<'PY'
import hashlib
from pathlib import Path
p = Path('outputs/reproduction_first10_v3/lookups.json')
assert hashlib.sha256(p.read_bytes()).hexdigest() == 'c30400e85718172f497275c85141a39c29c3db8b1a4a77249129a794c3082038'
print('First-ten lookup exactly matches workflow v3.')
PY
```

For A0001 alone, use the same evaluator command with only `--address-id A0001`
and a different output directory. Its entries should equal the A0001 array in
the first-ten file; the whole-file hash will differ because the other addresses
are absent.

### All 500 addresses

```bash
PYTHONPATH=src .venv/bin/python scripts/evaluate_all_addresses.py \
  --output outputs/reproduction_all500_v3 \
  --batch-size 10 \
  --reuse-run outputs/reproduction_first10_v3
```

The runner fixes the input paths above, the date `2026-10-01`, and `coverage`
mode. It does not accept `--addresses`, `--plans`, or `--as-of`. The optional
`--reuse-run` here reuses only the newly generated, validated smoke run; omit it
to compute all 500 addresses fresh. Neither route reads reference lookup
decisions as evaluation inputs.

Completed batches can resume by repeating the **same full-run command** with
the same output directory. Reuse requires compatible input/code identities,
address order, and artifact hashes. Incomplete batches are preserved and retried
separately. If inputs or code changed, use a new output directory. The single
evaluator command rejects a nonempty output directory rather than overwriting
it; keep its successful smoke result and proceed to the full runner.

The accepted 500-address run took approximately 298 seconds locally, including
reuse of ten compatible addresses and fresh processing of the other 490. This
is a measurement, not a runtime guarantee; model calls remained zero. Detailed
audit serialization and source validation are included in the workflow.

## 6. Acceptance checks

Counts provide a quick diagnosis. SHA-256 comparison verifies the complete
public file, including explanations, citations, order, and serialization.

| Metric | First ten | All 500 |
| --- | ---: | ---: |
| Address rows | 10 | 500 |
| Evaluated | 9 | 477 |
| Skipped | 1 | 23 |
| Returned address-rule entries | 1,060 | 55,196 |
| `applies` | 257 | 14,331 |
| `unknown` | 803 | 40,865 |

A0001 has 54 applies / 107 unknown; A0006 has 12 / 74; A0009 is skipped.
All 477 evaluated addresses currently have at least one applies. This is an
observed consequence of the supplied facts and rules, not a rule to enforce.
The 1,736 full-run conflict flags are a subset of returned entries.

Run the following after the full run:

```bash
.venv/bin/python - <<'PY'
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

out = Path('outputs/reproduction_all500_v3')
expected = {
    'lookups.json': 'e8c7e594d8028b1f2810dcc132abb2e8c1ce270650b54d9f4b3c5518719a3862',
    'rules.json': 'efbb30e5aeb0c53780f817a4f753b17a3059934e79d164ca2c87e3b87d261823',
    'rule_id_map.json': '1b98a520778c706ec2fda84d438daef0360c6d5b729b10a86bdf66cc1ff4d023',
    'address_diagnostics.json': '243bdc602dd1277f80126a28adb62874aa05b885be941ca7d19a466e0b789757',
}
for name, digest in expected.items():
    assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest, name
payload = json.loads((out / 'lookups.json').read_text(encoding='utf-8'))
diagnostics = json.loads((out / 'address_diagnostics.json').read_text(encoding='utf-8'))['addresses']
with Path('outputs/address_facts/address_enrichment.csv').open(newline='', encoding='utf-8-sig') as handle:
    ids = [row['address_id'] for row in csv.DictReader(handle)]
assert payload['as_of'] == '2026-10-01'
assert list(payload['lookups']) == list(diagnostics) == ids
assert len(ids) == 500
assert Counter(item['status'] for item in diagnostics.values()) == {'evaluated': 477, 'skipped': 23}
entries = [entry for group in payload['lookups'].values() for entry in group]
assert Counter(entry['result'] for entry in entries) == {'applies': 14331, 'unknown': 40865}
assert sum(entry['conflict_flag'] for entry in entries) == 1736
for aid in ids:
    if diagnostics[aid]['status'] == 'skipped':
        assert payload['lookups'][aid] == []
        assert diagnostics[aid]['rules_evaluated'] == 0
    else:
        assert diagnostics[aid]['rules_evaluated'] == 466
print('Reproduction passed: exact public artifacts, address order, and expected counts.')
PY
```

`run_report.json` includes execution timings; `evaluation_audit_index.json`
includes output paths and batch reuse details. These metadata files can differ
between a reference run and a fresh run in another directory. Do not require
their full-file hashes to equal the reference merely to establish identical
lookups. The evaluator records hashes for the newly generated artifacts.

The historical repair validation additionally checked 50 audit batches and
222,282 rule decisions. Against v2, exactly 570 entries at 29 addresses changed
from unknown to applies; other evaluated addresses, rule membership, conflict
flags, and skipped-address reasons stayed unchanged. See
[repair_validation.json](outputs/evaluator_all500_workflow_v3/repair_validation.json).

## 7. Deliverables and troubleshooting

Deliver the new run's `lookups.json` **with its paired `rules.json`**. For GitHub,
the 167,558,063-byte lookup is stored as `lookups.json.gz`; decompression preserves
the exact hash. Also retain locally:

- `rule_id_map.json` for mapping public IDs to internal rules.
- `address_diagnostics.json` for the original reasons for skipped inputs.
- `address_summary.csv` for per-address counts and evaluation status.
- `evaluation_audit_index.json` and all referenced batch audit directories for
  branch decisions, evidence, and unresolved facts. If a batch was reused from
  the smoke run, include that directory as well.
- `run_identity.json` and `run_report.json` for input/code identity and timings.

The public package includes the small diagnostics, summary, identity, and report
files. Detailed audits and their index are excluded from GitHub. Keep them when
handing off a complete audit archive or a cache intended for reuse; the published
result alone does not contain that audit archive.

The evaluator produces the machine-readable summary CSV. The existing
`address_statistics.html` is a separate presentation of that CSV; it is not
automatically generated by the batch runner and is not required to reproduce
the lookup JSON. Likewise, `repair_validation.json` is generated by the separate
historical comparison script, not by ordinary lookup execution. This workflow
does not produce the organizer's separate `changes.json` submission.

| Symptom | Check and response |
| --- | --- |
| Hundreds of unknown entries for an unverified jurisdiction | Confirm the v3 code hashes and input check; the current workflow skips that row before rule evaluation |
| Verified special residential types have zero applies | Confirm `eval_facts.py` matches v3 and the property's use status is actually verified; do not force results |
| Original evidence span/hash failed | Recover the exact source capture, particularly supplemental D037/D059/D074; do not bypass the check |
| Rule snapshot / contract / instruction mismatch | Use the matching snapshot and plans; changing legal inputs requires a separate review and compilation |
| More unknowns with identical address facts | Check `coverage` versus `event`, compiled plan completeness, fact scopes, and source questions |
| Long internal IDs appear in the public output | Check the fixed registry and export adapter; do not edit internal rule IDs |
| Counts match but lookup hash differs | Compare input/code hashes, date, ordering, explanation formatting, encoding, and line endings; matching counts alone is insufficient |
| Output directory rejected | Preserve it; choose a new directory or resume a compatible full batch run |
| A new address/date gives different counts | The reference totals apply only to this fixed CSV and reviewed date; new facts or dates are a new evaluation |

## 8. Ready-to-use instruction for an AI coding assistant

Give the colleague this file and the handoff package, then use this instruction:

```text
Reproduce RentAgent workflow v3 by following LOOKUP_REPRODUCTION.md.
Read AGENTS.md and README.md first. Work from the repository root.
The target is the fixed 2026-10-01 coverage lookup using the supplied 466-rule
snapshot, reviewed executable plans, and unchanged 500-address CSV.

Run the documented preflight. If a required source or matching input is missing,
report the exact missing file or identity mismatch; do not synthesize data or
disable validation. Do not re-extract laws, recompile the plans, re-enrich the
addresses, or rewrite explanations with a model as part of this reproduction.

Use new output directories. Generate the first-ten smoke run, compare its exact
lookup hash, then run all addresses with compatible batch reuse. Preserve the
jurisdiction skip reasons, verified residential subtype scope, three-valued
AND/OR/NOT behavior, separate exemptions and event triggers, original citations,
and explicit unknown explanations. Use the fixed submission ID registry.

Run the acceptance checks and report their actual outcome. Return paths to the
new lookups.json, paired rules.json, address_diagnostics.json, address_summary.csv,
and run_report.json. Do not edit outputs merely to match expected counts.
If asked to implement a new evaluator instead, identify that as a separate task
and compare it against this frozen reference before claiming equivalence.
```
