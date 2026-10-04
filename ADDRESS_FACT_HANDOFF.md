# Minimum Address Facts Handoff

Review date: **2026-10-03**. Legal query date: **2026-10-01**.

**First delivery: preserve the original address table, establish jurisdiction where possible, and return inexpensive additions already available from the same data sources.** Deeper research follows only when a specific rule decision needs it. Missing facts are acceptable; unsupported assumptions are not.

The address teammate supplies facts and evidence. Our side handles rule interpretation, status, fact mapping, matching, explanations, `lookups.json`, and change tests. This document is an integration plan; the address evaluator is not yet implemented. The teammate does not need to implement the rule engine or reproduce the 541 internal fact keys.

## Files to send the teammate now

| Priority | File | Purpose |
| --- | --- | --- |
| Required | This `ADDRESS_FACT_HANDOFF.md` | Scope, minimum return, and optional later requests |
| Required | [sample_addresses.csv](participant-final-no-hour16_v5/data/sample_addresses.csv) | The unchanged 500-row input and original facts; keep the same IDs |
| Optional | [address_profile.json](outputs/submission_readiness_20261003/address_profile.json) | Existing missing-field and conflict findings, including the NJ ZIP warning IDs; useful to avoid repeating diagnosis |

These two required files are enough to start. **Do not ask the teammate to process the full rules file or fact catalog.** We retain [rules.json](outputs/module_a/rules.json) and [applicability_fact_catalog.json](outputs/module_a/applicability_fact_catalog.json) for choosing subsequent requests. They may be shared as optional technical references, not an investigation checklist. The readiness plan is not needed for this handoff.

## Exact minimum required return

Return **one file**, suggested name `address_enrichment.csv`, with one row per original address ID: **500 rows and 500 unique IDs** for the complete first delivery. Inline source references keep this minimum to one file; an existing shared evidence list is also accepted. A smaller batch may be sent earlier. An existing JSON export with equivalent information is also acceptable; our side will adapt it. This is an internal input file, not the organizer's `lookups.json`.

Suggested CSV header:

```csv
address_id,state,legal_city,county,incorporation_status,jurisdiction_status,evidence_ref,retrieved_at,notes
```

| Column | Minimum requirement |
| --- | --- |
| `address_id` | Required on every row; exactly the original ID |
| `state` | State established by the jurisdiction check; blank if unresolved. The originally supplied state remains available in the input CSV. |
| `legal_city` | Verified municipality/place name; blank if unresolved or unincorporated. Do not copy a postal city as a verified result. |
| `county` | Include when the same lookup provides it; no separate county search is required for first delivery. Blank means not established. |
| `incorporation_status` | `incorporated`, `unincorporated`, or `unknown` |
| `jurisdiction_status` | Required on every row: `verified`, `partial`, `unknown`, or `conflicting`. `verified` means state and municipality/unincorporated status are established; an optional missing county does not defeat that status. `partial` means only some components are established. |
| `evidence_ref` | Required for established components or reported conflicts: source URL plus identifying record/match information, or a shared evidence ID. Blank is acceptable when nothing was established. |
| `retrieved_at` | Date/time of the supporting lookup, either here or in the referenced shared evidence. Do not invent a lookup date for an unattempted record. |
| `notes` | Required for partial, unknown or conflicting results: briefly say what remains unresolved and why. For an unattempted lookup, say `not checked`; for a conflict, preserve both alternatives and their evidence here or by reference. |

**The required work is a reasonable bulk jurisdiction pass and a traceable return, not successful resolution of every address.** Individual failures may remain `unknown`; all-unknown output caused by a source failure should state that failure. A partially known address is useful: return the established components instead of discarding the row.

Original `year_built`, `units`, `use_code`, `use_description`, address text and provenance are joined by `address_id`; do not retype or re-research them to satisfy this format. Any unresolved interpretation remains unresolved. New property facts are welcome when inexpensive, but are **not required for the minimum return**. Coordinates, ZIP completion, normalized addresses, APNs, CO dates, owner details and program checks are not mandatory first-delivery fields.

This minimum lets us begin matching and identify the next useful questions. It does **not** guarantee a conclusive applicability result for every rule.

## Why this is the minimum useful scope

The current [rule export](outputs/module_a/rules.json) contains 466 records, including 457 marked `in_force`. The [original address table](participant-final-no-hour16_v5/data/sample_addresses.csv) already supplies:

| Existing information | Addresses with a value | First-pass treatment |
| --- | ---: | --- |
| Address ID, street address, postal city, state | 500 | Preserve; distinguish postal labels from verified jurisdiction |
| Use code, use description, source dataset, retrieval date | 500 | Reuse and interpret consistently by dataset |
| Construction year | 288 | Reuse as a year, with original provenance |
| Numeric unit count | 258 | Reuse with its documented counting scope |
| ZIP | 370 | Fill only if needed to resolve the address |

These are supplied observations, not newly verified facts. Do not re-research every populated field. The 212 missing years and 242 missing unit counts are **not** a mandatory manual lookup list. Existing descriptions sometimes support a useful range after their definitions are checked.

Rule references help prioritize but do not measure how many decisions a field will settle. For example, among current in-force records, 72 explicitly request `property.building_unit_count`, 64 request `property.certificate_of_occupancy_date`, and 87 request `owner.occupies_property`. These counts overlap and include conditional branches. Owner and CO facts can matter greatly; postponing expensive searches does not establish that an exemption is absent. No address-level coverage percentage has been measured.

## First-pass workflow

### 1. Preserve IDs and establish jurisdiction

- Return exactly the original **500 `address_id` values**, including unresolved records. Keep the organizer CSV unchanged; reference its rows rather than copying every original value into a new format.
- Return verified state and legal city or unincorporated status, with county when available and a source/match reference. If only the state is established, keep the city unresolved so state-level work can proceed.
- Reuse existing reliable matches. A documented municipal dataset boundary plus a matching property record may already establish jurisdiction; otherwise use a batch address/boundary lookup and review ambiguous matches.
- Postal city, ZIP, an organizer sample-group label, or coordinates without boundary evidence are not sufficient by themselves. A normalized address, coordinates and APN are useful if returned by the same lookup, but do not acquire them separately when the match is already established.

### 2. Reuse the supplied property facts

- Keep `year_built`, `units`, `use_code` and `use_description`, including their original source and unresolved conflicts.
- Before deriving a new value, check the relevant dataset field definition and reuse the mapping across its records. Share an existing mapping/source when available. There are seven source datasets, but verifying all seven dictionaries is not required for the minimum jurisdiction return; an uninterpreted code can remain unchanged.
- Prefer a supported range over a costly exact count when the relevant threshold can be decided from that range. For example, a verified 7–30-unit classification can settle a greater-than-four test; it cannot settle a greater-than-ten test. Record the classification's building/parcel/unit scope.
- Preserve mixed-use, subsidized, senior-housing, cooperative and similar labels as existing leads. They do not independently prove every legal exemption or program qualification.

### 3. Make only inexpensive additions

If the same bulk dataset or matched property record provides a missing year, unit count, use, or program identifier, include it with evidence. Batch the work by source and reuse results. Keep any reliable extra facts already collected; do not discard teammate work because it is outside this minimum.

**Stop the first pass after the bulk lookup attempt and recording any inexpensive additions you chose to make.** For missing values that require a separate manual document search, return `unknown` with a reason such as not researched beyond the bulk pass, no match, or conflicting evidence. Send the first available batch for integration without waiting for every record or data dictionary to be resolved.

## Where to spend the next increment of effort

These are priorities based on the supplied table, not verified jurisdiction assignments or requirements to research every row.

| Sample group | Cheapest useful next step | Work to defer |
| --- | --- | --- |
| San Francisco, 80; Cambridge, 50 | Reuse existing unit counts and nearly complete years; check jurisdiction and obvious contradictions | All 130 missing ZIPs unless they block matching; broad property-history searches |
| Los Angeles, 80 | Reuse the 74 years and 77 unit counts already supplied; use validated apartment classifications where sufficient | Exact CO/permit dates and replacement-unit history until a relevant branch needs them |
| Berkeley, 40 | Check the supplied 5+-unit classification once against source definitions; use a supported lower bound where sufficient | Forty separate searches for exact unit counts or construction dates |
| San Diego, 50 | Reuse all 50 unit counts and the supplied use information | All 50 missing construction years: obtaining a year does not establish the CO date used by relevant rules |
| Boston, 60 | Decode the 28 `APT 7-30 UNITS` descriptions as ranges if supported; retain the 26 Section 8 labels and one elderly-home label as leads | Exact counts for every building; full subsidy-contract research without a relevant unresolved branch |
| New Jersey, 140 | Prioritize address/jurisdiction anomalies; interpret reliable use/description fields in batches | Guessing units from opaque strings, or 139 individual searches merely to fill the numeric-unit column |

Carry these existing issues forward:

- **A0227:** `units=2` conflicts with `13B-93U-2C-G`; **A0398:** `units=5` conflicts with `TIC Bldg 4 units or less`. Flag both observations; do not silently select one.
- **27 NJ rows** have ZIP prefixes outside 07/08. These are matching warnings, not proven corrected locations. Full IDs are in the [address profile](outputs/submission_readiness_20261003/address_profile.json).
- Boston neighborhood labels and San Ysidro require actual jurisdiction evidence; do not use postal labels as legal city names.
- **A0107 and A0432** have LA construction year 1978 only. Request a more precise date only when the relevant rule requires it. A year cannot establish which side of a cutoff within that year applies, or substitute for a CO date.

## Second delivery: only targeted missing facts

Our side will first select the relevant jurisdiction, legal status, actor and rule branch using the available facts. We will then send a short request containing **address IDs, affected rule IDs, the missing fact and its scope/date, and why it could change the result**. Legal definitions and legal-status gaps stay with our side; the teammate supplies factual dataset definitions.

Prioritize requests that can settle several relevant unresolved decisions through one accessible source. Stop pursuing a branch once a supported fact decides it, or when another unresolved legal issue means the proposed search cannot settle it. Do not treat a high reference count alone as proof of value.

| Deferred information | Request only when |
| --- | --- |
| CO or exact construction date | A relevant rule uses that specific date and the existing evidence cannot settle its threshold. Use construction evidence only for a construction test. |
| Owner occupancy, entity type, LLC membership or holdings | A still-possible exemption depends on that fact. If another established condition defeats that exemption, do not investigate its remaining owner facts. |
| Subsidy, affordability covenant or special program | A relevant branch depends on the exact program, restricted units or dates. Existing labels are leads; missing labels do not prove no program exists. |
| Separate title, ADU/shared layout, conversion or replacement history | A remaining property-specific branch requires the detail. |
| Local rent-program coverage/registration record | An accessible record can resolve a particular coverage question. Preserve its date and unit scope for our interpretation. |
| Bedroom count or other amount modifiers | A requested calculation depends on it; it is not a prerequisite for every general coverage decision. |

Do not conduct a blanket owner investigation, acquire personal owner names, search all permits, or reconstruct every property's history for the first delivery. Rent, deposits, lease dates, notices, tenant characteristics and software-use conduct generally require later user/transaction evidence; they are not address-enrichment tasks. Pending/future rules require extra fact collection only for a requested forecast or change test, rather than current obligations.

## Optional later files and information

**Do not prepare these files or investigate all these fields now.** Return them only for inexpensive additions already collected or after we send a targeted request from the preceding section. The filenames and column names are suggestions; existing equivalent exports are accepted.

| File | When to return it | Information needed |
| --- | --- | --- |
| `address_fact_updates.csv` or JSON equivalent | New or corrected property facts are available | `address_id`, `field_name`, `value` or bounds, `status`, `subject_scope`, `date_precision`, `effective_or_observed_date`, `evidence_ref`, and a note for derivations/conflicts. Populate only relevant fields; each row describes one observation. |
| `evidence_sources.csv` or a small JSON/text source list | Many observations reuse a source and a shared list saves work | Evidence ID, URL or dataset/record ID, relevant source field/passage or boundary-match method, retrieval date, and source effective date if known. Inline evidence is sufficient; a separate file is not required. |
| Dataset field definitions or a mapping file already used by your pipeline | You derive a use type, unit range, or another value from an encoded field | Dataset name/version, original field/code, documented meaning, derived value/range and scope, and dictionary source. Share the mapping once, not once per address. |

For later requests, the fact must describe the correct subject and date:

- **Dates:** distinguish construction completion from CO issuance; keep year/month/day precision and the building or unit covered.
- **Counts:** distinguish building, parcel, rented units and owner holdings; a supported lower/upper bound may be sufficient.
- **Owner facts:** supply only the requested entity/occupancy/holdings fact with its relevant date and population. A mailing-address match alone does not establish actual owner occupancy.
- **Programs:** preserve the specific program or covenant, affected units and effective period when available; retain an assessor label as a lead if the qualification is unverified.
- **Title, layout, permits and history:** identify the affected property/unit and event/date, and return the record needed for the requested branch.
- **Local registration/coverage:** preserve the system's actual result, checked date and property/unit scope for our interpretation.

A source URL and record reference are normally enough; there is no requirement to download every PDF or build a document archive. Existing source captures can be shared when helpful. Unchanged original fields need no new citations or copied evidence objects: reference the supplied CSV row and its existing provenance.

For fact updates, use `known`, `unknown`, `not_applicable` or `conflicting` as the observation status. A known false value is different from unknown. An omitted optional field means **not provided**, never false. Preserve original versus derived values and competing observations; do not use facts observed after the query date as historical facts without support. These fact-observation statuses are separate from the jurisdiction statuses in the first file.

## Completion for this teammate's first pass

The first pass is complete when all 500 IDs are accounted for, jurisdiction results or explicit gaps are returned, existing facts are traceable, inexpensive additions have evidence, and known conflicts remain visible. **It does not require every field to be populated or every rule to receive a conclusive result.**

On our side, missing facts that matter remain `unknown`; irrelevant missing facts do not block an independently supported decision. Distinguish a conditional protection from an actual triggered duty or violation. This approach reduces research cost while preserving uncertainty, rather than increasing the apparent rule count by assuming unknown exemptions away.
