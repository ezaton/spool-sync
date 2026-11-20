# Design: Propagate SpoolEase `material_subtype` → Spoolman `extra.variant`

> **Author:** spoolsync bridge team
> **Date:** 2026-11-20 (v6 — incorporates design-review-v1 through v4 + implementation-review-v1 feedback)
> **Status:** ✅ Implemented (v6 — all design and implementation reviews incorporated)
> **Related:** [INVESTIGATION.md](../../INVESTIGATION.md) in spoolease-snapmaker

---

## 1. Problem Statement

The spool-sync bridge maps SpoolEase filament records into Spoolman's normalized
Vendor → Filament → Spool hierarchy. However, the **`material_subtype`** field
(e.g. `"Matte"`, `"Silk"`, `"Basic"`, `"HF"`, `"CF"`) from SpoolEase is **never
propagated** to Spoolman's `extra.variant` custom field on the filament entity.

This means:

1. **Snapmaker U1 spoollink** reads `extra.variant` from Spoolman and gets `""`
   (empty string), so `print_task_config.filament_sub_type` is always empty.
2. **Snapmaker Orca (snorca) slicer** displays `"Generic PLA"` instead of
   `"Overture PLA Matte"` — the subtype information is lost.
3. **All downstream consumers** of Spoolman's filament data (BambuStudio via
   Spoolman plugins, other slicers, analytics) also miss the subtype information.

### Current Data Flow (Broken)

```
SpoolEase SpoolRecord.material_subtype = "Matte"
    ↓  (spool-sync bridge, mapper.py)
Spoolman Filament.name = "Matte Black"           ← subtype only in display name
Spoolman Filament.extra.variant = ""              ← GAP!
    ↓  (spoollink on U1 reads extra.variant)
U1 print_task_config.filament_sub_type = ""       ← EMPTY
    ↓  (snorca reads print_task_config)
snorca UI shows "Generic PLA" instead of "Overture PLA Matte"
```

### Verified on Live System

All 4 active spools on the user's Spoolman instance have **empty `extra.variant`**:

| Spool ID | Filament Name | Vendor | Material | extra.variant |
|----------|--------------|--------|----------|---------------|
| 66 | CR-PETG White | Creality | PETG | `{}` (empty) |
| 42 | Matte Black | Overture | PLA | `{}` (empty) |
| 215 | Basic Dark Blue | Frucindra | PETG | `{}` (empty) |
| 204 | Basic Purple | Frucindra | PETG | `{}` (empty) |

> **Note:** Live filament names may differ from current `map_filament_name()`
> output if they were created by an earlier version of the bridge or set
> manually. The migration script handles this by parsing whatever name is
> present in Spoolman.

The `variant` custom field **exists** on the Spoolman `filament` entity (created
by spoollink on first connection), but it is **never populated** by spool-sync.

### Filament Name Clarification

`map_filament_name()` builds the name from `material_subtype` + `color_name` only:

```python
def map_filament_name(rec: SpoolRecord) -> str | None:
    parts = [p for p in (rec.material_subtype.strip(), rec.color_name.strip()) if p]
    return " ".join(parts).strip() or None
```

So `material_subtype="Matte"` + `color_name="Black"` → `"Matte Black"` (not
`"Matte PLA Black"`). The `material_type` is **not** part of the filament name.

---

## 2. Proposed Changes

### 2.1 Summary

Add `extra.variant` mapping to the bridge's filament upsert path. When a
SpoolEase record has a non-empty `material_subtype`, the bridge will write it
to Spoolman's `extra.variant` field on the filament entity.

### 2.2 Files Changed

| File | Changes |
|------|---------|
| `spoolsync/mapper.py` | Add `extra` field to `FilamentSpec`; add `map_variant()` function; update `map_filament()` to set `extra.variant` |
| `spoolsync/spoolman.py` | Update `upsert_filament()` to include `extra.variant` in PATCH/POST body; add `ensure_variant_field()` method |
| `spoolsync/sync.py` | Add `ensure_variant_field()` call alongside existing `ensure_spoolease_id_field()` |
| `spoolsync/sync_twoway.py` | Add `ensure_variant_field()` call alongside existing `ensure_spoolease_id_field()` |
| `tests/test_mapper.py` | Add tests for `map_variant()` and updated `map_filament()` |
| `migrate_variant.py` | **New file** — one-time migration script for backfilling existing filaments |
| `Dockerfile` | Add `COPY migrate_variant.py .` so the script is available in the container |

---

## 3. Detailed Changes

### 3.1 `mapper.py` — Add variant to FilamentSpec

**Change the import:**
```python
# Before:
from dataclasses import dataclass

# After:
from dataclasses import dataclass, field
```

**Current `FilamentSpec`:**
```python
@dataclass(frozen=True)
class FilamentSpec:
    external_id: str
    name: str | None
    material: str | None
    color_hex: str | None
    weight: float | None
    spool_weight: float | None
    density: float
    diameter: float
    article_number: str | None
    settings_extruder_temp: int | None
    settings_bed_temp: int | None
```

**New `FilamentSpec`:**
```python
@dataclass(frozen=True)
class FilamentSpec:
    external_id: str
    name: str | None
    material: str | None
    color_hex: str | None
    weight: float | None
    spool_weight: float | None
    density: float
    diameter: float
    article_number: str | None
    settings_extruder_temp: int | None
    settings_bed_temp: int | None
    extra: dict[str, str] = field(default_factory=dict)  # NEW
```

The `extra` dict follows Spoolman's convention: keys are custom field names,
values are JSON-encoded strings (e.g. `{"variant": '"Matte"'}`).

**New function — `map_variant()`:**
```python
def map_variant(rec: SpoolRecord) -> str | None:
    """Extract the variant/subtype from a SpoolEase record.

    Returns the material_subtype as-is if non-empty, else None.

    Examples:
        material_subtype="Matte" → "Matte"
        material_subtype="Silk"  → "Silk"
        material_subtype=""      → None
    """
    subtype = rec.material_subtype.strip()
    return subtype if subtype else None
```

**Updated `map_filament()` — append variant to extra dict:**
```python
def map_filament(rec: SpoolRecord, *, density: float, diameter: float,
                 default_extruder_temp: int = 215) -> FilamentSpec:
    variant = map_variant(rec)
    extra: dict[str, str] = {}
    if variant is not None:
        extra["variant"] = _json_str(variant)  # Spoolman JSON-encodes string values

    return FilamentSpec(
        external_id=filament_external_id(rec),
        name=map_filament_name(rec),
        material=map_material(rec),
        color_hex=_norm_hex(rec.color_code),
        weight=float(rec.weight_advertised) if rec.weight_advertised is not None else None,
        spool_weight=_tare(rec),
        density=density,
        diameter=diameter,
        article_number=rec.slicer_filament.strip() or None,
        settings_extruder_temp=map_extruder_temp(rec, default=default_extruder_temp),
        settings_bed_temp=None,
        extra=extra,  # NEW
    )
```

### 3.2 `spoolman.py` — Include variant in filament upsert

**Current `upsert_filament()`:**
```python
def upsert_filament(self, spec: FilamentSpec, vendor_id: int | None) -> int | None:
    existing = self._get("/filament", external_id=spec.external_id).json()
    body = {
        "name": spec.name,
        "vendor_id": vendor_id,
        "material": spec.material,
        "color_hex": spec.color_hex,
        "weight": spec.weight,
        "spool_weight": spec.spool_weight,
        "density": spec.density,
        "diameter": spec.diameter,
        "article_number": spec.article_number,
        "external_id": spec.external_id,
        "settings_extruder_temp": spec.settings_extruder_temp,
        "settings_bed_temp": spec.settings_bed_temp,
    }
    body = {k: v for k, v in body.items() if v is not None}
    if existing:
        fid = existing[0]["id"]
        self._write("PATCH", f"/filament/{fid}", body)
        return fid
    created = self._write("POST", "/filament", body)
    return created["id"] if created else None
```

**New `upsert_filament()`:**
```python
def upsert_filament(self, spec: FilamentSpec, vendor_id: int | None) -> int | None:
    existing = self._get("/filament", external_id=spec.external_id).json()
    body = {
        "name": spec.name,
        "vendor_id": vendor_id,
        "material": spec.material,
        "color_hex": spec.color_hex,
        "weight": spec.weight,
        "spool_weight": spec.spool_weight,
        "density": spec.density,
        "diameter": spec.diameter,
        "article_number": spec.article_number,
        "external_id": spec.external_id,
        "settings_extruder_temp": spec.settings_extruder_temp,
        "settings_bed_temp": spec.settings_bed_temp,
    }
    body = {k: v for k, v in body.items() if v is not None}

    # Include extra fields (variant, etc.) if present
    if spec.extra:
        body["extra"] = spec.extra

    if existing:
        fid = existing[0]["id"]
        self._write("PATCH", f"/filament/{fid}", body)
        return fid
    created = self._write("POST", "/filament", body)
    return created["id"] if created else None
```

### 3.3 `spoolman.py` — New `ensure_variant_field()` method

Add to `SpoolmanClient`:

```python
def ensure_variant_field(self) -> None:
    """Register the `variant` extra field on filaments (idempotent).

    This is the same pattern as ensure_spoolease_id_field() but for the
    filament entity's variant custom field. The field is already created
    by spoollink on first connection, but this ensures idempotent
    registration from the bridge side too.
    """
    body = {"name": "Variant", "field_type": "text", "order": 1}
    try:
        self._write("POST", "/field/filament/variant", body)
    except httpx.HTTPStatusError as e:  # already exists / validation — non-fatal
        log.debug("ensure variant field: %s", e)
```

### 3.4 `sync.py` — Call `ensure_variant_field()` at startup

**Current:**
```python
sm.ensure_spoolease_id_field()
```

**New:**
```python
sm.ensure_spoolease_id_field()
sm.ensure_variant_field()  # NEW
```

### 3.5 `sync_twoway.py` — Call `ensure_variant_field()` at startup

**Current (line ~70):**
```python
sm.ensure_spoolease_id_field()
```

**New:**
```python
sm.ensure_spoolease_id_field()
sm.ensure_variant_field()  # NEW
```

---

## 4. Impact Analysis

### 4.1 Effect on Existing Data

**The reconcile loop will NOT automatically backfill existing filaments.**

The content hash (`models.py:content_hash()`) is computed from SpoolEase record
data, not from the Spoolman output. Since SpoolEase records haven't changed,
the shadow store hash will still match, and the reconciler will skip those
records as `"unchanged"`.

**Therefore, the migration script (`migrate_variant.py`) is REQUIRED**, not optional.

### 4.2 Backfill Strategy

**Primary mechanism: Containerized migration script** (`migrate_variant.py`)

A one-time container run that populates `extra.variant` for all existing
filaments. See Section 7 for execution instructions.

**Secondary mechanism: Reconcile loop for new/changed records**

After deployment, the bridge will automatically include `extra.variant` for:
- New spools scanned from SpoolEase
- SpoolEase records whose data changes (weight update, etc.)

### 4.3 Effect on Data Sync

**SpoolEase → Spoolman (forward sync):**
- ✅ New data: `extra.variant` now flows to Spoolman
- ✅ No breaking changes to existing fields
- ✅ Backward compatible: records with empty subtype just don't set `extra.variant`

**Spoolman → SpoolEase (reverse sync, Phase 2):**
- ⚠️ The `map_sm_to_se()` function already reads `material_subtype` from
  SpoolEase records (via `cached_spoolease.get("material_subtype")`) and
  writes it as `subtype` in the DTO. This is a write-back direction
  (Spoolman → SpoolEase) and is not affected by this change.
- ✅ No changes needed to reverse sync.

### 4.4 Effect on Downstream Consumers

**Snapmaker U1 spoollink:**
- ✅ Will now read non-empty `extra.variant` from Spoolman
- ✅ `print_task_config.filament_sub_type` will be populated
- ✅ snorca will show `"Overture PLA Matte"` instead of `"Generic PLA"`

**BambuStudio / other slicers:**
- ✅ Can read `extra.variant` from Spoolman filament API
- ✅ May improve filament matching in slicer libraries

**Analytics / reporting:**
- ✅ Variant data now available for analysis

### 4.5 Backward Compatibility

| Aspect | Impact |
|--------|--------|
| SpoolEase API | None — no changes to SpoolEase |
| Spoolman API | None — uses existing `extra` field mechanism |
| Existing filaments without variant | No impact — `extra.variant` simply not set |
| Existing filaments with variant set manually | Overwritten on re-sync only if the SpoolEase record has a subtype; otherwise the manual value is preserved (see Open Question #2) |
| Filaments created manually in Spoolman | No impact — bridge only touches SpoolEase-synced filaments |
| spoollink Klipper component | No changes needed — already reads `extra.variant` |

### 4.6 Risk Assessment

| Risk | Likelihood | Severity | Mitigation |
|------|-----------|----------|------------|
| Reconcile loop won't backfill existing data | **Certain** | **High** | Migration script is required, not optional |
| Spoolman rejects unknown extra field | Low | Medium | `ensure_variant_field()` creates it first |
| Empty subtype causes `"variant": ""` in extra | Low | Low | `map_variant()` returns `None` for empty → no field written |
| Breaking existing tests | Low | Medium | Tests updated to check new `extra` field |

---

## 5. Data Transformation for Existing Records

### 5.1 Current State (Before Fix)

All filaments synced from SpoolEase have:
```json
{
  "name": "Matte Black",
  "material": "PLA",
  "extra": {}
}
```

### 5.2 After Migration Script

Same filaments will be updated to:
```json
{
  "name": "Matte Black",
  "material": "PLA",
  "extra": {
    "variant": "\"Matte\""
  }
}
```

Note the **JSON-encoded string** convention: `"\"Matte\""` (double-serialized,
as required by Spoolman's custom field API).

---

## 6. Testing Plan

### 6.1 Unit Tests (mapper.py)

Add to `tests/test_mapper.py`:

```python
def test_map_variant_returns_subtype_when_present():
    rec = SpoolRecord(id="v1", material_subtype="Matte")
    assert map_variant(rec) == "Matte"

def test_map_variant_returns_none_for_empty_subtype():
    rec = SpoolRecord(id="v2", material_subtype="")
    assert map_variant(rec) is None

def test_map_variant_trims_whitespace():
    rec = SpoolRecord(id="v3", material_subtype="  Silk  ")
    assert map_variant(rec) == "Silk"

def test_map_filament_includes_variant_in_extra():
    rec = SpoolRecord(id="f1", brand="Overture", material_type="PLA",
                      material_subtype="Matte", color_name="Black",
                      color_code="#000000", weight_advertised=1000,
                      weight_core=250)
    f = map_filament(rec, density=1.24, diameter=1.75)
    assert f.extra == {"variant": '"Matte"'}

def test_map_filament_no_variant_when_empty():
    rec = SpoolRecord(id="f2", brand="Generic", material_type="PLA",
                      material_subtype="", color_name="White",
                      color_code="#FFFFFF", weight_advertised=1000,
                      weight_core=200)
    f = map_filament(rec, density=1.24, diameter=1.75)
    assert f.extra == {}
```

### 6.2 Integration Tests (spoolman.py)

Add to existing test file:

```python
def test_upsert_filament_includes_variant():
    """Verify variant is sent in the PATCH/POST body."""
    spec = FilamentSpec(
        external_id="se-fil:test",
        name="Matte Black",
        material="PLA",
        color_hex="000000",
        density=1.24,
        diameter=1.75,
        extra={"variant": '"Matte"'},
    )
    # ... mock the HTTP calls and verify body contains extra.variant
```

### 6.3 Manual Verification

After deploying the fix:

1. Run spool-sync bridge
2. Check Spoolman API: `GET /api/v1/filament/{id}` for a filament with subtype
3. Verify `extra.variant` is set:
   ```json
   {"name": "Matte Black", "material": "PLA", "extra": {"variant": "\"Matte\""}}
   ```
4. Load filament on U1 → check `print_task_config.filament_sub_type` is populated
5. Check snorca slicer shows correct filament name with subtype

---

## 7. Rollout Plan — Containerized Execution

The spool-sync bridge runs as a Docker container with no shell access. All
migration steps must be executed via `docker compose` or `docker exec`.

### Step 1: Deploy Bridge Fix (Day 1)

```bash
# From the spool-sync project root
cd /home/ezaton/GIT/spool-sync

# Build and deploy the updated bridge image
docker compose -f docker/docker-compose.yml up -d --build spoolsync
```

This starts the bridge with the new variant mapping. The first reconcile pass
will include `extra.variant` for any new or changed SpoolEase records.

### Step 2: Run Migration Script (Day 1, REQUIRED)

The migration script runs as a **one-shot container** that connects to Spoolman
and patches all filaments with empty `extra.variant`. It is a separate,
short-lived container — the main spoolsync container continues running
normally.

**2a. Dry run (preview):**

```bash
docker compose -f docker/docker-compose.yml run \
  --rm \
  spoolsync python3 migrate_variant.py \
    --base-url http://spoolman:8000 --dry-run
```

**2b. Apply changes:**

```bash
docker compose -f docker/docker-compose.yml run \
  --rm \
  spoolsync python3 migrate_variant.py \
    --base-url http://spoolman:8000
```

**2c. Verify:**

```bash
# Check a specific filament
curl http://block:7912/api/v1/filament/42 | python3 -m json.tool
# Should show: "extra": { "variant": "\"Matte\"" }
```

**2d. Test a single filament first (optional):**

```bash
docker compose -f docker/docker-compose.yml run \
  --rm \
  spoolsync python3 migrate_variant.py \
    --base-url http://spoolman:8000 \
    --filament-id 42
```

### Step 3: Verify End-to-End (Day 2)

```bash
# 1. Scan a spool with SpoolEase
# 2. Wait for reconcile pass (~30s by default)
# 3. Check Spoolman API
curl http://block:7912/api/v1/filament/NEW_ID | python3 -m json.tool
# 4. Load filament on U1
# 5. Verify snorca shows correct filament name with subtype
```

### Container Architecture Note

The migration script is a **standalone Python script** (not a separate container
image). It runs inside the existing `spoolsync` service container using
`docker compose run --rm`, which:
- Spins up a one-shot container from the spoolsync image
- Runs the script with the `--base-url` CLI argument (no env vars needed)
- Automatically removes the container after completion (`--rm`)
- Does **not** interfere with the main spoolsync container

A single `COPY migrate_variant.py .` line was added to the `Dockerfile` so the
script is available inside the container at runtime. No changes to
`docker-compose.yml` are needed.

---

## 8. Open Questions

1. **Should we also update the filament `name` to include material type?**
   Currently `map_filament_name()` produces `"Matte Black"` (subtype + color).
   Some downstream consumers might expect `"Overture PLA Matte"` (brand-type-subtype).
   This is a separate concern and out of scope for this fix.

2. **Should we handle the case where a filament has no subtype but the user
   manually set `extra.variant` in Spoolman?**
   The bridge will **not** overwrite it. When SpoolEase has an empty
   `material_subtype`, the `extra` dict is empty, and `upsert_filament()`
   skips it (`if spec.extra:` is falsy). Spoolman preserves the existing
   value. This is the desired behavior — SpoolEase-synced filaments where
   SpoolEase has no subtype keep any manually-set variant.
   If SpoolEase **does** have a subtype, the bridge writes it as the
   authoritative value on the next re-sync (when the record's content hash
   changes).

3. **Should we add a config option to disable variant sync?**
   Probably not needed — the variant field is optional and harmless.

4. **What about the reverse direction (Spoolman → SpoolEase)?**
   The `map_sm_to_se()` function already handles this. If someone edits the
   variant in Spoolman, it won't flow back to SpoolEase (which is correct —
   SpoolEase is the source of truth for filament data).

---

## 9. Appendix: Full Data Flow After Fix

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SPOOLEASE (ESP32-S3)                         │
│  SpoolRecord.material_subtype = "Matte"                             │
└────────────────────────────┬────────────────────────────────────────┘
                             │ GET /api/spools.json
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    SPOOL-SYNC BRIDGE (Python)                        │
│                                                                      │
│  mapper.py:                                                          │
│    map_variant(rec) → "Matte"                                        │
│    map_filament(rec) → FilamentSpec { ..., extra: {"variant": "\"Matte\"" } } │
│                                                                      │
│  spoolman.py:                                                        │
│    upsert_filament(spec) → PATCH /api/v1/filament/{id}              │
│      body = { ..., "extra": {"variant": "\"Matte\"" } }             │
│    ensure_variant_field() → POST /api/v1/field/filament/variant     │
│                                                                      │
│  sync.py / sync_twoway.py:                                          │
│    sm.ensure_variant_field() at startup                              │
│                                                                      │
│  migrate_variant.py (one-shot, run once):                            │
│    docker compose run --rm spoolsync python3 migrate_variant.py     │
│    → PATCH /api/v1/filament/{id} extra.variant for all existing     │
└────────────────────────────┬────────────────────────────────────────┘
                             │ REST API
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        SPOOLMAN (block:7912)                         │
│  GET /api/v1/filament/{id}                                          │
│  {                                                                  │
│    "name": "Matte Black",                                           │
│    "material": "PLA",                                               │
│    "extra": { "variant": "\"Matte\"" }   ← NOW POPULATED!          │
│  }                                                                  │
└────────────────────────────┬────────────────────────────────────────┘
                             │ spoollink_resolve_spool()
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│              SNAPMAKER U1 (Moonraker + Klipper)                     │
│                                                                      │
│  Moonraker spoollink.py:                                           │
│    _parse_variant(vendor, filament) → "Matte"                       │
│                                                                      │
│  Klipper spoollink.py → print_task_config:                         │
│    filament_sub_type[1] = "Matte"  ← NOW POPULATED!                │
└────────────────────────────┬────────────────────────────────────────┘
                             │ Moonraker API
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  SNORCA (Snapmaker Orca Slicer)                      │
│  Reads: filament_vendor="Generic", filament_type="PLA",            │
│         filament_sub_type="Matte"                                   │
│  Displays: "Generic PLA Matte"  ← CORRECT!                         │
└─────────────────────────────────────────────────────────────────────┘
```
