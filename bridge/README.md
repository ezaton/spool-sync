# spoolsync — SpoolEase → Spoolman bridge

A small standalone service that mirrors SpoolEase spools into Spoolman. **Neither product's
core is modified.** The only (recommended) product change is a small JSON endpoint on
SpoolEase — see [`../spoolease-endpoints.md`](../spoolease-endpoints.md). Spoolman is used
purely through its public REST API.

> **Status: phase 1 (one-way, SpoolEase → Spoolman).** This is the foundation for two-way
> sync — the id map + shadow store it builds is exactly what the Spoolman → SpoolEase
> write-back (phase 2) needs. See [`../data-flow.md`](../data-flow.md).
>
> ⚠️ **Not yet executed.** This machine has no Python/Docker, so the code has not been run
> here. Treat it as a reviewed draft; run the tests (below) and a `DRY_RUN=true` pass before
> trusting it against a real Spoolman.

## What it does each cycle

1. Fetch all SpoolEase records (`GET /api/spools.json`).
2. Diff each against a local **shadow** (content hash) — SpoolEase has no `updated_at`, so
   change detection is by hash.
3. For anything new/changed, upsert **Vendor → Filament → Spool** into Spoolman (idempotent,
   keyed by `external_id` / RFID tag / `extra.spoolease_id`), computing net-remaining weight
   the same way SpoolEase does.
4. Archive (not delete) spools that vanished from SpoolEase.

## Layout

```
spoolsync/
  config.py     env-driven Config
  models.py     SpoolRecord + JSON contract + content hash
  mapper.py     pure flat→normalized mapping + weight logic  (unit-tested)
  state.py      sqlite shadow store (id map + hashes)  = the sync bookkeeping
  spoolman.py   Spoolman REST client (idempotent upserts)
  spoolease.py  SpoolEase source connector (json mode; encrypted mode stubbed)
  reconcile.py  the one-way reconciliation pass
  sync.py       loop + wiring
  __main__.py   entrypoint
tests/
  test_mapper.py   deterministic tests for the risky logic
```

## Run it

### Option 1: Docker (Recommended)

**Using published image from Docker Hub:**

```bash
cd sync-tool/bridge
cp docker-compose.yml docker-compose.local.yml
# Edit docker-compose.local.yml with your settings
docker-compose -f docker-compose.local.yml up -d
```

**Or with Spoolman's existing docker-compose:**

```yaml
services:
  spoolsync:
    image: etzion/spoolsync:latest
    environment:
      SPOOLMAN_URL: http://<spoolman_address>/api/v1
      SPOOLEASE_URL: http://<spoolease_address>
      SPOOLEASE_MODE: encrypted
      SPOOLEASE_SECURITY_KEY: your-key
    volumes:
      - spoolsync-state:/state
```

### Option 2: Local Python

```bash
cd sync-tool/bridge
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # edit SPOOLMAN_URL / SPOOLEASE_URL / SPOOLEASE_SECURITY_KEY
set -a; . ./.env; set +a        # export the vars (bash)
python -m spoolsync
```

### Option 3: Build Docker locally

```bash
cd sync-tool/bridge
docker build -t spoolsync .
docker run -d \
  -e SPOOLMAN_URL=http://<spoolman_address>/api/v1 \
  -e SPOOLEASE_URL=http://<spoolease_address> \
  -e SPOOLEASE_MODE=encrypted \
  -e SPOOLEASE_SECURITY_KEY=your-key \
  -v spoolsync-state:/state \
  spoolsync
```

**Always do a dry run first** (`DRY_RUN=true`): it logs every intended Spoolman write
without performing any, so you can eyeball the mapping against your data.

## Test

```bash
cd sync-tool/bridge
pip install pytest
pytest            # exercises weight math + identity/dedup, no network needed
```

## Configuration

See [`.env.example`](.env.example). Key ones:

| Var | Meaning |
|---|---|
| `SPOOLMAN_URL` | Spoolman REST base, incl. `/api/v1` |
| `SPOOLEASE_URL` | SpoolEase console base URL |
| `SPOOLEASE_MODE` | `json` (recommended) or `encrypted` (not implemented) |
| `SPOOLEASE_TOKEN` | bearer token the JSON endpoint checks |
| `DEFAULT_DIAMETER` / `DEFAULT_DENSITY` | fills Spoolman's required Filament fields SpoolEase doesn't store |
| `SYNC_INTERVAL_SECONDS` | poll cadence |
| `DRY_RUN` | log intended writes, perform none |
| `ARCHIVE_MISSING` | archive Spoolman spools when their SpoolEase record disappears |
| `STATE_PATH` | sqlite file for the shadow store (mount a volume!) |

## Known limitations (phase 1)

- **One-way only.** No Spoolman → SpoolEase write-back yet (that's phase 2; needs the
  SpoolEase write endpoint + echo suppression — design already in `../data-flow.md`).
- **`SPOOLEASE_MODE=json` requires the SpoolEase endpoint changeset.** `encrypted` mode is
  stubbed on purpose.
- **Material→density** comes from Spoolman's `/external/material` with a flat fallback;
  verify densities for exotic materials.
- Calibration (K) is not mirrored in phase 1 (see note in `mapper.map_spool`).
