# Reference: Spoolman — data model + HTTP API (ground truth)

Distilled facts a future session needs to read/write Spoolman without re-exploring. Repo:
`D:\Users\c-etzion.barnoy\GIT\private\Spoolman` (Python/FastAPI). Data model was read
directly; the API catalog came from an Explore subagent — **spot-check an exact
endpoint/schema before depending on it.**

## Data model — `spoolman/database/models.py`

- **Vendor** (:28): `id`, `registered`, `name`, `empty_spool_weight`, `comment`,
  `external_id`, `extra[]`.
- **Filament** (:45): `id`, `registered`, `name`, `vendor_id`→Vendor, `material`, `price`,
  **`density` (NOT NULL)**, **`diameter` (NOT NULL)**, `weight` (full NET), `spool_weight`
  (tare), `article_number`, `comment`, `settings_extruder_temp`, `settings_bed_temp`,
  `color_hex` (6/8 hex), `multi_color_hexes`, `multi_color_direction`, `external_id`,
  `extra[]`, `tags[]`.
- **Spool** (:83): `id`, `registered`, `first_used`, `last_used`, `price`, `filament_id`,
  `initial_weight` (net), `spool_weight` (tare), **`used_weight` (the only stored weight)**,
  `location`, `lot_nr`, `comment`, `archived`, `extra[]`, `tags[]`.
- **Tag** (:150): `uid` (unique, normalized uppercase hex), `target_type`, `spool_id` |
  `filament_id` | `target_value`, `format`, `added`. One physical tag → exactly one target.
- **{Vendor,Filament,Spool}Field**: `extra` custom fields (key/value, value JSON-encoded).

`remaining_weight` and lengths are **computed, read-only**:
`remaining_weight = max(initial_weight − used_weight, 0)` (falls back to `filament.weight`).

## API base & conventions

- All routers under **`/api/v1`** (`main.py:57`). Responses use
  `response_model_exclude_none=True` → **null/unset fields are omitted**.
- Lists: `limit`/`offset` paging; total in **`x-total-count`** header.
- Filters: `extra.<key>`, and on spool also `filament.extra.<key>`, `filament.vendor.extra.<key>`.

## Endpoints the bridge uses

### Vendor — `api/v1/vendor.py`
| Method | Path | Notes |
|---|---|---|
| GET | `/vendor?external_id=&name=&extra.<k>=` | find for idempotent upsert |
| POST | `/vendor` | `{name*, comment, empty_spool_weight, external_id, extra}` |
| PATCH | `/vendor/{id}` | partial (`exclude_unset`) |

### Filament — `api/v1/filament.py`
| Method | Path | Notes |
|---|---|---|
| GET | `/filament?external_id=&vendor.id=&material=&color_hex=&tag=` | find |
| POST | `/filament` | **`density`* (>0), `diameter`* (>0)** required; +`name, vendor_id, material, price, weight, spool_weight, article_number, comment, settings_extruder_temp, settings_bed_temp, color_hex (6/8 hex, '#' stripped), multi_color_hexes, multi_color_direction, external_id, extra` |
| PATCH | `/filament/{id}` | `density`/`diameter` optional-but-not-null; `extra` REPLACES all |
| DELETE | `/filament/{id}` | 403 if in use |
| POST/DELETE | `/filament/{id}/tag[/{uid}]` | link/unlink NFC tag |

### Spool — `api/v1/spool.py`
| Method | Path | Notes |
|---|---|---|
| GET | `/spool?tag=&filament.id=&location=&allow_archived=&extra.<k>=` | find (tag query accepts any separator) |
| POST | `/spool` | `{filament_id*, initial_weight, spool_weight, remaining_weight XOR used_weight, price, location, lot_nr, comment, archived, extra}` |
| PATCH | `/spool/{id}` | partial; `extra` replaces all |
| PUT | `/spool/{id}/use` | `{use_length}` XOR `{use_weight}` (g) — increments `used_weight` |
| PUT | `/spool/{id}/measure` | `{weight}` = **gross** scale reading; server computes the delta |
| POST/DELETE | `/spool/{id}/tag[/{uid}]` | link/unlink; **409** if uid already linked elsewhere |

> Weight choice for this project: send `remaining_weight` (bridge computes net the SpoolEase
> way) rather than `/measure` with raw gross, so SpoolEase — not Spoolman — owns the net calc.

### extra-field registry — `api/v1/field.py`
- `POST /field/{entity}/{key}` with `{name*, field_type*∈text|integer|float|boolean|choice|…,
  order, unit, default_value, choices, multi_choice}`. Register **before** setting values.
- Values in an entity's `extra` are **JSON-encoded strings**: text→`"\"x\""`, int→`"42"`,
  bool→`"true"`. On PATCH, per-key merge; `null` clears a key.

### external catalog — `api/v1/externaldb.py` (prefix `/external`)
- `GET /external/material` → `[{material, density, extruder_temp, bed_temp}]` — **use this to
  fill Filament.density** by material name.
- `GET /external/filament[/search?query=]` → upstream SpoolmanDB mirror (read-only; default
  `https://donkie.github.io/SpoolmanDB/`, env `EXTERNAL_DB_URL`, hourly). Not in the SQL DB;
  linkage to local rows is **by convention**: copy the catalog id into local `external_id`.

### websockets — `ws.py`  (phase-2 change-driven sync)
- Connect a WS to the REST path: `/api/v1/spool` (all spools), `/api/v1/spool/{id}`, same for
  `filament`/`vendor`, `/api/v1/` = everything. Events propagate up the tree.
- Envelope: `{type: added|updated|deleted, resource, date, payload}`; payload is the full
  entity (same shape as REST). **Bulk `PATCH /spool/field/{field}` does NOT emit per-row
  events.** Scan events are on a separate tree (`/api/v1/tag/scan`), never on entity trees.

### export — `api/v1/export.py`
- `GET /export/{spools|filaments|vendors}?fmt=csv|json` (file download). **No import
  endpoint** — import via the normal POSTs.

## Auth & security — `security.py`

- **No API auth** of any kind for a non-browser client (`security.py:3`). The guard is
  `TrustedOriginMiddleware` (blocks writes/WS from untrusted browser **Origins**) +
  optional `TrustedHostMiddleware`. A server-to-server client sends **no `Origin`** → always
  trusted. So: just `Content-Type: application/json`, no auth header, no Origin.
- Env knobs: `SPOOLMAN_CORS_ORIGIN` (`*` disables origin check), `SPOOLMAN_ALLOWED_HOSTS`,
  `SPOOLMAN_DEBUG_MODE`. `SPOOLMAN_DB_PASSWORD*` is DB-only, unrelated to API.

## In-process extension seam (if ever hosting sync inside Spoolman)

`main.py:236-240` builds a `Scheduler` and calls `externaldb.schedule_tasks(schedule)`.
`externaldb.py:243-261` = check a config URL → run `_sync()` once → `scheduler.cyclic(...)`.
A `spoolease_sync.py` of the same shape + one line in `main.py` would mirror it (and could
call the DB layer directly). Not the chosen path, but documented in `decision.md`.
