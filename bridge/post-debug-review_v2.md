# Post-Debug Design Review (v2)

> **Reviewer:** Claude Opus 4.6 (via Claude Code)
> **Date:** 2026-11-21
> **Document:** post-debug-design_v1.md (v2)
> **Verdict:** One critical issue found and fixed.

---

## Critical Issue Found and Fixed

### Broken import path

`from .migrate_variant import migrate_filaments` in both `sync.py` and
`sync_twoway.py` would fail because `migrate_variant.py` lived at the bridge
root, not inside the `spoolsync/` package. Setting `MIGRATION=true` would
crash at startup.

**Fix:** Moved `migrate_variant.py` into `spoolsync/` package. Updated
`Dockerfile` to remove the standalone `COPY migrate_variant.py .` line (the
file is now part of the `spoolsync` package copied via `COPY spoolsync ./spoolsync`).

**Verification:** Import tested and confirmed working:
```
from spoolsync.migrate_variant import migrate_filaments  ✓
```

---

## Minor Issues

### 1. Docker Compose gap
The `MIGRATION` env var isn't documented in `docker-compose.yml` per the design.
This is a deployment concern — the compose file is on the `block` machine and
needs `MIGRATION: "true"` added for first deployment.

### 2. `print()` vs `logging`
Migration output uses `print()` which bypasses the bridge's log formatter.
This is acceptable for a one-time migration — the structured log messages
(`log.info`, `log.warning`) are properly formatted, and the per-filament
`print()` output is human-readable progress reporting.

---

## What's Correct

| Aspect | Assessment |
|--------|------------|
| URL `removesuffix` handling | Correct — strips `/api/v1` prefix |
| Migration placement in lifecycle | Correct — before websocket startup |
| Value encoding | Consistent between migration and bridge (`json.dumps`) |
| Config field wiring | Properly wired in dataclass + `from_env()` |
| Import path after fix | Correct (`from .migrate_variant import ...`) |
| Dockerfile | Updated — no standalone copy needed |

---

## Summary

**Verdict: Clean after fix.** The critical import path issue has been resolved.
All other aspects are correct. The code is ready for deployment with
`MIGRATION: "true"` in docker-compose.yaml.
