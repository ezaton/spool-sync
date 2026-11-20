# Implementation Review: Variant Sync (v2)

> **Reviewer:** Claude Opus 4.6
> **Date:** 2026-11-20
> **Design:** DESIGN_VARIANT_SYNC.md v4
> **Prior review:** implementation-review-v1.md
> **Verdict:** All v1 issues resolved. Implementation is correct and ready.

---

## v1 Issue Resolution

| v1 Issue | Status |
|----------|--------|
| PEP 8 missing blank lines around `map_variant()` | **Fixed** -- now has 2 blank lines before and after |
| Integration test for `upsert_filament()` | Acknowledged as non-blocking; no change |
| Pre-existing test failure (`filament_external_id`) | Unchanged; unrelated to variant sync |

### PEP 8 Fix Verified

`mapper.py` lines 94-110 now have correct spacing:

```python
    return rec.material_type.strip() or None     # end of map_material()
                                                  # blank line 1
                                                  # blank line 2
def map_variant(rec: SpoolRecord) -> str | None:
    ...
    return subtype if subtype else None
                                                  # blank line 1
                                                  # blank line 2
def map_filament_name(rec: SpoolRecord) -> str | None:
```

---

## Files Changed

| File | Expected (per design) | Actual | Match? |
|------|----------------------|--------|--------|
| `spoolsync/mapper.py` | `FilamentSpec.extra`, `map_variant()`, `map_filament()` update | All present | Yes |
| `spoolsync/spoolman.py` | `ensure_variant_field()`, `upsert_filament()` extra handling | All present | Yes |
| `spoolsync/sync.py` | `ensure_variant_field()` startup call | Present at line 42 | Yes |
| `spoolsync/sync_twoway.py` | `ensure_variant_field()` startup call | Present at line 71 | Yes |
| `tests/test_mapper.py` | 5 new variant tests | All present (lines 278-310) | Yes |
| `migrate_variant.py` | One-time migration script | Present (untracked) | Yes |
| `models.py` | No changes | Unchanged | Yes |
| `reconcile.py` | No changes | Unchanged | Yes |
| `reconcile_sm_to_se.py` | No changes | Unchanged | Yes |

---

## Test Results

```
73 passed, 1 failed (pre-existing)
```

**All 5 variant tests pass:**
- `test_map_variant_returns_subtype_when_present` -- PASS
- `test_map_variant_returns_none_for_empty_subtype` -- PASS
- `test_map_variant_trims_whitespace` -- PASS
- `test_map_filament_includes_variant_in_extra` -- PASS
- `test_map_filament_no_variant_when_empty` -- PASS

**Pre-existing failure (NOT introduced by this change):**
- `test_vendor_and_filament_identity_are_stable_and_dedupe` -- FAIL
- Expects `"se-fil:GFA01"` but gets `"se-fil:GFA01:1A1A1AFF"`
- Stale test assertion from an earlier commit; not related to variant sync

---

## Correctness Re-verification

### JSON Encoding Chain (end-to-end)

Verified the full encoding path for a record with `material_subtype="Matte"`:

| Step | Function | Input | Output |
|------|----------|-------|--------|
| 1 | `map_variant(rec)` | `rec.material_subtype = "Matte"` | `"Matte"` (str) |
| 2 | `_json_str("Matte")` | `"Matte"` | `'"Matte"'` (JSON-encoded string) |
| 3 | `FilamentSpec.extra` | -- | `{"variant": '"Matte"'}` |
| 4 | `upsert_filament()` body | `spec.extra` truthy | `body["extra"] = {"variant": '"Matte"'}` |
| 5 | Spoolman storage | -- | `extra.variant = '"Matte"'` |
| 6 | `json.loads('"Matte"')` | consumer reads | `"Matte"` (str) |

This matches Spoolman's double-serialization convention (values are JSON-encoded
strings), consistent with how `spoolease_id` is already stored on spools.

### Edge Cases Verified

| Input | `map_variant()` | `extra` dict | `if spec.extra:` | Body includes `extra`? |
|-------|-----------------|-------------|-------------------|----------------------|
| `"Matte"` | `"Matte"` | `{"variant": '"Matte"'}` | True | Yes |
| `""` | `None` | `{}` | False | No |
| `"   "` | `None` | `{}` | False | No |
| `"  Silk  "` | `"Silk"` | `{"variant": '"Silk"'}` | True | Yes |

When `extra` is empty (`{}`), it's falsy in Python, so `upsert_filament()`
does not include the `extra` key in the HTTP body. Spoolman preserves any
existing `extra` values on the filament when the key is absent from PATCH.

### mapper.py

**`FilamentSpec.extra` field (line 36):**
- `extra: dict[str, str] = field(default_factory=dict)` -- Correct.
- Uses `field(default_factory=dict)` for mutable default on frozen dataclass.
- Import: `from dataclasses import dataclass, field` at line 11.

**`map_variant()` (lines 97-108):**
- Strips whitespace, returns `None` for empty/whitespace-only subtypes.
- Matches design exactly.

**`map_filament()` (lines 293-313):**
- Calls `map_variant()`, builds extra dict only when variant is non-None.
- Uses `_json_str()` for Spoolman's JSON-encoded-string convention.
- Passes extra dict to `FilamentSpec` constructor.

### spoolman.py

**`ensure_variant_field()` (lines 71-83):**
- `body = {"name": "Variant", "field_type": "text", "order": 1}`
- POST to `/field/filament/variant` -- correct path for filament entity.
- Follows same pattern as `ensure_spoolease_id_field()`.
- Catches `HTTPStatusError` (idempotent -- ignores "already exists").

**`upsert_filament()` extra handling (lines 119-121):**
- `if spec.extra: body["extra"] = spec.extra`
- Placed after `{k: v for ... if v is not None}` filter -- not subject to it.
- Empty dict is falsy -- filaments with no variant don't send `extra`.

### sync.py (line 42)

- `sm.ensure_variant_field()` called after `sm.ensure_spoolease_id_field()`.
- Runs once at startup before the reconcile loop.

### sync_twoway.py (line 71)

- `sm.ensure_variant_field()` called after `sm.ensure_spoolease_id_field()`.
- Runs once at startup before the two-way reconcile loop.

### tests/test_mapper.py

- `map_variant` imported at line 22.
- 5 tests cover: present subtype, empty subtype, whitespace trimming,
  extra dict with variant, and empty extra dict.
- All assertions match expected behavior.

---

## Summary

| Aspect | Status |
|--------|--------|
| `mapper.py` -- `FilamentSpec.extra` field | Correct |
| `mapper.py` -- `map_variant()` function | Correct |
| `mapper.py` -- `map_filament()` extra handling | Correct |
| `mapper.py` -- PEP 8 blank lines | **Fixed** (was broken in v1) |
| `mapper.py` -- import updated (`field`) | Correct |
| `spoolman.py` -- `ensure_variant_field()` | Correct |
| `spoolman.py` -- `upsert_filament()` extra | Correct |
| `sync.py` -- startup call | Correct |
| `sync_twoway.py` -- startup call | Correct |
| `tests/test_mapper.py` -- 5 new tests | All pass |
| JSON encoding chain | Verified end-to-end |
| Edge cases (empty, whitespace) | Verified |
| Pre-existing test failure | Unrelated to this change |
| No unintended changes to other files | Verified |

**The implementation is correct, matches the design, and all v1 issues are resolved.**
No further changes required for the variant sync feature.
