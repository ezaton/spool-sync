# SpoolEase ↔ Spoolman Sync

Design documentation for keeping filament **type/details** and **weight** data in sync
between **SpoolEase** (ESP32-S3 firmware, flat CSV DB on SD card) and **Spoolman**
(Python/FastAPI inventory service, normalized relational DB).

Goal: **two-way sync** with the least intrusion into either product.

## Chosen approach

**External bridge** (a standalone Python service next to Spoolman) + a small, optional JSON
endpoint on SpoolEase. Spoolman is unchanged. This keeps all mapping/conflict/bookkeeping in
the one component that's easy to iterate, matches the preferred topology, and is the least
total change. See [decision.md](decision.md) for why a product modification *alone* can't
deliver robust two-way sync.

## Documents

| File | Contents |
|---|---|
| [decision.md](decision.md) | **External tool vs. in-product modification** — analysis, decision matrix, recommendation |
| [architecture.md](architecture.md) | Components, deployment topology, the bridge's internals, interface options on each side |
| [data-flow.md](data-flow.md) | The sync loops (both directions), change detection, identity keys, conflict resolution, echo suppression |
| [data-mapping.md](data-mapping.md) | Field-by-field mapping, source-of-truth per field, the flat↔normalized granularity problem, resolved weight semantics |
| [spoolease-endpoints.md](spoolease-endpoints.md) | **The SpoolEase changeset** — the small JSON read/write endpoint the bridge uses (annotated Rust sketch) |

## Code

| Path | Contents |
|---|---|
| [bridge/](bridge/) | **`spoolsync`** — the bridge, two-way sync. Runnable Python; see [bridge/README.md](bridge/README.md) |

## Source of truth (summary)

Ownership is **per-field**, not global:

| Data | Owner | Rationale |
|---|---|---|
| Spool existence, physical weight, consumption, RFID tag binding, K/pressure-advance calibration | **SpoolEase** | It has the scale, AMS/print telemetry, and the NFC reader |
| Filament/Vendor catalog library, density/diameter | **Spoolman** | Richer schema + the external SpoolmanDB mirror |
| Inventory presentation: `location`, `price`, `archived`, comments, reporting | **Spoolman** | It's the ecosystem hub (Moonraker/Klipper/etc.) |

Cross-system join key: **the RFID tag UID** (both sides have it), with the SpoolEase
record `id` as a fallback stored in a Spoolman `extra` field.

## Quick Start

### Using Docker (Recommended)

```bash
docker run -d \
  --name spoolsync \
  -e SPOOLMAN_URL=http://spoolman_address/api/v1 \
  -e SPOOLEASE_URL=http://spoolease_address \
  -e SPOOLEASE_MODE=encrypted \
  -e SPOOLEASE_SECURITY_KEY=your-6-char-key \
  -e TWO_WAY_SYNC=true \
  -v spoolsync-state:/state \
  etzion/spoolsync:latest
```

**Docker Hub Setup**: See [DOCKER-SETUP.md](DOCKER-SETUP.md) for complete instructions on:
- Setting up GitHub Actions for automated builds
- Publishing to Docker Hub
- Versioning and tagging strategy

### Using Python Directly

```bash
cd bridge
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit with your settings
python -m spoolsync
```

See [bridge/README.md](bridge/README.md) for detailed configuration.

## Status

- ✅ **Design complete** — all architecture, data-flow, and mapping docs finalized.
- ✅ **Weight semantics resolved** from the firmware (`weight_current` = gross scale
  reading; see [data-mapping.md](data-mapping.md#weight-semantics-resolved)).
- ✅ **Phase 1 complete**: One-way sync SpoolEase → Spoolman
  - Encrypted mode implemented (AES-GCM decryption)
  - Idempotent mapping with shadow state tracking
  - Docker Hub automated builds configured
- ✅ **Phase 2 complete**: Two-way sync with real-time write-back
  - Websocket listener for real-time Spoolman changes
  - Echo suppression to prevent sync loops
  - Field ownership (only writes location/comment back)
  - Startup sequence with baseline scan and event queue drain
  - Temperature mapping (98 base filaments, 36 materials)
  - Comprehensive test suite (68 tests)
- ✅ **Verbose logging** — `VERBOSE=yes` for per-record audit trail
- ✅ **Production deployed** on `block` machine — 179 spools synced, both directions working

### What's running in production

- **Bridge**: `etzion/spoolsync:latest` on `block` (via docker-compose)
- **Sync direction**: Two-way (SpoolEase ↔ Spoolman)
- **SpoolEase mode**: encrypted (AES-256-GCM, 7-char device key)
- **Poll interval**: 30 seconds
- **Spoolman websocket**: real-time event listener for write-backs
- **Shadow store**: SQLite at `./spoolsync-state/spoolsync.sqlite`

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SPOOLMAN_URL` | *(required)* | Spoolman API base URL |
| `SPOOLEASE_URL` | *(required)* | SpoolEase base URL |
| `SPOOLEASE_MODE` | `json` | `json` or `encrypted` |
| `SPOOLEASE_TOKEN` | — | Bearer token for JSON mode |
| `SPOOLEASE_SECURITY_KEY` | — | 7-char key for encrypted mode |
| `TWO_WAY_SYNC` | `false` | Enable Spoolman → SpoolEase write-back |
| `DEFAULT_DIAMETER` | `1.75` | Fallback filament diameter (mm) |
| `DEFAULT_DENSITY` | `1.24` | Fallback density (g/cm³) |
| `DEFAULT_EXTRUDER_TEMP` | `215` | Fallback extruder temp (°C) |
| `SYNC_INTERVAL_SECONDS` | `30` | Poll interval |
| `DRY_RUN` | `false` | Log writes without executing |
| `ARCHIVE_MISSING` | `true` | Archive spools removed from SpoolEase |
| `VERBOSE` | `false` | Enable per-record DEBUG-level audit logging |
| `LOG_LEVEL` | auto | Python log level; auto-sets to `DEBUG` when `VERBOSE=yes` |
| `STATE_PATH` | `/state/spoolsync.sqlite` | Shadow store SQLite path |

### Example docker-compose

```yaml
services:
  spoolsync:
    image: etzion/spoolsync:latest
    environment:
      SPOOLMAN_URL: http://spoolman:8000/api/v1
      SPOOLEASE_URL: http://192.168.0.120
      SPOOLEASE_MODE: encrypted
      SPOOLEASE_SECURITY_KEY: your-key
      TWO_WAY_SYNC: "true"
      VERBOSE: "yes"  # enable per-record audit logging
    volumes:
      - ./spoolsync-state:/state
```
