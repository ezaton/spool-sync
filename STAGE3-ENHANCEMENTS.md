# Stage 3: Enhancements to the Bridge

## Status
📋 **Planned** — not yet implemented

## Goal
Close the gaps identified between the PHASE2-LOCAL.md design and the current implementation, and enhance temperature mapping between SpoolEase and Spoolman.

---

## Gap 1: Startup Sequence with Queue Drain

### Current State
`run_twoway()` in `sync_twoway.py` starts both sync loops immediately with no coordination:
- The websocket listener connects and begins processing events right away
- The polling loop starts its first reconcile pass independently
- No initial scan phase, no event queue, no drain step

### Problem
Events received before the shadow store has any mappings produce noisy "no SpoolEase mapping for Spoolman spool_id=X" warnings. No events are lost (the lookup simply skips them), but startup is noisy and uncoordinated.

### Design (from PHASE2-LOCAL.md)
```
1. Connect to Spoolman websocket (start listening immediately)
2. Queue incoming events in a thread-safe buffer
3. Poll SpoolEase → populate baseline
4. Poll Spoolman → populate baseline
5. Diff both → populate shadow store (SQLite)
6. Drain queued events → apply changes that arrived during scan
7. Enter normal operation: process websocket events in real-time
```

### Why Queue Instead of Wait?
Waiting for the scan to complete means missing Spoolman changes that occur during the scan. Queueing captures everything and replays it cleanly after the baseline is established. The queue holds only event payloads (small, bounded).

### Implementation Plan
- Add an `asyncio.Queue` in `run_twoway()` before starting either loop
- Pass the queue to the websocket listener callback
- Modify `handle_spoolman_event()` to push events to the queue instead of processing immediately
- After the initial reconcile pass completes, drain the queue
- Then switch to direct processing (or continue queuing with a flag)

### Files to Modify
- `bridge/spoolsync/sync_twoway.py` — add queue, coordinate startup
- `bridge/spoolsync/reconcile_sm_to_se.py` — optionally accept queue vs. direct mode

---

## Gap 2: Missing `map_sm_to_se()` Mapper Function

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

## Gap 3: Shadow Store Schema Mismatch

### Current State
The actual schema in `state.py` uses a different table name and structure than what was designed:
```sql
CREATE TABLE shadow (
    spoolease_id TEXT PRIMARY KEY,
    content_hash TEXT,
    spoolman_spool_id INTEGER,
    archived INTEGER
);
```

### Design (from PHASE2-LOCAL.md)
```sql
CREATE TABLE spool_map (
    spoolease_id TEXT PRIMARY KEY,
    spoolman_id INTEGER,
    se_hash TEXT,      -- last known SpoolEase record hash
    sm_hash TEXT       -- last known Spoolman record hash
);
```

### Problem
There is no `sm_hash` field — only a single `content_hash` (SpoolEase side). The `spool_map` table referenced in the design doesn't exist. The single hash is sufficient for change detection on the SpoolEase side (since SpoolEase has no `updated_at`), but having both hashes would be useful for bidirectional sync diagnostics.

### Decision
**Keep the current schema** (`shadow` table) — it works and the `sm_hash` was never actually needed for any logic. The `spool_map` name was just a design artifact. No action needed unless bidirectional hash tracking becomes useful later.

---

## Gap 4: Location/Comment Write-Back to Separate Fields

### Current State
The current `write_record()` does a simple replacement:
- `location` from Spoolman → `actual_location` field in SpoolEase
- `comment` from Spoolman → `note` field in SpoolEase

### Design (from PHASE2-LOCAL.md)
Location and comment should be combined into SpoolEase's `note` field as "Location: X | Comment: Y".

### Problem
The current implementation writes them to separate fields (`actual_location` and `note`), which diverges from the design's intent of combining them. Additionally, writing comment to `note` **overwrites the entire note field**, losing any location prefix that might have been there.

### Decision
**Keep the current implementation** (separate fields). SpoolEase has both `actual_location` and `note` as separate fields, so there's no need to combine them. The design's concern was based on the assumption that SpoolEase only had one field for both — but it has two. The current approach is actually cleaner.

### Caveat
If SpoolEase's `actual_location` field is used for something else (e.g., printer staging), this could cause conflicts. Need to verify that `actual_location` is a free-form field intended for user-specified locations.

---

## Enhancement 1: Temperature Mapping (SpoolEase → Spoolman)

### Background
SpoolEase stores **nozzle temperature ranges** (min/max) for each filament type, sourced from:
1. **Base filaments CSV** (`base-filaments-index.csv`) — format: `code,name,min_temp,max_temp,material`
   ```
   GFA00,Bambu PLA Basic,190,240,PLA
   GFA01,Bambu PLA Matte,190,240,PLA
   ```
2. **Materials CSV** (`materials.csv`) — format: `material,filament_info,min_temp,max_temp`
   ```
   ABS,GFB99,240,280
   ```
3. **Custom filaments** (user-defined in app config)

Spoolman's `settings_extruder_temp` accepts only a **single value**.

SpoolEase does **not** track bed temperatures anywhere in the codebase.

### Source of Truth
- **Temperatures are owned by SpoolEase** — they come from manufacturer specs in the firmware's CSV files
- **Direction: SpoolEase → Spoolman only** — no write-back of temperatures from Spoolman to SpoolEase
- Spoolman users can override the extruder temp in Spoolman's UI, but that change won't propagate back to SpoolEase

### Mapping Logic
```python
def map_extruder_temp(rec: SpoolRecord) -> int | None:
    """Return midpoint of nozzle temp range, or None if unknown."""
    # If we have the slicer filament code, we can look up the range
    # (The actual lookup happens in mapper.py where we have access to density_map or config)
    if rec.slicer_filament.strip():
        # Known filament code — use exact midpoint from base CSV
        midpoint = get_midpoint_from_base_csv(rec.slicer_filament)
        if midpoint is not None:
            return midpoint
        # Known filament code — use exact midpoint from materials CSV
        midpoint = get_midpoint_from_materials_csv(rec.material_type)
        if midpoint is not None:
            return midpoint
    
    # Unknown/custom filament — use a reasonable guess
    # PLA-like default: 215 ± 25
    return DEFAULT_EXTRUDER_TEMP_GUESS
```

Where `DEFAULT_EXTRUDER_TEMP_GUESS` = 215 (midpoint of typical PLA range 190–240).

### Implementation Plan
- Add `DEFAULT_EXTRUDER_TEMP` config value (default: 215) to `config.py`
- Add `map_extruder_temp()` function in `mapper.py`
- Update `map_filament()` to call `map_extruder_temp()` and populate `settings_extruder_temp`
- Leave `settings_bed_temp` as `None` (SpoolEase doesn't track it)

### Files to Modify
- `bridge/spoolsync/config.py` — add `default_extruder_temp` field
- `bridge/spoolsync/mapper.py` — add `map_extruder_temp()`, update `map_filament()`
- `bridge/tests/test_mapper.py` — add tests for temperature mapping

---

## Gap 5: No Tests for Phase 2

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

## Summary Table

| Item | Type | Priority | Effort |
|------|------|----------|--------|
| Startup sequence with queue drain | Gap | High | Medium |
| `map_sm_to_se()` mapper function | Gap | Medium | Low |
| Shadow store schema | Gap | Low | None (keep current) |
| Location/Comment write-back | Gap | Low | None (keep current) |
| Temperature mapping | Enhancement | Medium | Low |
| Phase 2 tests | Gap | Medium | Medium |

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
| **Nozzle temp range** | **SpoolEase** | **→ Spoolman only** |
| **Location** | **Spoolman** | **← Spoolman only** |
| **Comment** | **Spoolman** | **← Spoolman only** |
| Price | Spoolman | Spoolman only (not written back) |
| Archived | Spoolman | Spoolman only (not written back) |
