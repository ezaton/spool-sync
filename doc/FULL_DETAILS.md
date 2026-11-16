# SpoolEase ↔ Spoolman Sync — Code Reference

Detailed documentation of the bridge implementation (`bridge/spoolsync/`). This is the
**code-level reference** — how each module works, its responsibilities, key functions, and
interactions. For design/architecture decisions, see [DESIGN.md](DESIGN.md).

---

## Package Overview

```
bridge/spoolsync/
  __init__.py          Package entry; docstring (two-way bridge, version 0.1.0)
  __main__.py          CLI entrypoint: python -m spoolsync
  config.py            Runtime config from env vars (typed dataclass)
  models.py            SpoolRecord dataclass + JSON contract + content_hash
  mapper.py            Pure flat↔normalized mapping functions (no I/O)
  state.py             SQLite shadow store (id map, shadow hashes, echo suppression)
  spoolman.py          Spoolman REST client + websocket listener
  spoolease.py         SpoolEase connector (JSON + encrypted modes)
  reconcile.py         SE→SM reconciliation pass (phase 1)
  reconcile_sm_to_se.py SM→SE event handler (phase 2)
  sync.py              Phase 1 loop orchestration (blocking)
  sync_twoway.py       Phase 2 loop orchestration (async)
```

---

## Module-by-Module Reference

### `config.py` — Runtime Configuration

Loads all settings from environment variables into a frozen `Config` dataclass.

**Key functions:**

- `Config.from_env()` — reads all env vars, validates required fields, returns `Config`.
- `_get(name, default)` — strips whitespace, returns None if empty.
- `_get_bool(name, default)` — recognizes `1`, `true`, `yes`, `on` as truthy.
- `_get_float(name, default)` / `_get_int(name, default)` — typed conversion.

**Verbose mode logic:** When `VERBOSE=yes` and `LOG_LEVEL` is not explicitly set,
auto-sets `LOG_LEVEL=DEBUG`. Otherwise defaults to `INFO`. This is non-breaking:
`log.debug()` calls are zero-cost when disabled.

**Environment variables:**

| Variable | Default | Description |
|---|---|---|
| `SPOOLMAN_URL` | *(required)* | Spoolman API base URL |
| `SPOOLEASE_URL` | *(required)* | SpoolEase base URL |
| `SPOOLEASE_MODE` | `json` | `json` or `encrypted` |
| `SPOOLEASE_TOKEN` | — | Bearer token for JSON mode |
| `SPOOLEASE_SECURITY_KEY` | — | 7-char key for encrypted mode |
| `TWO_WAY_SYNC` | `false` | Enable SM→SE write-back via websocket |
| `DEFAULT_DIAMETER` | `1.75` | Fallback filament diameter (mm) |
| `DEFAULT_DENSITY` | `1.24` | Fallback density (g/cm³) |
| `DEFAULT_EXTRUDER_TEMP` | `215` | Fallback extruder temp (°C) |
| `SYNC_INTERVAL_SECONDS` | `30` | Poll interval |
| `DRY_RUN` | `false` | Log writes without executing |
| `ARCHIVE_MISSING` | `true` | Archive spools removed from SpoolEase |
| `VERBOSE` | `false` | Enable per-record DEBUG-level audit logging |
| `LOG_LEVEL` | auto | Python log level; auto-sets to `DEBUG` when `VERBOSE=yes` |
| `STATE_PATH` | `/state/spoolsync.sqlite` | Shadow store SQLite path |

---

### `models.py` — SpoolRecord Dataclass

Defines the SpoolEase record shape the bridge consumes. Targets a **clean JSON contract**
(one object per spool, plain field types) from the optional `GET /api/spools.json` endpoint.

**Fields:** Mirrors `SpoolEase/core/src/spool_record.rs::SpoolRecord` — 21 fields covering
identity, material, color, weight, consumption tracking, and tag metadata.

**Key methods:**

- `SpoolRecord.from_json(obj)` — parses a dict, filtering unknown keys into `_extra`.
- `SpoolRecord.has_valid_tag()` — mirrors `SpoolRecord::has_valid_tag_id`; a `'-'` prefix
  marks a retired tag.
- `SpoolRecord.content_hash()` — stable SHA-256 hash of the full record (minus `_extra`).
  Used for change detection since SpoolEase has no `updated_at` field.

**Critical design note:** This dataclass uses **normal Python types** (not the base64 floats
or y/n bools from SpoolEase's serde adapters). The bridge operates on this clean DTO, not
on the raw CSV serialization.

---

### `mapper.py` — Pure Mapping Functions

**No I/O.** All functions are deterministic and unit-tested (`tests/test_mapper.py`).

**Dataclasses (output specs):**

- `VendorSpec(name, external_id, empty_spool_weight)` — frozen.
- `FilamentSpec(external_id, name, material, color_hex, weight, spool_weight, density, diameter, article_number, settings_extruder_temp, settings_bed_temp)` — frozen. `density` and `diameter` are required (not optional).
- `SpoolSpec(spoolease_id, tag_uid, tag_format, initial_weight, spool_weight, remaining_weight, comment, extra)` — frozen.

**Key functions:**

- `map_vendor(rec)` → `VendorSpec | None` — derives vendor from `rec.brand`. Returns None
  if brand is empty. `external_id` = `"se-brand:{slug}"`.
- `map_filament(rec, density, diameter, default_extruder_temp)` → `FilamentSpec` — maps all
  filament fields. Material convention: `material_type` goes into `material` (e.g. `"PLA"`),
  subtype goes into the name (e.g. `"Basic Black"`).
- `map_spool(rec)` → `SpoolSpec` — computes remaining weight, sets tag UID, creates
  `extra.spoolease_id` (JSON-encoded per Spoolman's contract).
- `map_sm_to_se(cached_spoolease, updates)` → `dict` — Phase C: converts cached SpoolEase
  record + Spoolman-owned updates into a SpoolEase DTO for write-back.

**Weight computation (`compute_remaining`):**

```
tare = weight_core OR (weight_new - weight_advertised)
if weight_current is not None:
    remaining = weight_current - tare - consumed_since_weight
elif weight_advertised is not None:
    remaining = weight_advertised - consumed_since_add
remaining = max(0.0, round(remaining, 1))
```

**Temperature mapping:** 98 base filament codes with exact min/max ranges from
`base-filaments-index.csv`, plus 47 material-type fallbacks from `materials.csv`. Returns
the midpoint of the range. For unrecognized filaments, uses the caller-provided default.

**Identity functions:**

- `vendor_external_id(brand)` → `"se-brand:{slug}"`
- `filament_external_id(rec)` → `"se-fil:{slicer_filament}:{color}"` (falls back to
  synthesized key from brand+material+color when no slicer code).

**Helpers:**

- `_slug(s)` — lowercases, replaces non-alphanumeric with `-`.
- `_norm_hex(color_code)` — strips `#`, validates 6 or 8 hex chars, returns uppercase.
- `_json_str(value)` — JSON-encodes a string (for Spoolman's `extra` field contract).
- `_tare(rec)` — returns `weight_core` if known, else `weight_new - weight_advertised`.
- `tag_format(rec)` — derives `"bambu"`, `"openprinttag"`, or raw origin from
  `data_origin`/`tag_type`.

---

### `state.py` — SQLite Shadow Store

The **neutral third store** the bridge exists to own. Persists across restarts.

**Database schema (2 tables):**

```sql
-- Shadow: last-known state per SpoolEase record
CREATE TABLE shadow (
    spoolease_id      TEXT PRIMARY KEY,
    content_hash      TEXT NOT NULL,
    spoolman_spool_id INTEGER,
    archived          INTEGER NOT NULL DEFAULT 0
);

-- Recent writes: echo suppression tracking
CREATE TABLE recent_writes (
    spoolman_spool_id INTEGER NOT NULL,
    timestamp         REAL NOT NULL,
    direction         TEXT NOT NULL,  -- 'to_spoolman' or 'to_spoolease'
    content_snapshot  TEXT NOT NULL   -- JSON of key fields
);
CREATE INDEX idx_recent_writes_spool ON recent_writes(spoolman_spool_id);
CREATE INDEX idx_recent_writes_time ON recent_writes(timestamp);
```

**Key methods:**

- `State(path)` — creates/connects to SQLite, creates tables if missing.
- `get(spoolease_id)` → `ShadowRow | None` — lookup by SpoolEase ID.
- `upsert(row)` — insert or update shadow row.
- `all_ids()` → `set[str]` — all real SpoolEase IDs (excludes `_placeholder_*`).
- `get_by_spoolman_id(id)` → `ShadowRow | None` — reverse lookup.
- `get_spoolease_id_by_spoolman_id(id)` → `str | None` — reverse lookup for write-back.
- `all_spoolman_ids()` → `set[int]` — all known Spoolman IDs.

**Placeholder support:** `upsert_placeholder(spoolman_spool_id)` creates entries with
sentinel PKs (`_placeholder_{id}`) for Spoolman-only spools during baseline scan. The
reconcile pass overwrites with real SpoolEase IDs.

**Echo suppression methods (Phase 2):**

- `record_write_to_spoolman(spoolman_id, snapshot)` — tracks a write we made to Spoolman.
- `record_write_to_spoolease(spoolman_id, snapshot)` — tracks a write-back to SpoolEase.
- `is_echo_from_spoolman(spoolman_id, current_content, window_seconds=30)` — checks if
  incoming Spoolman change matches a recent write we made. Compares tracked fields:
  `location`, `price`, `archived`, `comment`, `remaining_weight`.
- `is_echo_from_spoolease(spoolman_id, current_content, window_seconds=30)` — same for
  write-back echo detection.
- `_content_matches(recorded, current)` — compares key fields; a key present in one dict
  but not the other is a mismatch. Keys absent from both are ignored.

**Cleanup:** Old entries (>60 seconds) are pruned on each write operation.

---

### `spoolman.py` — Spoolman REST Client

Plain JSON client. **No auth header, no Origin header** — Spoolman's `TrustedOriginMiddleware`
allows non-browser clients (no `Origin` = trusted).

**Key methods:**

- `__init__(base_url, timeout=30.0, dry_run=False)` — creates httpx.Client.
- `close()` — closes underlying httpx.Client.
- `external_material_densities()` → `dict[str, float]` — material(lowercased) → density
  from `GET /external/material`. Used to fill Spoolman's REQUIRED Filament.density.
- `ensure_spoolease_id_field()` — registers the `spoolease_id` extra field once (POST to
  `/field/spool/spoolease_id`). Non-fatal if already exists.
- `upsert_vendor(spec)` → `int | None` — find by `external_id`, PATCH or POST.
- `upsert_filament(spec, vendor_id)` → `int | None` — find by `external_id`, PATCH or POST.
- `find_spool_id(spec)` → `int | None` — try tag UID first, then `extra.spoolease_id`.
  The extra value is wrapped in quotes for exact match against JSON-encoded storage.
- `upsert_spool(spec, filament_id, existing_id)` → `int | None` — PATCH or POST, then
  link tag if present.
- `_link_tag(spool_id, uid, fmt)` — handles semicolon-separated tag lists. Ignores 409
  (already linked) and logs 400 errors without failing.
- `archive_spool(spool_id)` — PATCH `archived=true`.
- `get_spool(spool_id)` → `dict | None` — fetch single spool.
- `get_all_spools()` → `list[dict]` — fetch all spools (including archived). Used for
  baseline scan in Phase 2.
- `listen_websocket(on_event_callback)` — async method. Connects to `ws://` equivalent of
  the REST URL + `/spool`. Auto-reconnects with exponential backoff (1s → 60s max).
  Handles JSON decode errors and logs exceptions without crashing.

**Internal methods:**

- `_get(path, **params)` — GET with query params, filters None values.
- `_write(method, path, json_body)` — HTTP request; returns None in dry-run mode.

---

### `spoolease.py` — SpoolEase Connector

Two modes: **JSON** (recommended, needs the small SpoolEase endpoint) and **encrypted**
(reads the stock AES-GCM `/api/spools` blob).

**Key methods:**

- `__init__(base_url, mode="json", token=None, security_key=None, timeout=20.0)`
- `close()` — closes httpx.Client.
- `fetch_records()` → `list[SpoolRecord]` — dispatches to `_fetch_json()` or
  `_fetch_encrypted()`.
- `get_spool_by_id(record_id)` → `dict | None` — fetches all records, finds by ID, returns
  a dict for manipulation (used by write-back).
- `write_record(record_id, updates)` → `bool` — Phase 2: fetches full record, merges
  Spoolman-owned updates via `map_sm_to_se()`, sends via JSON or encrypted POST.

**JSON mode (`_fetch_json`):** GET `/api/spools.json`, accepts bare list or
`{"spools": [...]}` wrapper. Parses each object into `SpoolRecord`.

**Encrypted mode (`_fetch_encrypted`):** GET `/api/spools`, decrypts AES-256-GCM, parses
CSV with adapted field decoding:
- Base64 floats → `struct.unpack("<f", ...)` for `consumed_since_*`.
- y/n bools → `"y"/"yes"/"true"` → True.
- Integer fields → `int` or None.
- Optional floats (`td`) → `float` or None.

**Encrypted write (`_encrypt_json`):** PBKDF2-HMAC-SHA256(security_key, "example_salt",
10000 iters) → AES-GCM encrypt. Output: base64(nonce) + base64(ciphertext||tag), unpadded.

**Response decryption (`_try_decrypt_response`):** If response starts with `{`, it's
plaintext (success or error). Otherwise, decrypts the same way as fetch.

---

### `reconcile.py` — SE→SM Reconciliation Pass

Each pass: fetch all SpoolEase records, diff against shadow store by content hash, upsert
Vendor → Filament → Spool for anything new or changed. Records that disappeared are archived.

**Function: `reconcile_once(se, sm, state, cfg, density_map)` → `dict[str, int]`**

Returns stats: `{"created": N, "updated": N, "unchanged": N, "archived": N, "errors": N}`.

**Flow:**
1. Fetch all records from SpoolEase.
2. For each record:
   a. Compute content hash.
   b. Look up shadow. If hash matches and not archived → skip (unchanged).
   c. Resolve density from `density_map` (exact match, then prefix match, then default).
   d. Map → upsert Vendor → upsert Filament → upsert Spool.
   e. Record write for echo suppression (Phase 2).
   f. Update shadow store.
3. If `archive_missing`: archive any shadow entries no longer in current SpoolEase.

**Error handling:** Each record is wrapped in try/except — one bad record won't stall the
entire sync loop. Errors are logged and counted.

**Verbose logging:** When `cfg.verbose`, logs each record with action (created/updated/
unchanged/archived), vendor, filament, spoolman_id, and weight.

---

### `reconcile_sm_to_se.py` — SM→SE Event Handler

Processes Spoolman websocket events and writes back only Spoolman-owned fields.

**Function: `handle_spoolman_event(event, se, state, cfg=None, dry_run=False)`**

**Flow:**
1. Filter: only process `resource == "spool"` events with a valid `payload.id`.
2. Echo suppression: check `state.is_echo_from_spoolman()`. If echo → drop.
3. Resolve: look up SpoolEase ID via `state.get_spoolease_id_by_spoolman_id()`.
4. Extract: pull Spoolman-owned fields (`location`, `comment`) from payload.
5. Dry-run: log without executing if `dry_run=True`.
6. Write-back: call `se.write_record(spoolease_id, updates)`.
7. On success: record write for echo suppression on the SE→SM side.

**Field ownership:** Only `location` and `comment` are currently mapped back. `price` and
`archived` are not yet supported due to SpoolEase limitations.

---

### `sync.py` — Phase 1 Loop Orchestration

Blocking event loop. Wires up clients + state, reconciles on interval.

**Function: `run(cfg)`**

1. Configure logging at `cfg.log_level`.
2. Create `State`, `SpoolmanClient`, `SpooleaseClient`.
3. One-time setup: register `spoolease_id` field, load material densities.
4. Loop: reconcile, sleep interruptibly (1s chunks), check SIGINT/SIGTERM.
5. Finally: close all clients and state.

**Signal handling:** `_Stop` class registers handlers for SIGINT/SIGTERM that set a flag.
The sleep loop checks this flag every second.

---

### `sync_twoway.py` — Phase 2 Loop Orchestration

Async event loop with concurrent polling and websocket listening.

**Function: `run_twoway(cfg)`**

1. Configure logging.
2. Create clients + state.
3. **Startup sequence** (`_startup_sequence`):
   a. Start websocket listener in background (queues events during scan).
   b. Run initial SE→SM reconcile pass.
   c. Run initial SM baseline scan (`_scan_spoolman_baseline`).
   d. Signal startup complete → switch listener to direct processing.
   e. Drain queued events.
   f. Cancel startup listener (normal listener takes over).
4. **Normal operation:** `asyncio.gather()` runs both loops concurrently:
   a. `_poll_spoolease_to_spoolman()` — same as `sync.py` loop but async.
   b. `_listen_spoolman_to_spoolease()` — calls `sm.listen_websocket()`.

**Startup sequence rationale:** Without it, websocket events arriving during the baseline
scan would trigger "no mapping" warnings because the shadow store isn't populated yet. The
queue ensures events are processed in the right order.

**Baseline scan (`_scan_spoolman_baseline`):** Fetches all Spoolman spools, creates
placeholder shadow entries for Spoolman-only spools (those without `se-fil:` in
`external_id`). This ensures echo suppression and reverse lookups work from the start.

---

## Test Suite

### `tests/test_mapper.py` — Mapping Logic Tests

Tests for all pure mapping functions:

- **Weight computation:** weighed records (gross - tare - consumed), unweighed fallback
  (advertised - consumed_since_add), clamping at zero.
- **Identity functions:** stable vendor/filament IDs, dedup across records, synthesized
  keys without slicer codes.
- **Filament mapping:** density/diameter required, color normalization, material convention.
- **Spool mapping:** tag UID linking, retired tag handling, JSON-encoded extra fields.
- **Temperature mapping:** 98 base filament codes, 47 material fallbacks, custom defaults.
- **Write-back mapping:** `map_sm_to_se()` preserves SpoolEase fields, overwrites location
  and comment, handles bool→y/n conversion, optional fields (stock, td).

### `tests/test_echo_suppression.py` — Echo Suppression Tests

Tests for the shadow store's echo suppression logic:

- **Spoolman echo:** matching content detected, different content passes through, unknown
  spool IDs pass through, time window expiry, custom window parameter.
- **Spoolease echo:** matching content detected, different content passes through.
- **Cross-direction isolation:** writes to one direction don't trigger the other's echo
  detection.
- **Content matching:** key-by-key comparison, extra keys ignored when matching, None vs
  empty string distinction.

### `tests/test_reconcile_sm_to_se.py` — Event Handler Tests

Tests for `handle_spoolman_event()` using mock objects:

- **Resource filtering:** non-spool events ignored, missing resource ignored, missing ID
  logged and skipped.
- **Echo suppression:** echo events suppressed, non-echo events proceed.
- **Mapping:** unmapped spools skipped, mapping lookup called with correct ID.
- **Field extraction:** location and comment extracted, both together, None values included.
- **Write-back:** correct SpoolEase ID, echo recording on success, no echo recording on
  failure.
- **Dry-run:** write skipped, mapping still resolved.

---

## Data Flow Between Modules

```
Config.from_env()
    ↓
run() / run_twoway()
    ├── State(cfg.state_path)          ← SQLite shadow store
    ├── SpoolmanClient(cfg.spoolman_url)  ← REST + WS client
    ├── SpooleaseClient(cfg.spoolease_url) ← HTTP connector
    │
    ├── reconcile_once(se, sm, state, cfg, density_map)
    │   ├── se.fetch_records()         ← fetch all SpoolEase records
    │   ├── for rec in records:
    │   │   ├── rec.content_hash()     ← compute change detection hash
    │   │   ├── state.get(rec.id)      ← lookup shadow
    │   │   ├── _resolve_density()     ← density map lookup
    │   │   ├── map_vendor(rec)        ← pure mapping
    │   │   ├── sm.upsert_vendor()     ← REST upsert
    │   │   ├── map_filament()         ← pure mapping
    │   │   ├── sm.upsert_filament()   ← REST upsert
    │   │   ├── map_spool(rec)         ← pure mapping
    │   │   ├── sm.upsert_spool()      ← REST upsert
    │   │   └── state.upsert(shadow)   ← persist state
    │   └── archive missing records
    │
    └── handle_spoolman_event(event, se, state, cfg)
        ├── state.is_echo_from_spoolman()  ← echo check
        ├── state.get_spoolease_id_by_spoolman_id()  ← reverse lookup
        ├── se.write_record(id, updates)   ← write back to SpoolEase
        └── state.record_write_to_spoolease()  ← track for echo suppression
```

---

## Key Gotchas (From Experience)

1. **SpoolRecord CSV is not plain.** The serde adapters (`serialize_f32_base64`,
   `serialize_bool_yn`) apply in *any* format. The JSON sync endpoint must use a plain
   DTO. The bridge's `models.py` already expects the clean DTO shape.

2. **No `updated_at` on SpoolEase.** Change detection is content-based via `content_hash()`.
   Only `added_time` / `encode_time` exist (set at create).

3. **`weight_current` is GROSS, not remaining.** `weight_current − tare − consumed_since_weight`
   = net remaining. This was re-derived every time someone guessed otherwise.

4. **Spoolman needs `density` and `diameter` > 0 on every Filament.** SpoolEase stores
   neither. The bridge supplies defaults + the external-material density map.

5. **Spoolman `extra` values are JSON-encoded strings.** `"42"` is stored as `"\"42\""`.
   Register a field via `POST /field/spool/<key>` before setting values, or writes 400.

6. **Spoolman auth:** none for a non-browser client (no `Origin` header ⇒ trusted).
   Don't add an auth header; don't send an `Origin`.

7. **Echo suppression window:** 30-second default window for echo detection. Entries older
   than 60 seconds are pruned on each write. This is short enough to catch echoes but
   doesn't accumulate unbounded state.

8. **Phase 2 startup sequence:** Without the queue-and-drain pattern, websocket events
   arriving during the baseline scan would trigger "no mapping" warnings. The startup
   sequence ensures the shadow store is populated before events are processed.
