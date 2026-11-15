# Phase 2: Two-Way Sync — Local Implementation Design

## Status
⏳ **In progress** — no firmware modifications required

## Goal
Enable two-way sync between SpoolEase and Spoolman using only the external bridge. No modifications to either product.

## Architecture

```
SpoolEase Device                Bridge                 Spoolman Server
     ├─────────────────────────────────────────────────────────┤
     │                                                          │
     │  Poll every 30s                                          │
     │  ──────────────>  Read changes  ──────────────>         │
     │  (weight, consumption,          (vendors, filaments,    │
     │   tag scans)                     spools)                │
     │                                                          │
     │                   Listen websocket  <──────────────     │
     │  <──────────────  (location, comment,                   │
     │  POST update       price, archived)                     │
     │  (location, comment)                                    │
     └──────────────────────────────────────────────────────────┘
```

### Key Decisions
- **No firmware modifications** — use existing SpoolEase endpoints only
- **Websocket-driven** — connect to Spoolman websocket for real-time Spoolman→SpoolEase sync
- **Polling for SpoolEase** — pull all records every 30s for SpoolEase→Spoolman sync
- **Persistent shadow store** — SQLite tracks last-known hashes per side
- **Per-field source of truth** — each field has a designated owner

## Startup Sequence

1. Connect to Spoolman websocket (start listening immediately)
2. Queue incoming events in a thread-safe buffer
3. Poll SpoolEase → populate baseline
4. Poll Spoolman → populate baseline
5. Diff both → populate shadow store (SQLite)
6. Drain queued events → apply any changes that arrived during scan
7. Enter normal operation: process websocket events in real-time

### Why Queue Instead of Wait?
Waiting for the scan to complete means missing Spoolman changes that occur during the scan. Queueing captures everything and replays it cleanly after the baseline is established. The queue holds only event payloads (small, bounded).

## Normal Operation Loop

### Spoolman → SpoolEase (websocket-driven)
```
On websocket event from Spoolman:
    1. Parse event: {type, resource, payload}
    2. Check echo suppression (is this our write?)
    3. If echo → discard
    4. If not echo → map fields → POST to SpoolEase
    5. Update shadow store
```

### SpoolEase → Spoolman (polling, every 30s)
```
On SpoolEase poll:
    1. Pull all records from SpoolEase via GET /api/spools
    2. Diff against shadow store (content hash)
    3. Upsert changed records to Spoolman
    4. Archive vanished spools
    5. Update shadow store
```

## Shadow Store Schema (SQLite)

```sql
CREATE TABLE spool_map (
    spoolease_id TEXT PRIMARY KEY,
    spoolman_id INTEGER,
    se_hash TEXT,      -- last known SpoolEase record hash
    sm_hash TEXT       -- last known Spoolman record hash
);

CREATE TABLE recent_writes (
    spool_id TEXT,
    direction TEXT,    -- 'se_to_sm' or 'sm_to_se'
    hash TEXT,
    timestamp REAL     -- for cleanup of old entries
);
```

## Field Ownership

| Field | Owner | Sync Direction | Notes |
|-------|-------|----------------|-------|
| Physical weight, consumption | SpoolEase | → Spoolman only | Scale reading |
| Tag UID | SpoolEase | → Spoolman only | Physical tag |
| K-factor | SpoolEase | → Spoolman only | Calibration |
| Material, color, diameter | SpoolEase | → Spoolman only | Set at creation |
| Vendor | SpoolEase | → Spoolman only | Enriched by bridge |
| **Location** | **Spoolman** | **← → Both ways** | Prefixed as "Location: X" |
| **Comment** | **Spoolman** | **← → Both ways** | Appended as "Comment: X" |
| Price | Spoolman | ← Spoolman only | Not written back (SE doesn't track) |
| Archived | Spoolman | ← Spoolman only | Not written back (SE has no concept) |

### Write-Back Rules
- Only Spoolman-owned fields are written back to SpoolEase
- Location and comment are combined into SpoolEase's `note` field
- Price and archived are stored in Spoolman only

## DTO Shape for Write-Back

When pushing to SpoolEase, POST the **full record** to `POST /api/spools/add-edit`:

```json
{
  "id": "abc123",
  "vendor": "Brand",
  "material": "PLA",
  "weight_current": 250.0,
  "diameter": 1.75,
  "price": 25.0,
  "location": "Drawer 1",
  "comment": "",
  "archived": false,
  "color": "FF8800",
  "tare": 10.0,
  "consumed": 0.0,
  "k_factor": 0.0,
  "note": "Location: Shelf A | Comment: Test"
}
```

### Write-Back Process
1. Pull the cached SpoolEase record from bridge memory
2. Update only Spoolman-owned fields (location, comment → note)
3. POST the complete DTO with the `id` field set (triggers update, not create)

## Echo Suppression

- Every write to SpoolEase is recorded in `recent_writes`
- Before writing back, check if the Spoolman event matches a recent write
- If it does → it's our own change → suppress
- Old entries in `recent_writes` are cleaned up periodically

## Configuration

```bash
TWO_WAY_SYNC=true
SPOOLEASE_MODE=json
SPOOLEASE_TOKEN=your-token  # if SpoolEase requires auth
```

## Implementation Tasks

1. **Implement `spoolease.write_record()`** — POST full DTO to `/api/spools/add-edit`
2. **Implement websocket listener** in `spoolman.py` — connect, queue events
3. **Implement `reconcile_sm_to_se()`** — process queued events, diff, write back
4. **Startup sequence** — connect WS, scan both sides, populate shadow, drain queue
5. **Echo suppression** — check `recent_writes` before writing
6. **Field ownership** — only write location/comment back

## Comparison with PHASE2-README.md

| Aspect | PHASE2-README.md | This Design |
|--------|------------------|-------------|
| Trigger mechanism | Spoolman websocket (real-time) | Spoolman websocket + SpoolEase polling |
| SpoolEase modification | Requires new endpoint | Uses existing `POST /api/spools/add-edit` |
| Complexity | More complex | Simpler, no firmware changes |
| Latency | < 1 second | < 1 second (SM→SE), up to 30s (SE→SM) |
| SpoolEase ownership | Modifies firmware | No modifications |
| Echo suppression | Shadow table | Shadow table + recent_writes |
| Startup | Assumes endpoint exists | Full scan + queue drain |

## Limitations

1. **Location + Comment combined** — SpoolEase has one `note` field
2. **Price not written back** — SpoolEase doesn't track price
3. **Archived not written back** — SpoolEase doesn't have archive concept
4. **No conflict resolution UI** — last-writer-wins with field ownership

## Monitoring

### Logs
- **Successful write-back:** `INFO writing Spoolman changes back to SpoolEase {id}: {fields}`
- **Echo suppressed:** `DEBUG suppressing echo: spoolman spool_id={id} (our recent write)`
- **Websocket connected:** `INFO websocket connected, listening for Spoolman changes...`
- **SE→SM reconcile:** `INFO SE→SM reconcile: created={n}, updated={n}, archived={n}`

### Health Checks
- **Phase 1 working:** Log shows `SE→SM reconcile: created=0, updated=X`
- **Phase 2 working:** Log shows `websocket connected, listening...`
- **Write-backs working:** Search logs for `writing back to SpoolEase`
