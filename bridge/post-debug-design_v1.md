# Post-Debug Design: Migration Startup Integration + URL Fix

## Date: 2026-11-21 (v2)

## 1. Verified Findings

### 1.1. Spoolman PATCH behavior (empirically verified)

Spoolman's PATCH endpoint **merges** `extra` for registered fields:

```bash
# Filament has: extra = {"variant": "\"PLA+\""}
PATCH /api/v1/filament/162 {"extra": {"variant": "\"Merged\""}}
# Result: extra = {"variant": "\"Merged\""}  ✓ merged

# With two registered fields:
# Filament has: extra = {"variant": "\"PLA+\"", "test_field": "\"val\""}
PATCH /api/v1/filament/162 {"extra": {"variant": "\"Merged\""}}
# Result: extra = {"variant": "\"Merged\"", "test_field": "\"val\""}  ✓ merged
```

**Unregistered fields are rejected** — only keys that exist in Spoolman's field registry can appear in `extra`.

**Implication:** Neither the migration nor the bridge has the "extra replacement" bug. The migration's `{"extra": {"variant": ...}}` is safe because:
- `variant` is a registered field
- Spoolman merges it into existing `extra`
- No other registered fields exist on filaments currently

### 1.2. Bridge behavior (corrected)

The bridge's `upsert_filament()` builds `extra` from scratch in `map_filament()`:
```python
extra: dict[str, str] = {}
if variant is not None:
    extra["variant"] = _json_str(variant)
```

It does **not** read the existing filament's `extra` and merge. However, this is **not a bug** because Spoolman's PATCH endpoint merges on the server side. The bridge's approach is correct — it sends the desired `extra` state, and Spoolman merges it.

### 1.3. URL double `/api/v1` bug (confirmed)

The `SPOOLMAN_URL` env var includes `/api/v1`:
```
SPOOLMAN_URL: http://spoolman:8000/api/v1
```

The bridge's `SpoolmanClient` uses this as `_base` and appends paths **without** `/api/v1`:
```python
self._base = "http://spoolman:8000/api/v1"
self._http.get("/filament")  # → http://spoolman:8000/api/v1/filament ✓
```

The migration uses paths **with** `/api/v1`:
```python
http.get("/api/v1/filament")  # → needs base without /api/v1
```

If we pass `cfg.spoolman_url` (`http://spoolman:8000/api/v1`) to the migration, it will produce:
```
http://spoolman:8000/api/v1/api/v1/filament  → 404 ✗
```

## 2. Solution Design

### 2.1. Fix: URL handling in migration integration

The migration's `migrate_filaments()` function uses paths with `/api/v1` prefix. The bridge's `cfg.spoolman_url` also includes `/api/v1`. We need to strip the prefix before passing to the migration:

```python
# In sync_twoway.py and sync.py:
base_url = cfg.spoolman_url.removesuffix("/api/v1")
migrated, skipped, errors = migrate_filaments(base_url, dry_run=cfg.dry_run)
```

### 2.2. Config change: Add `migration` field

Add `migration: bool` to the `Config` dataclass:

```python
# In Config dataclass:
migration: bool  # Run one-time variant migration at startup

# In Config.from_env():
migration=_get_bool("MIGRATION", False),  # Run one-time migration at startup
```

### 2.3. `migrate_variant.py`: Move into package and export callable function

`migrate_variant.py` was moved from the bridge root into the `spoolsync/` package
so that `from .migrate_variant import migrate_filaments` works correctly in
`sync.py` and `sync_twoway.py`. The `migrate_filaments()` function is already
the public API — no wrapper needed. The `Dockerfile` no longer needs a standalone
`COPY migrate_variant.py .` since the file is now part of the `spoolsync` package.

### 2.4. Integration in `sync_twoway.py`

Add the migration step after field initialization and before the startup sequence. The websocket listener starts **during** the startup sequence (step 1), so the listener is **not active** during migration:

```python
# Initialize
sm.ensure_spoolease_id_field()
sm.ensure_variant_field()
density_map = sm.external_material_densities()

# --- Migration phase (optional, before websocket listener) ---
if cfg.migration:
    base_url = cfg.spoolman_url.removesuffix("/api/v1")
    log.info("running variant migration (pre-startup, websocket inactive)")
    from .migrate_variant import migrate_filaments
    migrated, skipped, errors = migrate_filaments(
        base_url=base_url,
        dry_run=cfg.dry_run,
    )
    log.info("variant migration complete: migrated=%d, skipped=%d, errors=%d",
             migrated, skipped, errors)
    if errors > 0:
        log.warning("migration had %d errors — continuing anyway", errors)

# --- Startup sequence (websocket listener starts here) ---
await _startup_sequence(se, sm, state, cfg, density_map, stop)
```

### 2.5. Integration in `sync.py` (one-way mode)

Same pattern for one-way sync:

```python
# --- Migration phase (optional) ---
if cfg.migration:
    base_url = cfg.spoolman_url.removesuffix("/api/v1")
    log.info("running variant migration (pre-startup)")
    from .migrate_variant import migrate_filaments
    migrated, skipped, errors = migrate_filaments(
        base_url=base_url,
        dry_run=cfg.dry_run,
    )
    log.info("variant migration complete: migrated=%d, skipped=%d, errors=%d",
             migrated, skipped, errors)

# --- Main sync loop ---
run_sync_loop(...)
```

### 2.6. Docker Compose change

Add `MIGRATION: "true"` to the spoolsync service env vars for the first deployment:

```yaml
spoolsync:
  environment:
    MIGRATION: "true"   # Remove after first successful run
    # ... other vars ...
```

## 3. Websocket Listener Timing

The migration runs **before** the websocket listener starts. This is acceptable because:

1. The migration only **writes** to Spoolman (PATCH filament records)
2. Spoolman's PATCH does not generate websocket events for `extra` field changes (it generates events for filament updates, but the listener isn't active yet)
3. Even if events were generated, they would be lost during migration — but this is a one-time operation and no SpoolEase writes happen during migration
4. After migration, the startup sequence starts the websocket listener, runs baseline scan, and drains any queued events

**Timeline:**
```
T0: Bridge starts
T1: Fields ensured, densities loaded
T2: Migration runs (writes extra.variant to filaments)
T3: Startup sequence begins
T4: Websocket listener connects (starts receiving events)
T5: Baseline scan populates shadow store
T6: Queued events drained
T7: Normal operation
```

## 4. Implementation Plan

### 4.1. Files to change

| File | Change |
|------|--------|
| `spoolsync/config.py` | Add `migration: bool` field + env var parsing |
| `migrate_variant.py` | Moved into `spoolsync/` package; keep `migrate_filaments()` as public API |
| `spoolsync/sync_twoway.py` | Add migration step between field init and startup sequence |
| `spoolsync/sync.py` | Add migration step between field init and main loop |
| `Dockerfile` | Remove `COPY migrate_variant.py .` (now inside spoolsync package) |
| `docker-compose.yaml` | Add `MIGRATION: "true"` (temporary) |

### 4.2. No changes needed to bridge's `upsert_filament()`

The bridge's approach of building `extra` from scratch is correct because Spoolman's PATCH endpoint merges registered fields. The "extra replacement" concern from the review was based on an incorrect assumption about Spoolman's behavior.

## 5. Testing

### 5.1. Integration test

Test the full startup sequence with `MIGRATION=true`:

1. Set `MIGRATION=true` in docker-compose
2. Start the bridge
3. Verify logs show: "running variant migration", "variant migration complete"
4. Verify filaments have `extra.variant` set correctly
5. Verify websocket listener starts after migration
6. Restart bridge (without `MIGRATION=true`)
7. Verify no migration runs (all filaments skipped)

### 5.2. URL correctness test

Verify the migration connects to the correct URL:

```python
# In the integration code:
base_url = cfg.spoolman_url.removesuffix("/api/v1")
# cfg.spoolman_url = "http://spoolman:8000/api/v1"
# base_url = "http://spoolman:8000"
# migrate_filaments(base_url) → http.get("/api/v1/filament")
# → http://spoolman:8000/api/v1/filament  ✓
```

## 6. Rollback Plan

If the migration causes issues:

1. Set `MIGRATION=false` (or remove the env var)
2. Restart the bridge
3. The migration will not run on subsequent startups
4. The bridge will continue normal operation
5. Manual migration can be re-run later via `docker compose run` if needed

## 7. Summary

| Aspect | Status |
|--------|--------|
| Spoolman PATCH merges `extra` | Verified empirically — no merge bug |
| Bridge builds `extra` from scratch | Correct — Spoolman merges on server side |
| URL double `/api/v1` | **Real bug** — fix with `removesuffix("/api/v1")` |
| Migration as startup phase | Good improvement over standalone script |
| Control mechanism | `MIGRATION=true` env var |
| Websocket during migration | **Not active** (consistent with code) |
| Files to change | 5 files |
| Re-run needed for current data | No (already migrated) |
