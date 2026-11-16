# SpoolEase ↔ Spoolman Sync

Two-way sync bridge keeping filament **type/details** and **weight** data synchronized
between **SpoolEase** (ESP32-S3 firmware, flat CSV DB on SD card) and **Spoolman**
(Python/FastAPI inventory service, normalized relational DB).

## Documents

| Document | Contents |
|---|---|
| [DESIGN.md](doc/DESIGN.md) | Architecture, data flow, data mapping, decision rationale, SpoolEase endpoint design, Spoolman/SpoolEase API references |
| [FULL_DETAILS.md](doc/FULL_DETAILS.md) | Code-level reference: every module, function, data flow, gotchas |
| [docker/docker-compose.yml](docker/docker-compose.yml) | Single Docker Compose file — encrypted, JSON, and two-way sync modes |
| [doc/DOCKER-COMPOSE-GUIDE.md](doc/DOCKER-COMPOSE-GUIDE.md) | Full Docker deployment guide |
| [doc/QUICK-START-DOCKER.md](doc/QUICK-START-DOCKER.md) | Quick start with 3 options |
| [doc/GITHUB-ACTIONS-SUMMARY.md](doc/GITHUB-ACTIONS-SUMMARY.md) | GitHub Actions + Docker Hub publishing |

## Quick Start

### Using Docker (Recommended)

```bash
# Copy and configure
cp docker/docker-compose.yml docker-compose.yml
# Edit SPOOLEASE_URL and SPOOLEASE_SECURITY_KEY
docker compose up -d
```

For a quick standalone run:
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

**Docker deployment guide**: See [doc/DOCKER-COMPOSE-GUIDE.md](doc/DOCKER-COMPOSE-GUIDE.md)
**Quick start**: See [doc/QUICK-START-DOCKER.md](doc/QUICK-START-DOCKER.md)

### Using Python Directly

```bash
cd bridge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit with your settings
python3 -m spoolsync
```

## Status

- ✅ **Design complete** — all architecture, data-flow, and mapping docs finalized.
- ✅ **Weight semantics resolved** from the firmware (`weight_current` = gross scale
  reading).
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
