# Rental Housing Law Navigator 🏠⚖️

An auditable system that turns official rental-housing law and verified property facts into address-level rule screening—without pretending uncertainty is a legal verdict.

MIT Rental Housing Law Navigator Challenge · legal snapshot as of **2026-10-01** · **[live product](https://rental-law-buddy.lovable.app/)**

## The problem

US rental housing law is layered across states, counties, and cities. Whether a rule may apply can depend on the exact address, city boundary, unit count, building age, property use, effective date, and an exemption hidden deep in the text.

A search result is not enough. A useful answer must show:

- which jurisdiction and property facts were used;
- which conditions were established and which remain unknown;
- the exact source text, citation, retrieval date, and legal as-of date;
- whether the law is enacted, pending, not yet effective, or failed; and
- when conflicting facts or legal sources require human review.

## What the Navigator does

Enter one of the 500 supplied addresses. The Navigator resolves its legal jurisdiction, reconciles available property facts, routes it to the relevant reviewed rules, and evaluates explicit `AND`, `OR`, and `NOT` conditions using three-valued logic: **true, false, or unknown**.

The demo in one line: address **A0001** resolves to Los Angeles, returns **161 candidate rules**, and lets a reviewer trace every displayed outcome back to the quoted source and the facts used—while preserving unknowns and conflicts instead of guessing.

## Live

- **Product:** [rental-law-buddy.lovable.app](https://rental-law-buddy.lovable.app/)
- **60-second live demo:** [rental-law-buddy.lovable.app/live-demo](https://rental-law-buddy.lovable.app/live-demo)
- **60-second technical tour:** [rental-law-buddy.lovable.app/tech-video](https://rental-law-buddy.lovable.app/tech-video)
- **Frontend source:** [tanmiemie/property-law-navigator](https://github.com/tanmiemie/property-law-navigator)

## The result

| Proof point | Result |
|---|---:|
| Reviewed rules | **466** |
| Organizer addresses | **500** |
| Addresses passing the verified-jurisdiction gate | **477** |
| Address-rule outcomes | **55,196** |
| Runtime model calls in the evaluator | **0** |
| Deterministic change cases | **5** |
| Automated tests | **316 passing** |

The reviewed rule snapshot contains **457 in-force**, **6 not-yet-effective**, **2 pending**, and **1 failed** records. Twenty-three addresses fail closed at the jurisdiction gate; they are logged rather than silently assigned to a city.

## How it stays honest

The model helps read source documents. It does not decide an address-level outcome at runtime. Everything that could silently invent certainty is constrained by deterministic code.

- **Every rule stays attached to evidence.** Reviewed records preserve an exact quotation, source document, URL, retrieval timestamp, citation, and as-of date.
- **Legal prose becomes executable logic.** All 466 rules have reviewed applicability plans composed from typed comparisons and explicit `all`, `any`, and `not` expressions.
- **Missing is not false.** Three-valued evaluation prevents an absent fact from being treated as a failed condition or a nonexistent exemption.
- **Jurisdiction fails closed.** Only addresses with verified legal-city resolution enter automatic evaluation; skipped addresses retain their diagnostic evidence.
- **Property facts are reconciled, not guessed.** Census/TIGER jurisdiction evidence, organizer data, official use-code definitions, use descriptions, and public parcel records are retained with their provenance and conflicts.
- **Status and effective date are separate gates.** Pending, failed, and not-yet-effective changes never become current obligations merely because they appear in the corpus.
- **Conflicts remain visible.** Lookup explanations identify conflicting observations or source questions and route them to human review.
- **Every run is reproducible.** Reports retain input hashes, evaluator-code hashes, output hashes, timing, diagnostics, and zero-runtime-model-call accounting.

This system provides transparent candidate-rule screening. It is **not legal advice or a compliance certification**.

## Architecture

```text
Official legal sources
        │
        ▼
Semantic source spans → extraction → quote validation → reviewed rule snapshot
                                                              │
Address sample → Census/TIGER + parcel enrichment → verified facts
                                                              │
                         reviewed rules + verified facts ──────┤
                                                              ▼
                                          deterministic evaluator
                                       (true / false / unknown)
                                                              │
                           lookups + audit logs + change cases + UI
```

The extraction path is model-assisted and evidence-checked. The query path is deterministic Python and makes zero model calls. See [the architecture walkthrough](docs/architecture.md) for the data contracts and evaluation gates.

## Five change cases

| Case | What a correct system demonstrates |
|---|---|
| **T1 · California AB 325 / SB 763** | 250 California addresses move from not-yet-effective on 2025-12-31 to applies on 2026-01-02. |
| **T2 · Hoboken and Jersey City** | Each local ban applies only inside its own legal-city boundary; Newark stays excluded. |
| **T3 · New Jersey FAIR Act** | 140 addresses move to applies on 2027-07-02; 90 Hoboken/Jersey City records retain a possible state/local conflict flag. |
| **T4 · Massachusetts S.2983 / H.5222** | Both bills remain pending; 110 addresses are shown only as the hypothetical affected set if enacted. |
| **T5 · Massachusetts ballot question** | The failed proposal produces no current rent cap and an empty affected set. |

## Quickstart

Python **3.8+** is required. Validation, evaluation, and tests run without model calls.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# Run all automated tests
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v

# Evaluate one address against all reviewed plans
PYTHONPATH=src .venv/bin/python -m rent_rules.evaluator \
  --rules outputs/module_a/rules.json \
  --plans data/evaluation/rule_plans.json \
  --addresses outputs/address_facts/address_enrichment.csv \
  --address-id A0001 --as-of 2026-10-01 --mode coverage \
  --output outputs/evaluator_A0001

# Rebuild all five organizer-defined change cases
PYTHONPATH=src .venv/bin/python scripts/run_change_tests.py
```

For the exact full-population workflow, hashes, batch behavior, and acceptance checks, use [LOOKUP_REPRODUCTION.md](LOOKUP_REPRODUCTION.md).

## Submission artifacts

| Artifact | Purpose |
|---|---|
| [`outputs/module_a/rules.json`](outputs/module_a/rules.json) | Reviewed 466-rule internal snapshot with evidence and status extensions |
| [`data/evaluation/rule_plans.json`](data/evaluation/rule_plans.json) | Executable applicability plans for all reviewed rules |
| [`outputs/address_facts/`](outputs/address_facts/) | 500-address enrichment, provenance, mappings, and reconciliation audits |
| [`outputs/evaluator_all500_workflow_v3/`](outputs/evaluator_all500_workflow_v3/) | Paired lookup/rule export, diagnostics, audit index, hashes, and run report |
| [`outputs/module_c/changes.json`](outputs/module_c/changes.json) | Organizer-formatted results for T1–T5 |
| [`outputs/module_c/change_test_audit.json`](outputs/module_c/change_test_audit.json) | Reproducible case assertions, address sets, aliases, and limitations |

## Repo map

| Path | What it contains |
|---|---|
| `src/rent_rules/` | Extraction, evidence validation, typed facts, three-valued logic, and evaluator |
| `scripts/` | Source capture, all-address evaluation, change tests, and repair validation |
| `data/evaluation/` | Reviewed rule plans, fact catalog, source evidence, and stable submission IDs |
| `data/supplemental/` | Supplemental official source captures and capture logs |
| `outputs/module_a/` | Reviewed rule corpus and status-validation artifacts |
| `outputs/address_facts/` | Jurisdiction and property-fact enrichment with audit trails |
| `outputs/evaluator_all500_workflow_v3/` | Reproducible full lookup package |
| `outputs/module_c/` | Five deterministic change cases and audit output |
| `tests/` | 316 deterministic tests across evidence, dates, facts, logic, output, and replay safety |
| `participant-final-no-hour16_v5/` | Unmodified organizer challenge pack, schemas, templates, and sample data |

## Deeper documentation

- [Architecture and evaluation gates](docs/architecture.md)
- [Full lookup reproduction guide](LOOKUP_REPRODUCTION.md)
- [Address-fact methodology and outputs](outputs/address_facts/README.md)
- [Full evaluator package](outputs/evaluator_all500_workflow_v3/README.md)
- [Address-fact handoff contract](ADDRESS_FACT_HANDOFF.md)

The legal corpus is a dated research snapshot, not continuous legal monitoring. An absent source or unresolved fact is a coverage limitation—not evidence that no law applies.
