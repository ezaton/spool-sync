# Post-Debug Verification v3 — Final State

**Date:** 2026-11-21
**Scope:** Verify all three previously-identified issues are correctly fixed.
**Commits reviewed:** `63aa4ce` (fix: httpx.Client context manager, .env.example MIGRATION entry), `3128563` (feat: embed variant migration in bridge startup sequence)

---

## Issue 1: httpx.Client not closed in migrate_variant.py

**Status: FIXED**

`spoolsync/migrate_variant.py:138-142` — `httpx.Client` is now used as a context
manager (`with ... as http:`). The entire `migrate_filaments()` body is indented
inside the `with` block, so the client is guaranteed to be closed on both normal
exit and exceptions.

Before (commit `3128563`):
```python
http = httpx.Client(...)
# body not inside a with — client never closed on exception
```

After (commit `63aa4ce`):
```python
with httpx.Client(
    base_url=base,
    timeout=30.0,
    headers={"Content-Type": "application/json"},
) as http:
    # entire body inside with block
    ...
    return migrated, skipped, errors
```

**Verified correct.** The `return` at line 192 is inside the `with` block; Python
closes the client before returning.

---

## Issue 2: .env.example missing MIGRATION entry

**Status: FIXED**

`.env.example:31-34` now documents the `MIGRATION` env var:
```
# --- One-time variant migration ---
# Set to "true" to run a one-time migration of filament variant data at startup.
# After the migration completes, set back to "false" or remove the variable.
MIGRATION=false
```

Supporting code verified:
- `spoolsync/config.py:103` — `migration=_get_bool("MIGRATION", False)` reads the env var
- `spoolsync/config.py:67` — `migration: bool` is a typed field on Config

**Verified correct.** Default is `false` (safe), documentation is clear about one-time usage.

---

## Issue 3: Design doc references old path

**Status: VERIFIED (was already correct)**

The Dockerfile at `bridge/Dockerfile` no longer contains `COPY migrate_variant.py .` — that
line was removed in commit `63aa4ce`. The file `bridge/migrate_variant.py` (root-level) no
longer exists; only `bridge/spoolsync/migrate_variant.py` remains.

The Dockerfile copies the entire `spoolsync` package:
```dockerfile
COPY spoolsync ./spoolsync
```

This implicitly includes `spoolsync/migrate_variant.py`. No additional COPY needed.

**Verified correct.**

---

## Migration Startup Embedding — Cross-Verification

Both sync entry points correctly embed the migration:

### sync.py (Phase 1 — polling only)
`spoolsync/sync.py:47-58`:
```python
if cfg.migration:
    base_url = cfg.spoolman_url.removesuffix("/api/v1")
    log.info("running variant migration (pre-startup)")
    from .migrate_variant import migrate_filaments
    migrated, skipped, errors = migrate_filaments(
        base_url=base_url, dry_run=cfg.dry_run,
    )
```
- Runs after `ensure_variant_field()` (line 42) — correct ordering
- Uses `cfg.dry_run` — respects dry-run mode
- Strips `/api/v1` from config URL since `migrate_filaments()` adds it internally
- Logs errors as warning but continues startup — resilient

### sync_twoway.py (Phase 2 — websocket)
`spoolsync/sync_twoway.py:76-87`:
```python
if cfg.migration:
    base_url = cfg.spoolman_url.removesuffix("/api/v1")
    log.info("running variant migration (pre-startup, websocket inactive)")
    from .migrate_variant import migrate_filaments
    migrated, skipped, errors = migrate_filaments(
        base_url=base_url, dry_run=cfg.dry_run,
    )
```
- Runs before websocket connection (`_startup_sequence` at line 90) — correct timing
- Same URL stripping and dry-run handling as sync.py — consistent

### JSON encoding consistency
Both `mapper.py:298` and `migrate_variant.py:175` use `json.dumps(variant)` to encode the
variant value in `extra`. This produces a JSON-quoted string (e.g., `'"Matte"'`), matching
Spoolman's extra-field contract. Consistent across both code paths.

---

## Remaining Issues (non-blocking, documentation only)

### README.md references stale standalone migration command
`bridge/README.md:106-111` still documents running the migration as a standalone Docker command:
```bash
docker compose ... run --rm spoolsync python3 migrate_variant.py --base-url http://spoolman:8000
```

Now that migration is embedded in the startup sequence (`MIGRATION=true` env var), this
documentation is stale. The script still has a `__main__` guard and can be invoked standalone,
so the command is not *wrong*, but it no longer reflects the recommended workflow. The README
should mention `MIGRATION=true` as the primary migration method.

### DESIGN_VARIANT_SYNC.md has pre-embedding references
`bridge/DESIGN_VARIANT_SYNC.md` still describes the migration as a standalone script with
Docker commands (lines 503, 512, 529, 555, 613-614) and references a `COPY migrate_variant.py .`
Dockerfile line (line 555) that no longer exists. This design doc predates the startup-embedding
change and hasn't been updated.

Both are **documentation-only** issues — they don't affect runtime behavior.

---

## Summary

| # | Issue | Status | Verified At |
|---|-------|--------|-------------|
| 1 | httpx.Client not closed | **FIXED** | `migrate_variant.py:138-192` |
| 2 | .env.example missing MIGRATION | **FIXED** | `.env.example:31-34`, `config.py:67,103` |
| 3 | Design doc references old path | **ALREADY CORRECT** | `Dockerfile` (no stale COPY), package import works |

**All three reported issues are verified fixed.** Two non-blocking documentation items noted
(README and DESIGN_VARIANT_SYNC.md still reference standalone migration usage).
