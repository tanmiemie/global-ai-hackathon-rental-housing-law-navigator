# Legal Jurisdiction Mapping Reference

## 1. Purpose

This process converts the provided address records into city-level legal jurisdictions that can be used for rule matching.

This version answers only: “Which state and city jurisdiction governs this address?” It does not determine whether a particular rental-housing rule applies. Building age, unit count, property use, owner type, effective dates, and exemptions are evaluated later by the rule engine.

## 2. Inputs and Outputs

Input file: `data/sample_addresses.csv`

Input fields used by the mapping process:

- `street_address`: Preserved as the primary address and as the basis for future geographic verification.
- `postal_city`: Used to identify the city and known neighborhood aliases.
- `state`: Used to disambiguate city names and generate the state-level jurisdiction.
- `zip`: Not used for the current mapping because some ZIP codes in the sample conflict with the address, city, or state.

Output file: `data/sample_addresses_with_jurisdiction.csv`

Added fields:

| Field | Meaning | Example |
| --- | --- | --- |
| `legal_state` | Two-letter state abbreviation | `MA` |
| `legal_city` | Official city name used for city-law matching | `Boston` |
| `jurisdiction_key` | Stable rule-matching key in `City, ST` format | `Boston, MA` |
| `resolution_status` | Whether the jurisdiction was successfully resolved | `resolved` |

## 3. Current Mapping Logic

The mapping process follows these steps:

1. Trim whitespace from `postal_city` and standardize `state` as an uppercase two-letter abbreviation.
2. Resolve known neighborhood names and non-independent postal place names first.
3. Match all remaining records against the standard cities included in this challenge.
4. When a unique match is found, populate `legal_state`, `legal_city`, and `jurisdiction_key`, and set `resolution_status` to `resolved`.
5. When a unique jurisdiction cannot be determined, leave the jurisdiction fields blank and set `resolution_status` to `unresolved` rather than guessing.

### Boston Neighborhood Aliases

The following `postal_city` values map to `Boston, MA`:

- Allston
- Boston
- Brighton
- Dorchester
- East Boston
- Hyde Park
- Jamaica Plain
- Mattapan
- Roxbury
- South Boston

### San Diego Neighborhood Alias

- `San Ysidro, CA` maps to `San Diego, CA`.

### Directly Mapped Challenge Cities

- Berkeley, CA
- Los Angeles, CA
- San Diego, CA
- San Francisco, CA
- Cambridge, MA
- Hoboken, NJ
- Jersey City, NJ
- Newark, NJ

## 4. `resolution_status` Definitions

| Status | Definition | Next Step |
| --- | --- | --- |
| `resolved` | The input combination maps uniquely to a legal city within the challenge scope | Continue to rule matching |
| `unresolved` | Required data is missing, the city is unknown, or the jurisdiction cannot be uniquely determined | Stop automatic matching and review manually |

Do not label a record `resolved` merely because one city appears to be the closest match. If an external geocoder is added later, the system may introduce a more detailed status such as `needs_review`, but downstream logic must be updated at the same time.

## 5. Current Results and Validation

The current process resolved 500 addresses:

| Jurisdiction | Address Count |
| --- | ---: |
| Berkeley, CA | 40 |
| Boston, MA | 60 |
| Cambridge, MA | 50 |
| Hoboken, NJ | 40 |
| Jersey City, NJ | 50 |
| Los Angeles, CA | 80 |
| Newark, NJ | 50 |
| San Diego, CA | 50 |
| San Francisco, CA | 80 |
| **Total** | **500** |

Validation checks performed:

- All 500 `address_id` values were preserved in their original order.
- No `address_id` values were duplicated or lost.
- Values in all 11 original columns remained unchanged.
- None of the four added fields are blank.
- All 500 records have a `resolution_status` of `resolved`.
- Jurisdiction counts match the city distribution stated in the challenge materials.

## 6. Important Limitations

The current implementation is a deterministic mapping for the challenge’s fixed dataset. It is not a general-purpose U.S. address geocoder.

- `street_address` is preserved, but the current process does not geocode each street address and test its coordinates against an official municipal boundary. The result primarily relies on `postal_city + state`, known neighborhood aliases, and the challenge’s limited set of cities.
- `resolved` means that the record satisfies the current mapping rules. It does not mean that the address has been independently verified against official government GIS boundaries.
- For arbitrary user-entered addresses, the system must not assume that every record can be resolved automatically.
- ZIP codes are retained as original source data but do not participate in the current jurisdiction decision.
- The challenge rule schema supports only `state` and `city` levels, so the current process does not generate a county jurisdiction. County boundary resolution should be added separately if county ordinances are introduced later.

## 7. Extending the Process to Arbitrary Addresses

If the product expands from the fixed 500-address dataset to free-form user input, use the following process:

1. Standardize the full address while preserving the user’s original input.
2. Use a reliable geocoder to obtain coordinates and a standardized address.
3. Determine the legal city by testing the coordinates against official incorporated-place or municipal boundary data. Do not treat the postal city as the final legal conclusion.
4. Save the source, query timestamp, and match result to support auditing.
5. Mark unmatched, ambiguous, boundary-adjacent, or low-quality results as `unresolved` or `needs_review`.
6. Generate the `jurisdiction_key` and proceed to rule matching only after the jurisdiction passes validation.

## 8. Rule Engine Interface

For an address resolved to `Boston, MA`, the rule engine should evaluate both jurisdiction candidates:

- State-level jurisdiction: `MA`
- City-level jurisdiction: `Boston, MA`

Jurisdiction mapping identifies the candidate legal layers only. Final rule applicability must still account for coverage requirements, effective dates, exemptions, and missing property data.

