# Design Review: DESIGN_VARIANT_SYNC.md (v1)

> **Reviewer:** Claude Opus 4.6
> **Date:** 2026-11-20
> **Status:** Review of Draft design

---

## Critical: Reconcile Loop Will NOT Backfill Existing Filaments

**Location:** Section 3.1 (Effect on Existing Data), Section 3.5 (Risk Assessment)

### Problem

The design states:

> Existing records: Will be updated on the **next reconcile pass** because the
> content hash includes `material_subtype` (see `SpoolRecord.content_hash()`).

This is incorrect. The content hash (`models.py:61-70`) is computed from
**SpoolEase record data**, not from the Spoolman output. Since the SpoolEase
records themselves haven't changed, the hash stored in the shadow store
(`spoolsync-state.sqlite`) will still match, and the reconciler
(`reconcile.py:42`) will skip those records as `"unchanged"`:

```python
# reconcile.py:42
if shadow is not None and shadow.content_hash == h and not shadow.archived:
    stats["unchanged"] += 1
    continue
```

Adding `extra.variant` to the mapper output changes what gets **written** to
Spoolman, but does **not** change what gets **hashed** (the SpoolEase input).
Only records whose SpoolEase data actually changes (e.g., a weight update from
printing) will be re-synced and pick up the new variant field.

This also makes the Risk Assessment row "Content hash change triggers full
re-sync" (Section 3.5, Medium likelihood) moot -- there will be no hash change
and no re-sync.

### Suggested Fix

Choose one of the following strategies and document it in the design:

**Option A -- One-time shadow store wipe (simplest):**
Delete or rename `spoolsync-state.sqlite` before restarting the bridge. The
next reconcile pass will treat every record as new (no shadow entry) and
upsert all filaments with the new `extra.variant` field. Downside: all records
are re-written to Spoolman, not just the ones needing variant.

**Option B -- Schema version bump in the content hash:**
Add a constant (e.g., `"_schema_version": 2`) to the hash payload in
`SpoolRecord.content_hash()`. This invalidates all existing shadow hashes and
forces a full re-sync exactly once. Revert the version bump after first
deployment, or keep it as a migration mechanism for future mapper changes.

```python
# models.py -- content_hash()
payload = {k: v for k, v in asdict(self).items() if k != "_extra"}
payload["_schema_version"] = 2  # bump to force re-sync after mapper changes
```

**Option C -- Promote the migration script to required (not optional):**
Keep the reconcile loop as-is for new/changed records, and run
`migrate_variant.py` to backfill existing filaments. Update Section 4.3 and
Section 6 (Rollout Plan) to make the migration script a required step, not
optional.

**Recommendation:** Option B is the cleanest. It forces exactly one full
re-sync, is self-documenting, and generalizes to any future mapper-output
change that doesn't alter the SpoolEase input data.

---

## Missing File: `sync_twoway.py`

**Location:** Section 2.2 (Files Changed), Section 2.3.3

### Problem

The design lists only `sync.py` for the `ensure_variant_field()` call.
However, `sync_twoway.py:70` also calls `sm.ensure_spoolease_id_field()` at
startup:

```python
# sync_twoway.py:70
sm.ensure_spoolease_id_field()
```

Without adding `sm.ensure_variant_field()` here as well, the variant field
will not be registered when running in two-way sync mode.

### Suggested Fix

Add `sync_twoway.py` to the Files Changed table in Section 2.2:

| File | Changes |
|------|---------|
| `spoolsync/sync_twoway.py` | Add `ensure_variant_field()` call alongside existing `ensure_spoolease_id_field()` |

And add a subsection under 2.3.3 showing the change at `sync_twoway.py:70-71`:

```python
sm.ensure_spoolease_id_field()
sm.ensure_variant_field()  # NEW
```

---

## Incorrect Filament Name Examples

**Location:** Sections 1, 4.1, 4.2, and the Section 8 data flow diagram

### Problem

The document uses `"Matte PLA Black"` as the example filament name throughout.
The actual `map_filament_name()` (`mapper.py:96-99`) builds the name from
`material_subtype` + `color_name` only -- `material_type` is **not** included:

```python
def map_filament_name(rec: SpoolRecord) -> str | None:
    parts = [p for p in (rec.material_subtype.strip(), rec.color_name.strip()) if p]
    name = " ".join(parts).strip()
    return name or None
```

For `material_subtype="Matte"`, `color_name="Black"`, the output is
`"Matte Black"`, not `"Matte PLA Black"`. This is confirmed by the existing
test (`test_mapper.py:80-81`): `subtype="Basic"` + `color="Black"` ->
`"Basic Black"`.

The live system data in the Section 1 table may reflect names set by an older
version of the mapper or set manually, but the design should match the current
code behavior.

### Suggested Fix

Replace all occurrences of `"Matte PLA Black"` with `"Matte Black"` to match
the current mapper output. Similarly update `"Basic Dark Blue"` and
`"Basic Purple"` references if they also include material type (check against
the mapper).

Affected locations:
- Section 1: "Current Data Flow" diagram and live-system table
- Section 4.1: "Current State (Before Fix)" JSON example
- Section 4.2: "After Fix" JSON example
- Section 8: Full data flow diagram (`FilamentSpec` and Spoolman boxes)

---

## Minor Issues

### 1. Shell Artifacts at End of File

**Location:** Lines 627-628

The file ends with:

```
EOF
echo "DESIGN_VARIANT_SYNC.md created"
```

These are leftover from the heredoc/shell script that created the file. Remove
them.

### 2. Missing `field` Import

**Location:** Section 2.3.1

The proposed `FilamentSpec` change uses `field(default_factory=dict)`:

```python
extra: dict[str, str] = field(default_factory=dict)
```

But `mapper.py:12` currently imports only `dataclass`:

```python
from dataclasses import dataclass
```

The design should note that the import needs to change to:

```python
from dataclasses import dataclass, field
```

### 3. Section 2.3.3 Layout Is Confusing

**Location:** Section 2.3.3

The `ensure_variant_field()` method is defined on `SpoolmanClient` (lives in
`spoolman.py`) but is described entirely under the "sync.py" heading. This
could mislead an implementer into putting the method in the wrong file.

**Suggested fix:** Split into two subsections:
- **2.3.3a `spoolman.py`** -- New `ensure_variant_field()` method on
  `SpoolmanClient`
- **2.3.3b `sync.py` / `sync_twoway.py`** -- Call sites that invoke the new
  method at startup

### 4. Migration Script Framing

**Location:** Section 4.3

The migration script introduction says:

> If you want to populate `extra.variant` for existing filaments **without
> waiting** for the next reconcile pass (e.g. for filaments created manually
> in Spoolman)

Given the critical issue above (the reconcile pass will NOT backfill), this
framing is misleading. The migration script is the **primary** mechanism for
backfilling existing filaments, not a convenience shortcut.

**Suggested fix:** Reframe as:

> The reconcile loop only updates filaments whose SpoolEase source data has
> changed. To populate `extra.variant` on existing filaments that are already
> in sync, run this migration script.

---

## What the Design Gets Right

- The `extra` dict approach on `FilamentSpec` mirrors the existing
  `SpoolSpec.extra` pattern and follows Spoolman's JSON-encoded-string
  convention correctly.
- `map_variant()` correctly returns `None` for empty subtypes, preventing
  empty-string writes to Spoolman.
- The `ensure_variant_field()` pattern matches the existing
  `ensure_spoolease_id_field()` idiom.
- The impact analysis for downstream consumers (spoollink, snorca) is sound.
- Open questions are well-scoped and the answers are sensible.
- The end-to-end data flow diagram is clear and useful (aside from the name
  inaccuracy).
- Backward compatibility analysis is thorough.
