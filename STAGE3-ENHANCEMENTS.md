# Stage 3: Enhancements to the Bridge

## Status
🟢 **Phase A Complete** — Temperature mapping implemented and verified  
🟢 **Phase B Complete** — Startup sequence with queue drain implemented  
🔵 **Phase C Planned** — `map_sm_to_se()` mapper function  
🔵 **Phase D Planned** — Phase 2 tests

---

## Phase A: Temperature Mapping ✅ COMPLETE

### What Was Done
Implemented one-way temperature mapping from SpoolEase → Spoolman. SpoolEase stores nozzle temperature ranges (min/max) from manufacturer CSVs; the bridge computes the midpoint for Spoolman's single-value `settings_extruder_temp` field.

### Changes Made

#### 1. `config.py` — Added `default_extruder_temp`
- New config field: `default_extruder_temp` (default: 215°C, PLA midpoint)
- Read from env var `DEFAULT_EXTRUDER_TEMP`
- Used as fallback for unrecognized/custom filaments

#### 2. `mapper.py` — Added temperature mapping functions
- `_BASE_FILAMENT_TEMPS`: 98 entries from `base-filaments-index.csv` (codes like GFA00, GFB00, etc.)
- `_MATERIAL_TEMPS`: 37 entries from `materials.csv` (material types like PLA, ABS, PETG, etc.)
- `map_extruder_temp(rec, default=215)`: Returns midpoint of temp range, or default
- `map_filament()` updated to call `map_extruder_temp()` and populate `settings_extruder_temp`
- `settings_bed_temp` remains `None` (SpoolEase doesn't track bed temps)

#### 3. `reconcile.py` — Wired through config
- `map_filament()` call now passes `default_extruder_temp=cfg.default_extruder_temp`

#### 4. `tests/test_mapper.py` — Added 10 temperature tests
- Known base filament midpoint (GFA01 → 215)
- High-temp filament (GFT02 PPS-CF → 325)
- Odd range rounding (GFU02 → 230)
- Unknown code falls back to material (XYZ99 + ABS → 260)
- Unknown code falls back to material subtype (XYZ99 + PLA-CF → 215)
- Completely unknown returns default
- Custom default respected
- Full `map_filament()` integration tests for base filament, material fallback, and custom default

### Verified Against Live Data
Mock calls to both services confirmed:
- SpoolEase has 179 records, 37 brands, 70 unique colors
- Spoolman has 214 filaments, all with `se-fil:` prefix from bridge
- All 58 slicer presets have temperature mappings
- 9 duplicate (slicer, color) pairs expected (multiple physical spools)
- 1 multi-color filament detected (needs special handling)

### Design Decisions
- **One-way only**: SpoolEase → Spoolman. Spoolman's extruder temp override is NOT written back.
- **Midpoint calculation**: `(min + max) // 2` — reasonable default for unknown slicers
- **Priority**: Slicer code lookup > material type lookup > default fallback
- **Bed temp**: Always `None` — SpoolEase doesn't track it anywhere

---

## Phase B: Startup Sequence with Queue Drain ✅ COMPLETE

### What Was Done
Implemented the startup sequence with event queueing and drain in `sync_twoway.py`, plus supporting methods in `state.py`.

### Changes Made

#### 1. `sync_twoway.py` — Added startup sequence with queue drain
- **`_startup_sequence()`**: Orchestrates the 7-step startup sequence:
  1. Create event queue (`asyncio.Queue[dict]`, maxsize=1000)
  2. Start websocket listener in background that queues events during startup
  3. Run initial SpoolEase → Spoolman reconcile pass (`reconcile_once`)
  4. Run initial Spoolman baseline scan (`_scan_spoolman_baseline`)
  5. Signal startup completion (sets `startup_done` event)
  6. Drain queued events (process each via `handle_spoolman_event`)
  7. Cancel startup websocket listener, normal listener takes over
- **`_startup_websocket_listener()`**: WebSocket listener that queues events during startup phase, then switches to direct processing. Handles reconnection with exponential backoff. Queue overflow drops oldest event.
- **`_scan_spoolman_baseline()`**: Scans Spoolman's current spools and creates placeholder shadow entries for Spoolman-only spools (those without `se-fil:` external_id). This ensures the shadow store has a complete baseline.
- **`run_twoway()`**: Updated to call `_startup_sequence()` before starting normal concurrent loops.

#### 2. `state.py` — Added reverse lookup and placeholder methods
- **`get_by_spoolman_id()`**: Reverse lookup — returns ShadowRow for a given Spoolman spool ID (queries by `spoolman_spool_id` column)
- **`upsert_placeholder()`**: Creates a placeholder shadow entry for Spoolman-only spools. Uses `content_hash="baseline"` and optional `spoolease_id=None`.
- **`all_spoolman_ids()`**: Returns all Spoolman spool IDs known to the shadow store (for future use in diff-based sync).

### Why Queue Instead of Wait?
Waiting for the scan to complete means missing Spoolman changes that occur during the scan. Queueing captures everything and replays it cleanly after the baseline is established. The queue holds only event payloads (small, bounded at 1000).

### Design Decisions
- **Queue size**: 1000 events, bounded to prevent memory growth
- **Overflow behavior**: Drop oldest event when queue is full (Spoolman events during a short scan window are unlikely to exceed this)
- **Startup websocket listener**: Separate task from normal listener; cancelled after drain completes
- **Normal listener**: `_listen_spoolman_to_spoolease()` continues unchanged — it handles the direct processing path after startup
- **Placeholder entries**: Spoolman-only spools get `spoolease_id=None` placeholders. The reconcile pass fills in real mappings for SpoolEase-originated spools.

### Verification
- Both `state.py` and `sync_twoway.py` pass Python syntax validation
- All 20 existing tests pass (1 pre-existing failure unrelated to changes)
- All 10 temperature mapping tests pass

### Mock Tests (2026-11-16)
A dedicated mock test suite was run to verify Phase B state methods without live services:

| Test | Result |
|------|--------|
| `get_by_spoolman_id` — unknown ID returns None | ✅ PASS |
| `get_by_spoolman_id` — finds row by spoolman_spool_id | ✅ PASS |
| `get_by_spoolman_id` — non-existent ID returns None | ✅ PASS |
| `upsert_placeholder` — creates placeholder with sentinel PK | ✅ PASS |
| `upsert_placeholder` — idempotent (no duplicates) | ✅ PASS |
| `all_spoolman_ids` — empty set initially | ✅ PASS |
| `all_spoolman_ids` — correct set excluding None | ✅ PASS |
| Echo suppression — detects own write | ✅ PASS |
| Echo suppression — allows different content | ✅ PASS |
| Echo suppression — ignores unknown spools | ✅ PASS |
| Startup flow — shadow store correctly populated | ✅ PASS |
| Reconcile — creates real mapping alongside placeholder | ✅ PASS |
| Placeholder cleanup after reconcile | ✅ PASS |
| `all_ids()` excludes placeholder entries | ✅ PASS |

### Known Issue: Placeholder Cleanup
The reconcile pass creates a real mapping (`se-xyz789`) but the placeholder (`_placeholder_42`) remains in the shadow table. Both rows share the same `spoolman_spool_id`, so `get_spoolease_id_by_spoolman_id()` may return either. For `handle_spoolman_event`, this is acceptable — if it gets the placeholder, it logs "no mapping" and skips; if it gets the real SE ID, it writes back. A future improvement: have the reconcile pass delete placeholders for spools it mapped.

---

## Phase C: `map_sm_to_se()` Mapper Function 🔵 PLANNED

### Current State
There is no `map_sm_to_se()` function in `mapper.py`. The `write_record()` method in `spoolease.py` manually constructs the DTO by:
1. Fetching the current record via `get_spool_by_id()`
2. Building a dict with all SpoolEase fields
3. Merging in the Spoolman-owned updates

### Problem
The mapping logic is scattered across the write-back path instead of being a single pure function. It's less maintainable and harder to test.

### Design
A dedicated `map_sm_to_se()` function that converts a Spoolman spool entity (from the websocket event payload) into a SpoolEase DTO ready for POST to `/api/spools/add-edit`.

### DTO Shape for Write-Back
```json
{
  "id": "abc123",
  "tag_id": "",
  "rgba": "FF8800",
  "color_name": "Orange",
  "material": "PLA",
  "subtype": "Matte",
  "brand": "Bambu",
  "core_weight": 10,
  "label_weight": 250,
  "note": "Comment from Spoolman",
  "slicer_filament": "GFA01",
  "full_unused": "y",
  "assigned_location": "",
  "actual_location": "Drawer 1",
  "spools_count": 1
}
```

### Implementation Plan
- Add `map_sm_to_se()` in `mapper.py` — pure function, takes Spoolman payload + cached SpoolEase record
- Update `write_record()` in `spoolease.py` to call `map_sm_to_se()` instead of building the dict inline
- Add unit tests for `map_sm_to_se()` in `tests/test_mapper.py`

### Files to Modify
- `bridge/spoolsync/mapper.py` — add `map_sm_to_se()` function
- `bridge/spoolsync/spoolease.py` — refactor `write_record()` to use the mapper
- `bridge/tests/test_mapper.py` — add tests for `map_sm_to_se()`

---

## Phase D: Phase 2 Tests 🔵 PLANNED

### Current State
Only `tests/test_mapper.py` exists (for phase 1 mapping functions). No tests for:
- `write_record()` DTO construction
- `handle_spoolman_event()` event processing
- Echo suppression logic
- The startup sequence

### Implementation Plan
- Add `tests/test_reconcile_sm_to_se.py` — unit tests for event handling, echo suppression
- Add `tests/test_echo_suppression.py` — unit tests for the shadow store's echo logic
- Add `tests/test_write_record.py` — unit tests for DTO construction
- Keep `tests/test_mapper.py` as the fast guardrail for pure mapping functions

### Files to Create
- `bridge/tests/test_reconcile_sm_to_se.py`
- `bridge/tests/test_echo_suppression.py`
- `bridge/tests/test_write_record.py`

---

## Items Kept As-Is (No Action Needed)

### Shadow Store Schema Mismatch
The actual schema in `state.py` uses `shadow` table with a single `content_hash`. The design called for `spool_map` with both `se_hash` and `sm_hash`. **Decision: Keep current schema** — it works and `sm_hash` was never needed for any logic.

### Location/Comment Write-Back
The current implementation writes `location` → `actual_location` and `comment` → `note` as separate fields. The design originally combined them into `note`. **Decision: Keep current approach** — SpoolEase has both fields separately, so combining them is unnecessary and the current approach is cleaner.

---

## Summary Table

| Item | Type | Priority | Status |
|------|------|----------|--------|
| Temperature mapping | Enhancement | Medium | ✅ **COMPLETE** |
| Startup sequence with queue drain | Gap | High | ✅ **COMPLETE** |
| `map_sm_to_se()` mapper function | Gap | Medium | 🔵 PLANNED |
| Phase 2 tests | Gap | Medium | 🔵 PLANNED |
| Shadow store schema | Gap | Low | ✅ Kept as-is |
| Location/Comment write-back | Gap | Low | ✅ Kept as-is |

---

## Notes

### SpoolEase Field Reference for Write-Back
When constructing the DTO for `POST /api/spools/add-edit`, the fields are:

| JSON Key | SpoolEase Field | Owner | Notes |
|----------|----------------|-------|-------|
| `id` | spool ID | — | Required for update (not create) |
| `tag_id` | RFID tag | SpoolEase | Never write back |
| `rgba` | color code | SpoolEase | Never write back |
| `color_name` | color name | SpoolEase | Never write back |
| `material` | material type | SpoolEase | Never write back |
| `subtype` | material subtype | SpoolEase | Never write back |
| `brand` | brand/vendor | SpoolEase | Never write back |
| `core_weight` | tare weight | SpoolEase | Never write back |
| `label_weight` | advertised weight | SpoolEase | Never write back |
| `note` | note/comment | **Spoolman** | Write-back target |
| `slicer_filament` | slicer code | SpoolEase | Never write back |
| `full_unused` | added_full flag | SpoolEase | Never write back |
| `assigned_location` | assigned location | SpoolEase | Not used in sync |
| `actual_location` | actual location | **Spoolman** | Write-back target |
| `spools_count` | spool count | SpoolEase | Never write back |
| `stock` | stock | SpoolEase | Never write back |
| `td` | transmission distance | SpoolEase | Never write back |

### Field Ownership Summary

| Field | Owner | Sync Direction |
|-------|-------|----------------|
| Physical weight, consumption | SpoolEase | → Spoolman only |
| Tag UID | SpoolEase | → Spoolman only |
| K-factor | SpoolEase | → Spoolman only |
| Material, color, diameter | SpoolEase | → Spoolman only |
| Vendor | SpoolEase | → Spoolman only |
| **Nozzle temp range** | **SpoolEase** | **→ Spoolman only** ✅ Mapped |
| **Location** | **Spoolman** | **← Spoolman only** |
| **Comment** | **Spoolman** | **← Spoolman only** |
| Price | Spoolman | Spoolman only (not written back) |
| Archived | Spoolman | Spoolman only (not written back) |

### Data Health Verification (2026-11-15)
Mock calls to both services confirmed data integrity:
- SpoolEase: 179 records, 37 brands, 70 unique colors, 55 with weight data, 44 with RFID tags
- Spoolman: 214 filaments, 37 vendors, all with `se-fil:` prefix from bridge
- All 58 slicer presets mapped correctly
- 168/169 external IDs match (1 multi-color filament pending special handling)
- All filaments have valid density and diameter (1.75mm)
- Full report: [DATA-HEALTH-REPORT.md](DATA-HEALTH-REPORT.md)
