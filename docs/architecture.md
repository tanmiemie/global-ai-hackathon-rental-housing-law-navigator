# Architecture and evaluation gates

The Rental Housing Law Navigator keeps legal evidence and property evidence on separate tracks until deterministic evaluation. This prevents a plausible-sounding model response from becoming an address-level legal conclusion.

## 1. Legal-source track

1. Load the organizer manifest and preserve source identity and retrieval metadata.
2. Split long documents into overlapping source spans.
3. Extract candidate rules with conditions, exemptions, dates, citations, and quotations.
4. Restore and validate each quotation against the source bytes.
5. Review source context, repair bounded structural defects, and consolidate duplicates without erasing distinct legal claims.
6. Verify status separately from effective date.
7. Compile the reviewed rule into typed `all`, `any`, `not`, comparison, and temporal expressions.

The frozen snapshot contains 466 reviewed rules. Exact source evidence, snapshot identity, query date, and plan identity are checked before evaluation.

## 2. Address-fact track

The 500 organizer addresses are enriched without using postal city as a substitute for legal jurisdiction.

- Census Geocoder and TIGER/Line support incorporated-place resolution.
- Organizer property fields remain preserved as observations.
- Official use-code definitions and use descriptions provide independent unit-count and property-use evidence.
- Public parcel records provide additional unit and construction-year observations.
- Compatible observations are retained; incompatible observations become explicit conflicts.
- Only `jurisdiction_status == verified` passes the automatic evaluation gate.

Construction year is not silently substituted for certificate-of-occupancy date. Reported units are not silently reinterpreted as owner portfolio size, rentable units, or another legal count scope.

## 3. Deterministic evaluator

For each verified address and jurisdictional candidate rule, the evaluator applies gates in order:

1. legal status;
2. effective date at the requested as-of date;
3. jurisdiction;
4. property and actor coverage;
5. event conditions;
6. exemptions and exclusions;
7. compliance or calculation branches where the supplied facts permit them.

The expression engine implements three-valued logic:

- `true`: the predicate is supported by usable evidence;
- `false`: the predicate is defeated by usable evidence;
- `unknown`: evidence is missing, conflicting, out of scope, or legally unresolved.

An unknown exemption is never assumed absent. A rule covering a property does not prove a violation, transaction, entitlement, or amount owed.

## 4. Outputs and auditability

The full run produces:

- paired `lookups` and `rules` exports;
- stable short submission IDs with a bidirectional ID map;
- address diagnostics for evaluated and skipped inputs;
- per-rule evaluation traces and evidence-bearing facts;
- address summaries and aggregate result counts;
- hashes for inputs, evaluator code, outputs, and audit artifacts;
- explicit runtime-model-call accounting;
- limitations that travel with the run.

Module C derives five fixed change cases from the same reviewed rules, address sample, legal-city mapping, and temporal semantics. It reports enacted, pending, not-yet-effective, and failed changes separately and preserves possible state/local conflicts for human review.

## 5. Product layer

The separate [frontend repository](https://github.com/tanmiemie/property-law-navigator) packages a deterministic presentation bundle for the live product. It provides:

- searchable address selection;
- mutually exclusive outcome counts;
- expandable result explanations and primary-source evidence;
- secondary property-fact provenance;
- interactive as-of dates for the five change cases;
- narrated 60-second technical and live-product tours.

The UI is an explanation surface over frozen artifacts. It does not perform browser-side legal inference.
