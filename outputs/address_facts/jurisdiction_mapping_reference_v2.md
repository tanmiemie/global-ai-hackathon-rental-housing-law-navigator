# Legal Jurisdiction Mapping Reference — Version 2

## 1. Purpose

This process converts a rental-property address into the state and city jurisdictions that may supply applicable housing-law rules.

It answers only:

> Which legal state and incorporated city contain this property?

It does not decide whether a particular law applies. Rule applicability must be evaluated separately using effective dates, property characteristics, coverage tests, and exemptions.

The process is designed for transparency. Every final jurisdiction must retain enough evidence to reproduce or review the result.

## 2. Core Principles

1. Preserve the original input exactly as received.
2. Do not treat postal city or ZIP code as the final legal jurisdiction.
3. Use a geocoded point and an official incorporated-place boundary whenever possible.
4. Use an originating municipal property dataset as an accepted fallback when address geocoding fails.
5. Use parcel coordinates or parcel jurisdiction fields when street-address information is incomplete.
6. Never silently overwrite a conflicting or missing value.
7. Return `needs_review` when the available evidence cannot support one jurisdiction.
8. Keep the user-facing result simple while retaining detailed evidence in an audit log.

## 3. Inputs

The mapping process accepts these fields when available:

- `address_id`
- `street_address`
- `postal_city`
- `state`
- `zip`
- `source_dataset`
- property attributes that may help identify a source parcel, such as `units`, `use_code`, or parcel identifiers

### Input-field roles

| Field | Role |
| --- | --- |
| `street_address` | Primary address input for geocoding and parcel lookup |
| `postal_city` | Search hint and candidate jurisdiction; not conclusive by itself |
| `state` | State constraint and consistency check |
| `zip` | Search hint and data-quality check; not a legal-boundary determinant |
| `source_dataset` | Identifies a possible authoritative fallback and supports provenance |
| Property attributes | May disambiguate parcels when the street address is incomplete |

## 4. Resolution Workflow

### Step 1 — Preserve and normalize

Store the original fields without modification. Create separate normalized working values:

- trim whitespace;
- standardize state abbreviations;
- normalize common street suffixes and ordinal forms;
- split compound addresses such as `600 JACKSON/601 HARRISON`;
- split address ranges when required for geocoding;
- preserve the relationship between all normalized variants and the original record.

### Step 2 — Primary address geocoding

Submit the normalized street address, postal city, state, and ZIP to the U.S. Census Geocoder.

Record:

- match status;
- match type;
- standardized matched address;
- returned ZIP code;
- longitude and latitude;
- benchmark and vintage;
- retrieval time.

If the result is `No_Match` or `Tie`, retry with controlled transformations:

1. remove a suspicious ZIP code;
2. expand street abbreviations;
3. split compound or ranged addresses;
4. query each resulting address independently.

Do not discard the first attempt. Log every attempt.

### Step 3 — Legal-boundary resolution

For each successfully geocoded point, query the U.S. Census Bureau TIGERweb `Incorporated Places` layer.

- If exactly one incorporated place contains the point, use that place as `legal_city`.
- Use the containing state as `legal_state`.
- Save the place name, place GEOID, coordinates, TIGERweb vintage, and source URL.
- If address variants return different legal cities, set `resolution_status = needs_review`.
- If variants return different addresses or ZIP codes but the same legal city, the jurisdiction may be resolved while the address or ZIP remains ambiguous.

### Step 4 — Municipal-source fallback

If Census cannot geocode the address, an originating official municipal property dataset may resolve the city when the dataset's geographic scope is unambiguous.

Examples:

- Boston Property Assessment → Boston, Massachusetts
- Cambridge Property Database → Cambridge, Massachusetts
- DataSF property data → San Francisco, California

Record `resolution_method = municipal_source` and retain the dataset name and retrieval date. Do not label this method as a Census boundary match.

### Step 5 — Parcel fallback

When an address is incomplete or ambiguous, query the named official parcel source.

Use, in order of preference:

1. parcel centroid or polygon intersected with an official municipal boundary;
2. an official parcel jurisdiction field;
3. a unique situs address recovered from the parcel record.

Property attributes such as unit count and official use code may be used to identify the correct parcel, but they must not independently determine the jurisdiction.

If multiple parcel candidates share the same jurisdiction, the jurisdiction may be resolved while the precise address remains ambiguous.

### Step 6 — Final decision

Populate the final business fields only after evaluating the available evidence.

| Status | Definition |
| --- | --- |
| `resolved` | Available official evidence supports one legal state and city |
| `needs_review` | Evidence is missing, conflicting, or supports more than one legal jurisdiction |

Do not expose internal labels such as `boundary_verified` as a separate user-facing status. Store the verification method in the audit log.

## 5. Final Business Fields

| Field | Definition | Example |
| --- | --- | --- |
| `legal_state` | Final two-letter state abbreviation | `NJ` |
| `legal_city` | Final incorporated-place name | `Newark` |
| `jurisdiction_key` | Stable rule-matching key | `Newark, NJ` |
| `resolution_status` | `resolved` or `needs_review` | `resolved` |
| `resolution_method` | Highest-quality method supporting the result | `census_tiger` |

Recommended `resolution_method` values:

- `census_tiger`
- `municipal_source`
- `official_parcel_boundary`
- `official_parcel_jurisdiction`
- `manual_review`

## 6. ZIP-code Fields

ZIP codes support address matching but do not define legal jurisdiction.

Keep the original and resolved values separately:

| Field | Definition |
| --- | --- |
| `input_zip` | ZIP code supplied in the original record |
| `resolved_zip` | ZIP code returned by the selected standardized address or official parcel record |
| `zip_status` | Relationship between the two values |

Recommended `zip_status` values:

- `matched`
- `corrected`
- `filled_from_official_source`
- `ambiguous`
- `unresolved`

An incorrect or ambiguous ZIP does not invalidate an otherwise verified legal city.

## 7. Audit Log Design

Use an append-only audit table. Store one row for every resolution attempt, not merely one row per address.

### Required audit fields

| Field | Purpose |
| --- | --- |
| `audit_id` | Unique attempt identifier |
| `address_id` | Links the attempt to the property record |
| `attempt_number` | Orders attempts for the address |
| `attempted_at` | UTC timestamp |
| `input_street_address` | Original street address |
| `input_postal_city` | Original postal city |
| `input_state` | Original state |
| `input_zip` | Original ZIP |
| `query_street_address` | Normalized or split address submitted in this attempt |
| `query_city` | City sent to the source |
| `query_state` | State sent to the source |
| `query_zip` | ZIP sent to the source; blank when deliberately omitted |
| `source_name` | Census Geocoder, TIGERweb, NJ Geocoder, SanGIS, etc. |
| `source_url` | Official endpoint or dataset page |
| `source_version` | Benchmark, vintage, publication date, or dataset version |
| `source_retrieved_at` | Retrieval timestamp |
| `match_status` | Match, tie, no match, parcel match, etc. |
| `matched_address` | Standardized or recovered address |
| `matched_zip` | ZIP returned by the source |
| `longitude` / `latitude` | Returned or derived coordinates |
| `boundary_place_name` | Incorporated place containing the point |
| `boundary_place_geoid` | Official place identifier |
| `candidate_count` | Number of candidates returned |
| `selected_candidate` | Whether this attempt supplied the selected evidence |
| `decision` | `accepted`, `rejected`, or `needs_review` |
| `decision_reason` | Plain-language explanation |

### Audit rules

- Never delete an unsuccessful attempt.
- Never replace the original address or ZIP with corrected values.
- Store queries without a ZIP as separate attempts.
- For a split address, create one attempt for every component address.
- Record all candidates when the source returns a tie.
- A later successful attempt does not erase earlier failures.
- Save enough source/version information to reproduce the result later.
- Do not store private owner information that is unnecessary for jurisdiction resolution.

## 8. Current 500-address Result

After Census/TIGER boundary checks, municipal-source fallback, controlled address retries, the NJ official geocoder, and official SanGIS parcel review:

| Result | Count |
| --- | ---: |
| `resolved` | 500 |
| `needs_review` for legal jurisdiction | 0 |
| Confirmed legal-city conflicts | 0 |

Two records retain non-jurisdiction ambiguity:

- `A0279`: two Census address candidates with different ZIP codes; both are inside Jersey City, New Jersey. Set `zip_status = ambiguous`.
- `A0346`: two SanGIS parcels match the available street, ZIP, use code, and unit count; both have San Diego jurisdiction. Set `street_number_status = ambiguous`.

These ambiguities do not change the resolved legal city.

## 9. Current Data-quality Findings

For the 500-address dataset:

- 268 original ZIP codes matched the Census standardized address;
- 89 original ZIP codes differed from the Census standardized address;
- 130 original ZIP codes were missing;
- 13 original ZIP codes could not be evaluated in the initial batch because the original address did not geocode.

ZIP corrections must be logged separately from jurisdiction decisions.

## 10. Rule-engine Interface

For a property resolved to `Newark, NJ`, the rule engine should evaluate all applicable legal layers supported by the corpus, for example:

- New Jersey state rules;
- Newark city rules;
- any supported county layer, if county ordinances are included later.

Jurisdiction resolution identifies candidate legal layers. It does not determine final legal coverage and must not be presented as legal advice or a compliance certification.

## 11. Official Sources

- U.S. Census Geocoder API: <https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html>
- Census TIGERweb Current MapServer: <https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer>
- TIGERweb Incorporated Places layer: <https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/28>
- New Jersey Geocoding Service: <https://geo.nj.gov/arcgis/rest/services/Tasks/NJ_Geocode/GeocodeServer>
- NJOGIS Parcels and MOD-IV Composite: <https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/Parcels_Composite_NJ_WM/FeatureServer>
- SanGIS Parcels: <https://geo.sandag.org/server/rest/services/hosted/Parcels/FeatureServer/0>

