# Design Review: DESIGN_VARIANT_SYNC.md v4 (Review v4)

> **Reviewer:** Claude Opus 4.6
> **Date:** 2026-11-20
> **Status:** Review of v4 draft (post v1-v3 feedback)
> **Verdict:** Ready for implementation. No blocking issues remain.

---

## Summary of v3 Findings

| Finding | Status in v4 |
|---------|-------------|
| `Silk+` keyword broken by word-boundary regex | **Fixed** — removed from keyword list; `"Silk"` handles both |
| `"Matte PLA"` keyword creates bridge/migration mismatch | **Fixed** — removed from keyword list |
| Open Question #2 answer incorrect | **Fixed** — now correctly documents the `if spec.extra:` falsy behavior |
| Design version header stale | **Fixed** — says "v4 — incorporates design-review-v1 through v3 feedback" |

All v3 issues have been addressed.

---

## Regression Check

Verified the updated `_variant_pattern()` + revised keyword list against edge
cases (all pass):

| Input | Result | Correct? |
|-------|--------|----------|
| `"Matte Black"` | `Matte` | Yes — standard current-mapper output |
| `"Basic Dark Blue"` | `Basic` | Yes |
| `"CR-PETG White"` | `None` | Yes — no variant in vendor-prefix name |
| `"Silk+ Red"` | `Silk` | Yes — `Silk` matches, close enough for migration |
| `"Metallic Blue"` | `None` | Yes — `\bMetal\b` does not match inside "Metallic" |
| `"HF White"` | `HF` | Yes — short keyword at word boundary |
| `"PETG-CF Black"` | `CF` | Yes — hyphen acts as word boundary |
| `"High Speed Red"` | `High Speed` | Yes — multi-word variant |
| `"Professional"` | `None` | Yes — `\bPro\b` does not match inside "Professional" |
| `"Ultraviolet"` | `None` | Yes — `\bUltra\b` does not match inside "Ultraviolet" |
| `"Frosty"` | `None` | Yes — `\bFR\b` does not match inside "Frosty" |

---

## Observations (Non-blocking)

### 1. Backward Compatibility Row Could Be More Precise

**Location:** Section 4.5

The table states:

> Existing filaments with variant set manually | Overwritten when the
> SpoolEase record next changes (hash mismatch triggers re-sync)

This is only true when the SpoolEase record **has** a `material_subtype`. If
it doesn't, the empty `extra` dict is falsy and the manual variant survives
the re-sync — exactly as documented in Open Question #2.

The row isn't wrong (a re-sync from a record with a subtype does overwrite),
but could be more precise:

> Overwritten on re-sync only if the SpoolEase record has a subtype;
> otherwise the manual value is preserved (see Open Question #2)

This is a documentation polish item, not a design issue.

### 2. `_variant_pattern()` Lookahead Branch Is Currently Dead Code

The `_variant_pattern()` helper (migrate_variant.py:63-86) includes logic
for keywords ending in non-word characters (the `(?!\w)` suffix branch). With
`"Silk+"` removed from the keyword list, all current keywords are purely
alphabetic, so this branch is never exercised.

The helper is correct and future-proof — if a non-word-ending keyword is
added later, it will work. No action needed; just noting for completeness.

### 3. Migration Script `Silk` Returns `"Silk"` for `"Silk+"` Filaments

When a filament name contains "Silk+" (from a legacy mapper or manual entry),
the migration script returns `variant="Silk"` while the bridge would set
`variant="Silk+"` (from `material_subtype`). This temporary mismatch is
self-correcting: the bridge writes the authoritative value on the next
re-sync when the SpoolEase record changes.

Already acknowledged in the keyword list comment: "matches both 'Silk' and
'Silk+' (simpler, no regex edge cases)".

---

## Design Completeness Checklist

| Area | Status |
|------|--------|
| Problem statement and data flow | Clear, accurate, includes live-system evidence |
| Code changes — mapper.py | Correct; import, dataclass field, map_variant(), map_filament() |
| Code changes — spoolman.py | Correct; upsert_filament() extra handling, ensure_variant_field() |
| Code changes — sync.py | Correct; startup call |
| Code changes — sync_twoway.py | Correct; startup call |
| Backfill strategy | Correct; migration script is required, reconcile loop won't auto-backfill |
| Migration script | Correct; word-boundary regex, no false positives, proper URL convention |
| Docker rollout commands | Correct; service name `spoolsync`, base URL without `/api/v1` |
| Impact analysis | Thorough; forward sync, reverse sync, downstream consumers |
| Backward compatibility | Complete; covers all entity types and ownership scenarios |
| Risk assessment | Honest and actionable |
| Testing plan | Unit tests, integration tests, manual verification steps |
| Open questions | Well-scoped with correct answers |
| Data flow diagram | Accurate and includes all components |

---

## Conclusion

The design has matured through four review rounds. All critical issues (content
hash backfill gap, missing sync_twoway.py, incorrect filament names, broken
docker commands, regex edge cases, incorrect open question answer) have been
resolved.

The remaining observations are documentation polish and an accepted tradeoff
in the migration script's "Silk" vs "Silk+" handling. None are blocking.

**The design is ready for implementation.**
