# Implementation Review: Variant Sync (v1)

> **Reviewer:** Claude Opus 4.6
> **Date:** 2026-11-20
> **Design:** DESIGN_VARIANT_SYNC.md v4
> **Verdict:** Implementation is functionally correct. One style issue to fix,
> one missing test noted, one pre-existing test failure unrelated to this change.

---

## Files Changed

| File | Expected (per design) | Actual | Match? |
|------|----------------------|--------|--------|
| `spoolsync/mapper.py` | `FilamentSpec.extra`, `map_variant()`, `map_filament()` update | All present | Yes |
| `spoolsync/spoolman.py` | `ensure_variant_field()`, `upsert_filament()` extra handling | All present | Yes |
| `spoolsync/sync.py` | `ensure_variant_field()` startup call | Present at line 42 | Yes |
| `spoolsync/sync_twoway.py` | `ensure_variant_field()` startup call | Present at line 71 | Yes |
| `tests/test_mapper.py` | 5 new variant tests | All present (lines 278-310) | Yes |
| `migrate_variant.py` | One-time migration script | Present (untracked, not part of this diff) | Yes |
| `models.py` | No changes | Unchanged | Yes |
| `reconcile.py` | No changes | Unchanged | Yes |
| `reconcile_sm_to_se.py` | No changes | Unchanged | Yes |

---

## Test Results

```
73 passed, 1 failed (pre-existing)
```

**All 5 new variant tests pass:**
- `test_map_variant_returns_subtype_when_present` -- PASS
- `test_map_variant_returns_none_for_empty_subtype` -- PASS
- `test_map_variant_trims_whitespace` -- PASS
- `test_map_filament_includes_variant_in_extra` -- PASS
- `test_map_filament_no_variant_when_empty` -- PASS

**Pre-existing failure (NOT introduced by this change):**
- `test_vendor_and_filament_identity_are_stable_and_dedupe` -- FAIL
- Expects `"se-fil:GFA01"` but gets `"se-fil:GFA01:1A1A1AFF"`
- Confirmed pre-existing: fails identically with all variant changes stashed
- The test assertion is stale -- `filament_external_id()` was updated (in an
  earlier commit) to include color in the key, but the test was not updated
- **Not related to variant sync; should be fixed separately**

---

## Correctness Verification

### mapper.py

**`FilamentSpec.extra` field (line 36):**
```python
extra: dict[str, str] = field(default_factory=dict)  # NEW
```
Correct. Uses `field(default_factory=dict)` for the mutable default on a
frozen dataclass. Import updated to `from dataclasses import dataclass, field`
at line 11.

**`map_variant()` (lines 95-106):**
```python
def map_variant(rec: SpoolRecord) -> str | None:
    subtype = rec.material_subtype.strip()
    return subtype if subtype else None
```
Correct. Strips whitespace, returns `None` for empty/whitespace-only subtypes.
Matches the design exactly.

**`map_filament()` (lines 290-310):**
```python
variant = map_variant(rec)
extra: dict[str, str] = {}
if variant is not None:
    extra["variant"] = _json_str(variant)
...
extra=extra,
```
Correct. Calls `map_variant()`, builds the extra dict only when variant is
non-None, passes it to `FilamentSpec`. Uses existing `_json_str()` helper
for Spoolman's JSON-encoded-string convention.

### spoolman.py

**`ensure_variant_field()` (lines 71-83):**
```python
body = {"name": "Variant", "field_type": "text", "order": 1}
self._write("POST", "/field/filament/variant", body)
```
Correct. Follows the same pattern as `ensure_spoolease_id_field()`.
Path `/field/filament/variant` is correct for a filament-entity extra field
(compared to `/field/spool/spoolease_id` for spool-entity).

**`upsert_filament()` extra handling (lines 119-121):**
```python
if spec.extra:
    body["extra"] = spec.extra
```
Correct. Placed after the `None`-value filter (`body = {k: v ... if v is not
None}`), so `extra` is not subject to that filter. Empty dict is falsy, so
filaments with no variant don't send an `extra` key at all -- Spoolman
preserves existing `extra` values on PATCH when the key is absent.

### sync.py (line 42)

```python
sm.ensure_spoolease_id_field()
sm.ensure_variant_field()  # variant custom field for filaments
```
Correct. Called at startup before the reconcile loop.

### sync_twoway.py (line 71)

```python
sm.ensure_spoolease_id_field()
sm.ensure_variant_field()  # variant custom field for filaments
```
Correct. Called at startup in two-way mode.

### tests/test_mapper.py

All 5 tests match the design spec. `map_variant` is imported (line 22).
Tests cover: present subtype, empty subtype, whitespace trimming, extra dict
with variant, and empty extra dict.

---

## Issue: Missing Blank Lines Between Functions (PEP 8)

**Location:** `mapper.py:94-108`

The `map_variant()` function was inserted between `map_material()` and
`map_filament_name()` without the required PEP 8 blank-line spacing:

```python
    return rec.material_type.strip() or None     # end of map_material()
def map_variant(rec: SpoolRecord) -> str | None:  # <-- 0 blank lines (need 2)
    ...
    return subtype if subtype else None
                                                   # <-- 1 blank line (need 2)
def map_filament_name(rec: SpoolRecord) -> str | None:
```

PEP 8 requires two blank lines between top-level function definitions. The
implementation has 0 blank lines before `map_variant()` and 1 blank line
after it.

**Fix:**

Add two blank lines before `map_variant()` and two blank lines after it:

```python
    return rec.material_type.strip() or None


def map_variant(rec: SpoolRecord) -> str | None:
    ...
    return subtype if subtype else None


def map_filament_name(rec: SpoolRecord) -> str | None:
```

---

## Observation: Integration Test Not Implemented

The design (Section 6.2) proposed a `test_upsert_filament_includes_variant`
integration test to verify the HTTP body construction in `upsert_filament()`.
This test was not implemented -- there is no test file for `spoolman.py`.

The core mapping logic is well-covered by the mapper unit tests. The
untested path is the 3-line `if spec.extra: body["extra"] = spec.extra`
block in `upsert_filament()`. This is straightforward code, but if test
coverage is desired, it would require mocking `httpx.Client`.

Not blocking -- the mapper tests provide confidence in the data transformation,
and the code path in `upsert_filament()` is minimal.

---

## Summary

| Aspect | Status |
|--------|--------|
| `mapper.py` -- `FilamentSpec.extra` field | Correct |
| `mapper.py` -- `map_variant()` function | Correct |
| `mapper.py` -- `map_filament()` extra handling | Correct |
| `mapper.py` -- import updated (`field`) | Correct |
| `spoolman.py` -- `ensure_variant_field()` | Correct |
| `spoolman.py` -- `upsert_filament()` extra | Correct |
| `sync.py` -- startup call | Correct |
| `sync_twoway.py` -- startup call | Correct |
| `tests/test_mapper.py` -- 5 new tests | All pass |
| PEP 8 blank lines in `mapper.py` | **Needs fix** |
| Pre-existing test failure | Unrelated to this change |
| Integration test (`spoolman.py`) | Not implemented (non-blocking) |
| No unintended changes to other files | Verified |

**The implementation is functionally correct and matches the design.** The
only required fix is adding blank lines around `map_variant()` in `mapper.py`
to comply with PEP 8.
