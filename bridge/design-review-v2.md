# Design Review: DESIGN_VARIANT_SYNC.md v3 (Review v2)

> **Reviewer:** Claude Opus 4.6
> **Date:** 2026-11-20
> **Status:** Review of v3 draft (post design-review-v1 feedback)
> **Verdict:** v1 critical issues are resolved; new issues found in rollout plan and migration script

---

## Summary of v1 Findings

| Finding | Status in v3 |
|---------|-------------|
| Content hash won't trigger re-sync (critical) | **Fixed** — Section 4.1 correctly states migration is required |
| Missing `sync_twoway.py` | **Fixed** — Added to Section 2.2 and Section 3.5 |
| Incorrect filament name examples | **Fixed** — Now uses "Matte Black" throughout |
| Shell artifacts at EOF | **Fixed** — Clean end of file |
| Missing `field` import | **Fixed** — Section 3.1 shows import change |
| Section layout for `ensure_variant_field()` | **Fixed** — Separate sections 3.3/3.4/3.5 |
| Migration script framing | **Fixed** — Clearly marked as REQUIRED |

All v1 issues have been addressed. The remainder of this review covers new
issues found in the updated design.

---

## Bug: Double `/api/v1` in Migration Script Docker Commands

**Location:** Section 7, Steps 2a/2b/2c/2d

### Problem

The docker commands pass `--base-url http://spoolman:8000/api/v1`:

```bash
docker compose ... run --rm ... bridge python3 migrate_variant.py \
    --base-url http://spoolman:8000/api/v1
```

But the migration script (`migrate_variant.py:127`) hardcodes `/api/v1/` in
its request paths:

```python
resp = http.get("/api/v1/filament")          # line 127
resp = http.get(f"/api/v1/filament/{fid}")   # line 123
resp = http.patch(f"/api/v1/filament/{fid}") # line 162
```

With `base_url = "http://spoolman:8000/api/v1"`, the actual requests would go
to `http://spoolman:8000/api/v1/api/v1/filament` — a doubled path prefix
that will return 404.

Note the inconsistency with `SpoolmanClient`, which expects a base URL
**with** `/api/v1` (e.g., `http://spoolman:8000/api/v1`) because its paths
are bare (`/filament`, `/vendor`, etc.). The migration script takes the
opposite convention.

### Suggested Fix

Change the docker commands to pass the bare host URL (matching the script's
default of `http://block:7912`):

```bash
docker compose -f docker/docker-compose.yml run \
  --rm \
  spoolsync python3 migrate_variant.py --base-url http://spoolman:8000
```

Alternatively, refactor the migration script to use the same URL convention as
`SpoolmanClient` (base includes `/api/v1`, paths are bare). This would be
more consistent but is a larger change.

---

## Bug: Wrong Docker Service Name in Migration Commands

**Location:** Section 7, Steps 2a/2b/2d

### Problem

The docker commands reference a service named `bridge`:

```bash
docker compose -f docker/docker-compose.yml run --rm ... bridge python3 migrate_variant.py
```

But the docker-compose.yml (`docker/docker-compose.yml:46`) defines the
service as `spoolsync`, not `bridge`:

```yaml
services:
  spoolsync:
    image: etzion/spoolsync:latest
    container_name: spoolsync
```

These commands will fail with a "no such service" error.

### Suggested Fix

Replace `bridge` with `spoolsync` in all docker compose run commands:

```bash
docker compose -f docker/docker-compose.yml run \
  --rm \
  spoolsync python3 migrate_variant.py --base-url http://spoolman:8000
```

---

## Minor: Unused `-e SPOOLMAN_URL` in Migration Commands

**Location:** Section 7, Steps 2a/2b

### Problem

The docker commands set `-e SPOOLMAN_URL=http://spoolman:8000/api/v1`, but the
migration script does not read `SPOOLMAN_URL` — it only uses the `--base-url`
CLI argument. The env var serves no purpose and adds confusion (it also has
the wrong URL format per the double-prefix issue above).

### Suggested Fix

Remove the `-e SPOOLMAN_URL=...` flag from the migration commands. The
`--base-url` argument is sufficient.

---

## Minor: Migration Script Substring Matching Can False-Positive

**Location:** Section 7 (migration script), `migrate_variant.py:93-97`

### Problem

`parse_variant_from_name()` matches variant keywords as case-insensitive
substrings:

```python
name_upper = name.upper()
for variant in VARIANT_KEYWORDS:
    if variant.upper() in name_upper:
        return variant
```

Short keywords like `"HF"`, `"CF"`, `"GF"`, `"FR"` could theoretically match
inside longer words. More concretely, `"Metal"` would match inside a color
name like `"Metallic"` — a filament with no subtype and `color_name="Metallic
Blue"` would produce name `"Metallic Blue"` and incorrectly get
`variant="Metal"`.

Since `map_filament_name()` concatenates `material_subtype + color_name`, a
filament with no subtype has a name equal to just its color name. Any color
name containing a variant keyword becomes a false positive.

### Suggested Fix

Use word-boundary matching instead of bare substring:

```python
import re

def parse_variant_from_name(name: str) -> str | None:
    if not name:
        return None
    for variant in VARIANT_KEYWORDS:
        if re.search(r'\b' + re.escape(variant) + r'\b', name, re.IGNORECASE):
            return variant
    return None
```

This prevents "Metal" from matching "Metallic" while still matching
"Metal Red" or "PLA Metal".

For the short abbreviations ("HF", "CF", "GF", "FR"), word-boundary matching
is already correct since these appear as standalone tokens in filament names
(e.g., "PETG HF", "PA6-CF" where `-` is a boundary).

---

## Minor: Backward Compatibility Row Is Misleading

**Location:** Section 4.5 (Backward Compatibility table)

### Problem

The table states:

> Existing filaments with variant set manually | Updated on next reconcile if
> SpoolEase record has subtype

This could be read as "the next reconcile pass will update them," which
contradicts Section 4.1's correct statement that the reconcile loop will NOT
backfill unchanged records. The manual variant is only overwritten if the
SpoolEase record changes for some *other* reason (e.g., weight update),
causing a hash mismatch.

### Suggested Fix

Clarify the condition:

> Existing filaments with variant set manually | Overwritten when the
> SpoolEase record next changes (hash mismatch triggers re-sync)

---

## Observation: Live System Table May Not Reflect Current Code

**Location:** Section 1 (Verified on Live System table)

### Note

The table header says "Verified on Live System" and shows
`Filament Name = "Matte Black"` for spool 42. The v1 design showed
`"Matte PLA Black"` for the same spool — presumably the actual live value at
the time.

If the live system genuinely has `"Matte PLA Black"` (set by an older mapper
or manually), the table should note this:

> **Note:** Live filament names may differ from current `map_filament_name()`
> output if they were created by an earlier version of the bridge or set
> manually.

This is cosmetic and doesn't affect the technical design.

---

## What the Updated Design Gets Right

- **Backfill strategy (Section 4.1-4.2):** Correctly identifies the content
  hash limitation and documents the migration script as required.
- **Section structure:** Clean separation of `spoolman.py` method definition
  (3.3) from call sites in `sync.py` (3.4) and `sync_twoway.py` (3.5).
- **Import change documented** (Section 3.1): Explicitly shows the
  `dataclass, field` import.
- **Container architecture note** (Section 7): Good explanation of the
  one-shot container pattern for the migration script.
- **Risk assessment** (Section 4.6): Honest about the "Certain / High" risk
  of reconcile loop not backfilling — refreshing compared to typical design
  docs.
- **Filament name clarification** (Section 1): Proactively documents the
  `map_filament_name()` behavior to prevent future confusion.
- **Open questions** (Section 8): Well-scoped, actionable, and with clear
  stances on each.

---

## Corrected Section 7 Commands (for reference)

If all fixes above are applied, the migration commands would look like:

**Dry run:**
```bash
docker compose -f docker/docker-compose.yml run \
  --rm \
  spoolsync python3 migrate_variant.py --base-url http://spoolman:8000 --dry-run
```

**Apply:**
```bash
docker compose -f docker/docker-compose.yml run \
  --rm \
  spoolsync python3 migrate_variant.py --base-url http://spoolman:8000
```

**Single filament test:**
```bash
docker compose -f docker/docker-compose.yml run \
  --rm \
  spoolsync python3 migrate_variant.py --base-url http://spoolman:8000 --filament-id 42
```

**Verify:**
```bash
curl http://block:7912/api/v1/filament/42 | python3 -m json.tool
```
