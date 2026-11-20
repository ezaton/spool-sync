# Design Review: DESIGN_VARIANT_SYNC.md (Review v3)

> **Reviewer:** Claude Opus 4.6
> **Date:** 2026-11-20
> **Status:** Review of updated design (post v1 + v2 feedback)
> **Verdict:** All prior critical/major issues resolved. Remaining issues are
> in the migration script's keyword matching and one incorrect claim in
> Open Questions.

---

## Summary of v2 Findings

| Finding | Status |
|---------|--------|
| Double `/api/v1` in docker commands | **Fixed** — now `--base-url http://spoolman:8000` |
| Wrong docker service name (`bridge` vs `spoolsync`) | **Fixed** — commands use `spoolsync` |
| Unused `-e SPOOLMAN_URL` in migration commands | **Fixed** — removed |
| Substring matching false positives (Metal/Metallic) | **Fixed** — word-boundary regex |
| Backward compatibility row misleading | **Fixed** — "when the SpoolEase record next changes" |
| Live system table note | **Fixed** — note added |

All v2 issues have been addressed.

---

## Issue: `Silk+` Keyword Broken by Word-Boundary Regex

**Location:** `migrate_variant.py:94-99`, VARIANT_KEYWORDS list

### Problem

The v2 review suggested word-boundary regex (`\b...\b`) to prevent false
positives like "Metal" matching "Metallic". This was implemented correctly
for most keywords, but `\b` breaks the `"Silk+"` keyword.

`\b` asserts a boundary between a word character (`\w` = `[a-zA-Z0-9_]`) and
a non-word character. The `+` in `Silk+` is a non-word character, so the
trailing `\b` requires a word character to follow — but in practice, `Silk+`
is followed by a space or end-of-string (both non-word), so `\b` never
matches:

```
"Silk+ Red"      →  \bSilk\+\b  →  NO MATCH  (+ then space: \W\W, no boundary)
"PLA Silk+ White" → \bSilk\+\b  →  NO MATCH
"Silk+"          →  \bSilk\+\b  →  NO MATCH  (+ then end: \W then nothing)
"Silk+PLA"       →  \bSilk\+\b  →  MATCH     (+ then P: \W\w, boundary exists)
```

Since `"Silk+"` is checked before `"Silk"` in the keyword list, the match
falls through to `"Silk"`, returning `variant="Silk"` instead of
`variant="Silk+"`.

**Impact:** Filaments with a SpoolEase subtype of `"Silk+"` would get
`variant="Silk"` from the migration script, while the bridge would set
`variant='"Silk+"'`. A one-time mismatch until the bridge next re-syncs
the record.

### Suggested Fix

For keywords containing non-word characters at the edges, use a lookahead/
lookbehind instead of `\b` on that side:

```python
def _variant_pattern(keyword: str) -> re.Pattern:
    """Build a regex that matches the keyword as a standalone token.

    Uses word boundaries where possible, but handles keywords ending (or
    starting) with non-word characters like 'Silk+' by substituting
    lookahead/lookbehind for the impossible \b.
    """
    escaped = re.escape(keyword)
    # If first char is \w, use \b; otherwise use lookbehind for non-\w or start
    if re.match(r'\w', keyword):
        prefix = r'\b'
    else:
        prefix = r'(?<!\w)'
    # If last char is \w, use \b; otherwise use lookahead for non-\w or end
    if re.match(r'\w', keyword[-1]):
        suffix = r'\b'
    else:
        suffix = r'(?!\w)'
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)
```

This would make `Silk+` match when followed by a space, end-of-string, or
another non-word character — while still preventing it from matching inside
longer tokens.

Alternatively, since `"Silk+"` is the only affected keyword, a simpler fix
is to special-case it or drop it from the list and let `"Silk"` handle both
variants (accepting the loss of specificity).

---

## Issue: `"Matte PLA"` Keyword Creates Bridge/Migration Mismatch

**Location:** `migrate_variant.py:35`, VARIANT_KEYWORDS list

### Problem

The keyword list includes `"Matte PLA"` (checked before `"Matte"`). This
exists to handle legacy filament names like `"Matte PLA Black"` that may
have been created by an older mapper version.

However, the bridge's `map_variant()` returns the raw `material_subtype`,
which would be `"Matte"` (not `"Matte PLA"`). This creates a mismatch:

| Source | Filament name | variant value |
|--------|--------------|---------------|
| Bridge (going forward) | `"Matte Black"` | `"Matte"` |
| Migration script (legacy name) | `"Matte PLA Black"` | `"Matte PLA"` |

When the bridge next re-syncs a record (due to SpoolEase data change), it
would overwrite `"Matte PLA"` with `"Matte"`. This is harmless in the end,
but the intermediate inconsistency is confusing.

### Suggested Fix

Remove `"Matte PLA"` from the keyword list. The `"Matte"` keyword (later in
the list) will match both `"Matte Black"` and `"Matte PLA Black"`, producing
`variant="Matte"` in both cases — consistent with the bridge.

If there are other multi-word variant keywords that combine subtype + material
(which the bridge would never produce), they should be removed too. In the
current list, `"Matte PLA"` is the only one with this problem — `"High Speed"`
and `"for AMS"` are genuine subtypes.

---

## Issue: Open Question #2 Answer Is Incorrect

**Location:** Section 8, Open Question #2

### Problem

The design states:

> **Should we handle the case where a filament has no subtype but the user
> manually set `extra.variant` in Spoolman?**
> The bridge will overwrite it on next reconcile.

This is wrong. When SpoolEase has an empty `material_subtype`:

1. `map_variant(rec)` returns `None`
2. `extra` stays as `{}` (empty dict)
3. `FilamentSpec.extra = {}`
4. In `upsert_filament()`: `if spec.extra:` is **falsy** for an empty dict
5. The PATCH body has no `extra` key at all
6. Spoolman preserves whatever `extra.variant` was already set

So the bridge does **not** overwrite a manually-set variant when SpoolEase has
no subtype. The manually-set value survives.

This is actually desirable behavior — if SpoolEase doesn't know the subtype,
there's no reason to clear a user's manual annotation. But the design should
state this correctly.

### Suggested Fix

Rewrite Open Question #2:

> **Should we handle the case where a filament has no subtype but the user
> manually set `extra.variant` in Spoolman?**
> The bridge will **not** overwrite it. When SpoolEase has an empty
> `material_subtype`, the `extra` dict is empty, and `upsert_filament()`
> skips it (`if spec.extra:` is falsy). Spoolman preserves the existing
> value. This is the desired behavior — SpoolEase-synced filaments where
> SpoolEase has no subtype keep any manually-set variant.
>
> If SpoolEase **does** have a subtype, the bridge writes it as the
> authoritative value on the next re-sync (when the record's content hash
> changes).

---

## Nit: Design Version Header

**Location:** Line 3

The header still says `v3 — incorporates design-review-v1.md feedback`. It
should be bumped to reflect v2 feedback incorporation as well (docker command
fixes, regex update, etc.).

---

## What the Design Gets Right

All prior critical and major issues have been resolved cleanly:

- **Backfill strategy** is now correctly documented as requiring the migration
  script.
- **Both sync entry points** (`sync.py` and `sync_twoway.py`) are covered.
- **Docker commands** use the correct service name and URL format.
- **Word-boundary regex** correctly prevents "Metal"/"Metallic" false
  positives (the remaining `Silk+` edge case is narrow).
- **Filament name examples** are consistent with `map_filament_name()` output.
- **Import change** is explicitly documented.
- **Section structure** clearly separates method definitions from call sites.
- **Container architecture note** is helpful and accurate.
- **Risk assessment** is honest and actionable.

The design is in good shape for implementation. The remaining issues are all
in the migration script's keyword matching (a one-time tool) and a factual
correction to one open question.
