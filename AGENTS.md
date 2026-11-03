# AGENTS.md — handoff for a future AI session

Read this first. It orients you to the SpoolEase ↔ Spoolman sync project: what it is, what's
been decided, what's built, what's verified vs. assumed, the environment's quirks, and what
to do next. It links to the design docs and to two **reference** files that preserve the
expensively-gathered ground truth about both systems (so you don't have to re-explore).

---

## 1. Goal & current decision

- **Goal:** two-way sync of filament **type/details** and **weight** between **SpoolEase**
  and **Spoolman**.
- **Chosen approach (user-confirmed):** an **external bridge** (standalone Python service
  next to Spoolman) + a small, optional **JSON endpoint on SpoolEase**. **Spoolman is not
  modified.** Rationale and the rejected alternatives are in [decision.md](decision.md).
- **Why external (short version):** two-way sync needs neutral bookkeeping (id map +
  per-side last-known state + tombstones) that belongs to neither product; the two data
  models are asymmetric (flat ↔ normalized, 1:N); and the device is `no_std` firmware that's
  intermittently online. A product modification *alone* can't host that well.

## 2. The two systems (one-liners; full facts in `reference/`)

- **Spoolman** — `D:\Users\c-etzion.barnoy\GIT\private\Spoolman`. Python/FastAPI inventory
  service, relational DB (SQLite/PG/MySQL). Normalized model **Vendor → Filament → Spool**,
  plus NFC `Tag` and `extra`/`external_id` hooks. Complete REST API + websockets, **no auth
  for a server-to-server client**. → [reference/spoolman-api.md](reference/spoolman-api.md).
- **SpoolEase** — `D:\Users\c-etzion.barnoy\GIT\private\SpoolEase`. Rust **`no_std` ESP32-S3
  firmware** (embassy async). DB is a **flat CSV on microSD**, one `SpoolRecord` per physical
  spool, keyed by string `id`. One row flattens type + weight + Bambu K-calibration. **No
  `updated_at`.** → [reference/spoolease-internals.md](reference/spoolease-internals.md).

> Path note: the folder is `SpoolEase` but Windows is case-insensitive, so `spoolman` /
> `spoolease` in paths also resolve. Both subdirs are git repos; `sync-tool` is not.

## 3. Documents in this directory

| File | What it is |
|---|---|
| [README.md](README.md) | Index + chosen approach + status |
| [decision.md](decision.md) | External tool vs. in-product modification — analysis + decision matrix |
| [architecture.md](architecture.md) | Components, deployment topology, bridge internals, interface options |
| [data-flow.md](data-flow.md) | Both sync directions, change detection, identity keys, conflict/echo rules |
| [data-mapping.md](data-mapping.md) | Field-by-field mapping, per-field source of truth, **resolved weight semantics** |
| [spoolease-endpoints.md](spoolease-endpoints.md) | The SpoolEase changeset (annotated Rust sketch) |
| **reference/spoolman-api.md** | Distilled Spoolman REST/WS/auth catalog (with file:line) |
| **reference/spoolease-internals.md** | Distilled SpoolEase storage/networking/data-flow/weight (with file:line) |
| `bridge/` | The Python bridge, phase 1 (see `bridge/README.md`) |

## 4. What's built (and its real status)

- **Design docs:** complete.
- **Weight semantics:** RESOLVED by reading the firmware (not assumed). `weight_current` is a
  **gross** scale reading; net remaining = `weight_current − tare − consumed_since_weight`.
  See `reference/spoolease-internals.md` and `data-mapping.md`.
- **Bridge (`bridge/spoolsync`):** phase-1 **one-way** SpoolEase → Spoolman. Fetch all
  records → diff by content hash against a SQLite shadow store → upsert Vendor/Filament/Spool
  → archive vanished spools. Idempotent via tag UID / `external_id` / `extra.spoolease_id`.
  - ⚠️ **NEVER EXECUTED.** The authoring machine has **no Python and no Docker**. The code is
    a careful draft only. One real bug was already fixed during review (it set an
    unregistered `extra` field that Spoolman would 400). **Before trusting it: run
    `pytest` and a `DRY_RUN=true` pass.**
- **SpoolEase endpoint:** designed only (`spoolease-endpoints.md`). Not written into the
  SpoolEase repo (can't compile `no_std` firmware here).

## 5. Verified vs. assumed (trust calibration)

**Verified by directly reading source** (high trust):
- Spoolman data model — `Spoolman/spoolman/database/models.py` (Vendor:28, Filament:45,
  Spool:83, Tag:150).
- Spoolman has an in-process scheduler seam — `Spoolman/spoolman/main.py:236-240`, pattern in
  `externaldb.py:225,243-261`.
- SpoolEase SpoolRecord — `SpoolEase/core/src/spool_record.rs:24-59` (+ serde gotcha, §6).
- SpoolEase storage — `csvdb.rs` (records pub:93, insert:232), `store.rs` (query_spools:171,
  add_spool:235, update_spool:398, edit_spool_from_web:275).
- SpoolEase weight semantics — `view_model.rs:1346-1357` (`weight_left_spool`), 1941-1942.
- SpoolEase web routes — `web_app.rs` (`/api/spools`:258-269, add-edit:293-374).
- SpoolEase catalog CSVs — `SpoolEase/core/data/*.csv` (previewed).

**From two Explore subagents** (model-derived; high confidence but SPOT-CHECK the exact
endpoint/field before depending on it in code): the full Spoolman REST endpoint catalog with
request/response schemas, and SpoolEase's networking inventory. Both distilled into
`reference/`. These were not re-verified line-by-line by the main session.

**Assumptions still to confirm** (also listed in `data-mapping.md`):
1. SpoolEase diameter is always **1.75 mm** (bridge defaults to it).
2. Density source: Spoolman `GET /external/material`, else `DEFAULT_DENSITY`. Confirm values.
3. `material` convention: `"PLA"` with subtype in the name vs. `"PLA Matte"` in `material`
   (bridge currently does the former — `mapper.map_material`).
4. Spoolman accepts SpoolEase's 8-char `RRGGBBAA` as `color_hex` (agent says 6/8 hex OK).
5. The `GET /spool?extra.spoolease_id="<id>"` exact-match query syntax (bridge wraps the
   value in quotes; the shadow store usually makes this moot after the first sync).
6. picoserve's header/token extractor API for the SpoolEase endpoint (confirm against the
   `picoserve` version in `SpoolEase/core/cargo.toml`).

## 6. Gotchas that cost time (don't relearn these)

- **SpoolRecord CSV is not plain.** `spool_record.rs` puts serde adapters on the struct:
  `serialize_f32_base64` (floats → base64) and `serialize_bool_yn` (bools → `"y"/"n"`). They
  apply in **any** format, so `serde_json::to_string(&SpoolRecord)` would emit base64 floats
  and y/n bools. The SpoolEase JSON endpoint MUST serialize a **plain DTO** instead
  (`spoolease-endpoints.md`, "Change 1"). The bridge's `models.py` already expects the clean
  DTO shape.
- **No `updated_at` on SpoolEase** → change detection is whole-record content hashing
  (`models.SpoolRecord.content_hash`, state shadow). Don't look for a timestamp; there isn't
  one. Only `added_time` / `encode_time` exist (set at create).
- **`weight_current` is GROSS, not remaining.** Re-derived every time someone guesses
  otherwise. See §4.
- **Spoolman needs `density` and `diameter` > 0 on every Filament**; SpoolEase stores
  neither. The bridge supplies defaults + the external-material density map.
- **Spoolman `extra` values are JSON-encoded strings** (text `"42"` is stored as `"\"42\""`).
  The bridge encodes with `json.dumps`. Register a field via `POST /field/spool/<key>` before
  setting values, or writes 400.
- **Spoolman auth:** none for a non-browser client (it sends no `Origin`). Don't add an auth
  header; don't send an `Origin`.

## 7. Environment constraints (authoring machine)

- **Windows Server 2019.** Shells: **PowerShell 5.1** (primary) and **Git Bash**. Each has
  its own syntax; PowerShell here is 5.1 (no `&&`/`??`/ternary).
- **No Python, no Docker** installed. You cannot run the bridge or `pytest` here. (A future
  session on a machine that has them should run both.)
- **`cd` in the Bash tool persists** across calls and shifted the working dir mid-session
  earlier (into `SpoolEase/core/data`). **Use absolute paths.**
- **Avoid recursive filesystem scans** (`Get-ChildItem -Recurse` over `WindowsApps`, broad
  `find /`): they time out here. A search for a Python interpreter had to be killed.
- **Git/PR attribution:** this session was told to end commits with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and PRs with the Claude Code
  generated-with line. Re-confirm the current session's own attribution reminder before
  committing — don't assume it carries over.

## 8. Next steps (recommended order)

1. **Run & harden the bridge** (needs Python): `cd bridge && pip install -r requirements.txt
   pytest && pytest`; then a real `DRY_RUN=true` pass against a throwaway Spoolman. Fix
   anything that surfaces (it has never run).
2. **Confirm the §5 assumptions** against real data / a quick Spoolman API spot-check.
3. **Implement the SpoolEase read endpoint** (`spoolease-endpoints.md`, Change 1+3 read) on a
   branch in the SpoolEase repo; build/flash; `curl` it; point the bridge at it
   (`SPOOLEASE_MODE=json`, which it already defaults to).
4. **Phase 2 — two-way write-back** (design in `data-flow.md`): a Spoolman **websocket**
   consumer in the bridge + the SpoolEase **POST** endpoint + **echo suppression** (compare
   to the shadow you just wrote) + **field ownership** (only write Spoolman-owned fields —
   location/price/archived/comment — back to SpoolEase; never weight).
5. **Upstream** the SpoolEase endpoint as a PR ("optional JSON API for external integrations,
   e.g. Spoolman sync," off unless a token is configured).

## 9. How to extend the bridge (quick map)

```
bridge/spoolsync/
  config.py     env → Config (add new knobs here)
  models.py     SpoolRecord + JSON contract + content_hash  (the SpoolEase side's shape)
  mapper.py     PURE flat→normalized logic + weight math     (unit-tested; safest to change)
  state.py      SQLite shadow store (id map + hashes)        (the sync bookkeeping)
  spoolman.py   Spoolman REST client (idempotent upserts)    (add WS consumer here for phase 2)
  spoolease.py  SpoolEase connector (json mode; encrypted stubbed; add write_record for phase 2)
  reconcile.py  the SE→SM pass                               (add SM→SE pass for phase 2)
  sync.py       loop + wiring
```
Keep `mapper.py` pure (no I/O) so `tests/test_mapper.py` stays the fast guardrail.
