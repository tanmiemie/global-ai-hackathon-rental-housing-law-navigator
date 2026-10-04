# Workflow v3 published results

This package contains the accepted lookup for **500 addresses and 466 rules**,
with query date **2026-10-01** and evaluation mode `coverage`. It returns
**14,331 applies** and **40,865 unknown** entries across **477 evaluated
addresses**. The other **23 addresses** were skipped because their jurisdiction
was not verified; their original reasons are in
[address_diagnostics.json](address_diagnostics.json).

| File | Purpose |
| --- | --- |
| [lookups.json.gz](lookups.json.gz) | Lossless compressed copy of the complete final lookup |
| [rules.json](rules.json) | Paired 466-rule export with the short IDs used in the lookup; approximately 14 MiB |
| [rule_id_map.json](rule_id_map.json) | Mapping between submission IDs and internal rule IDs |
| [address_summary.csv](address_summary.csv) | Counts and evaluation status for every address |
| [address_statistics.html](address_statistics.html) | Searchable statistics; download and open locally |
| [run_identity.json](run_identity.json) | Frozen input and evaluator identity |
| [run_report.json](run_report.json) | Original run statistics, hashes, and timings |
| [repair_validation.json](repair_validation.json) | Recorded validation of the residential-use repair |
| [residential_repair_comparison.csv](residential_repair_comparison.csv) | Per-address counts before and after that repair |

The [first-ten preview](../evaluator_first10_workflow_v3/lookups.json) provides a
smaller readable example. For input dependencies, evaluation semantics, and
fresh-run commands, use the [reproduction guide](../../LOOKUP_REPRODUCTION.md).

## Restore the complete JSON

The uncompressed `lookups.json` is **167,558,063 bytes**, so the Git repository
ships it compressed. Its SHA-256 is
`e8c7e594d8028b1f2810dcc132abb2e8c1ce270650b54d9f4b3c5518719a3862`.
Run this standard-library command from the repository root. It verifies the
restored bytes, keeps an existing matching file, and refuses to overwrite a
different file.

```bash
python3 - <<'PY'
import gzip
import hashlib
from pathlib import Path

directory = Path('outputs/evaluator_all500_workflow_v3')
destination = directory / 'lookups.json'
expected_size = 167558063
expected_sha = 'e8c7e594d8028b1f2810dcc132abb2e8c1ce270650b54d9f4b3c5518719a3862'
with gzip.open(directory / 'lookups.json.gz', 'rb') as source:
    payload = source.read()
assert len(payload) == expected_size, 'Unexpected uncompressed size'
assert hashlib.sha256(payload).hexdigest() == expected_sha, 'Lookup hash mismatch'
if destination.exists():
    existing = destination.read_bytes()
    assert len(existing) == expected_size, 'Existing destination differs; preserved'
    assert hashlib.sha256(existing).hexdigest() == expected_sha, 'Existing destination differs; preserved'
    print('Existing lookups.json already matches; no changes made.')
else:
    with destination.open('xb') as output:
        output.write(payload)
    print('Restored and verified lookups.json.')
PY
```

Use this lookup with the `rules.json` in this directory. The internal rule
snapshot under `outputs/module_a/` remains an evaluator input with different IDs.

## Publication scope

The saved reports describe the original local run. Their references to batch
audits, the v2 baseline, and earlier review experiments point to unpublished
local artifacts. These reports are historical records, not a complete reusable
cache or enough files to rerun the historical audit comparison. A fresh run
creates its own audit files.

Old and failed output runs, caches, and detailed batch audits are excluded from
this publication. The exact [D037](../../data/supplemental/D037.txt),
[D059](../../data/supplemental/D059.txt), and
[D074](../../data/supplemental/D074.txt) snapshots needed for the current plans
are included under `data/supplemental/`. Other research captures remain local;
see the [capture notes](../../data/supplemental/capture_notes.txt). Fresh lookup
execution uses these original source bytes for evidence validation; missing or
mismatched source checks must not be bypassed.
