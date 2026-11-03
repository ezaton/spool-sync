# Data Mapping

How SpoolEase's flat `SpoolRecord` translates to/from Spoolman's normalized
`Vendor → Filament → Spool`.

## The granularity mismatch (the crux)

SpoolEase stores **one flat row per physical spool**, cramming three concerns together:

```
SpoolRecord = { type/catalog } + { physical instance } + { Bambu calibration }
```

Spoolman **normalizes** the same information across three tables:

```
Vendor (brand)  ──<  Filament (a type/product)  ──<  Spool (a physical instance)
```

So the bridge must **fan out** on the way in and **collapse** on the way out:

- 10 SpoolEase rows of "Bambu PLA Basic Black" → **1** Vendor (Bambu) + **1** Filament
  (PLA Basic Black) + **10** Spools.
- A Spoolman-side rename of that one Filament → must propagate to **all 10** SpoolEase
  rows.

De-duplication of the Vendor/Filament level is therefore central — handled by the identity
keys in [data-flow.md](data-flow.md#identity--idempotency-keys).

## Field mapping

### SpoolRecord → Vendor

| SpoolEase | Spoolman `Vendor` | Notes |
|---|---|---|
| `brand` | `name` | `filament-brands.csv` hints a brand→manufacturer hierarchy; Spoolman Vendor is flat |
| (synthesized) | `external_id` | normalized brand slug, for idempotent match |
| `weight_core` (catalog) | `empty_spool_weight` | optional default tare for the brand |

### SpoolRecord → Filament (the shared *type*)

| SpoolEase | Spoolman `Filament` | Notes |
|---|---|---|
| `material_type` (+ `material_subtype`) | `material` | convention decision: `PLA` vs `PLA Matte` — see open questions |
| `color_name` | `name` | or `"{material_subtype} {color_name}"` |
| `color_code` (RRGGBBAA) | `color_hex` | Spoolman accepts 6/8-hex, strips `#` — compatible |
| temps (`materials.csv` / `FilamentSupInfo`) | `settings_extruder_temp`, `settings_bed_temp` | |
| `weight_advertised` | `weight` (nominal full **net**) | |
| `weight_core` | `spool_weight` (tare) | catalog in `Spool-Core-Weights.csv` |
| `slicer_filament` (e.g. `GFA01`) | `external_id` and/or `article_number` | base-filament code; ideal Filament dedup key |
| — **not stored** | **`density`** (REQUIRED, >0) | **GAP** — resolve via Spoolman `GET /external/material` by material name |
| — **not stored** | **`diameter`** (REQUIRED, >0) | **GAP** — default `1.75` (SpoolEase is Bambu-centric) |

### SpoolRecord → Spool (the physical *instance*)

| SpoolEase | Spoolman `Spool` | Notes |
|---|---|---|
| `id` | `extra.spoolease_id` | idempotency fallback key; register field once |
| `tag_id` | linked via `POST /spool/{id}/tag {uid}` | becomes the primary cross-system key |
| `weight_advertised` | `initial_weight` (net full) | nominal net weight of a full spool |
| `weight_core` (or `weight_new − weight_advertised`) | `spool_weight` (tare) | empty core/spool weight |
| computed net remaining (see below) | `remaining_weight` | Spoolman stores `used_weight = initial_weight − remaining_weight` |
| `consumed_since_add` / `consumed_since_weight` | folded into the net-remaining calc | print-tracked usage |
| `note` | `comment` | |
| `k_info` / `ext_has_k` (calibration) | `extra.spoolease_k` (JSON string) | Spoolman has no native K field |
| (device presence) | `archived` | record gone on SpoolEase → `archived=true` |

> Spoolman stores only `used_weight`; `remaining_weight`/lengths are **computed**
> (`remaining_weight = max(initial_weight − used_weight, 0)`). You send *either*
> `remaining_weight` *or* `used_weight` on write — never both.

## Source of truth, per field

Ownership is **not** global — each field has a natural owner. Sync direction follows
ownership.

| Field group | Owner | Flows | Rationale |
|---|---|---|---|
| Spool existence, `weight_current`/consumption, `tag_id` binding, K calibration | **SpoolEase** | → Spoolman | SpoolEase has the scale, AMS/print telemetry, NFC reader |
| `material`, `color_hex`, `density`, `diameter`, temps (catalog identity) | **Spoolman** | ↔ (created from SpoolEase, enriched by Spoolman) | Spoolman has the richer catalog + external SpoolmanDB mirror |
| `location`, `price`, `archived`, `comment`, custom `extra` (inventory presentation) | **Spoolman** | → SpoolEase | Spoolman is the inventory hub |

Phase 1 can be **one-way SpoolEase → Spoolman** (mirror live spools). Phase 2 adds
write-back of the Spoolman-owned rows, governed by this table + echo suppression.

## Spoolman endpoints used, per operation

| Operation | Call |
|---|---|
| Find vendor | `GET /vendor?external_id=<slug>` or `?name=<brand>` |
| Create/update vendor | `POST /vendor` / `PATCH /vendor/{id}` |
| Find filament | `GET /filament?external_id=<code>` (or `?material=&color_hex=&vendor.id=`) |
| Create/update filament | `POST /filament` / `PATCH /filament/{id}` |
| Find spool | `GET /spool?tag=<uid>` → else `GET /spool?extra.spoolease_id=<id>` |
| Create/update spool | `POST /spool` / `PATCH /spool/{id}` |
| Link RFID tag | `POST /spool/{id}/tag {uid, format}` (409 if already linked elsewhere) |
| Apply weight (delta) | `PUT /spool/{id}/use {use_weight}` |
| Apply weight (gross scale) | `PUT /spool/{id}/measure {weight}` |
| Register extra field (once) | `POST /field/spool/spoolease_id {field_type:"text", ...}` |
| Resolve density default | `GET /external/material` → density by material |
| Subscribe to changes | websocket `/api/v1/spool` (+ `/filament`, `/vendor`) |

## Weight semantics (RESOLVED)

Confirmed in `view_model.rs:1346-1357` (`weight_left_spool`):

```rust
let realtime_weight = (weight_current - weight_core) as f32 - consumed_since_weight;
// fallback when core unknown:
let realtime_weight = (weight_current - (weight_new - weight_advertised)) as f32 - consumed_since_weight;
```

- **`weight_current` = GROSS scale reading** (filament + core), not net remaining.
- **`weight_new` = GROSS at registration**; **`weight_advertised` = nominal NET full**;
  **`weight_core` = tare**. When core isn't known directly, `tare ≈ weight_new − weight_advertised`.
- **`consumed_since_weight`** = filament printed since the last weighing (reset to 0 on each
  weigh, `view_model.rs:1942`); **`consumed_since_add`** = printed since the spool was added.

**Bridge rule** — compute net remaining the same way SpoolEase does, then send it as
Spoolman `remaining_weight` (Spoolman derives `used_weight` itself):

```
tare       = weight_core  OR  (weight_new - weight_advertised)
if weight_current is not None:          # has been weighed
    remaining = weight_current - tare - consumed_since_weight
elif weight_advertised is not None:     # never weighed, track by print only
    remaining = weight_advertised - consumed_since_add
initial_weight (Spool) = weight_advertised      # net full
spool_weight  (Spool) = tare
```

This reproduces SpoolEase's own "weight left" exactly and sidesteps `/measure` vs `/use`
bookkeeping. (`PUT /spool/{id}/measure` with the raw gross `weight_current` is an
alternative, but then Spoolman — not SpoolEase — owns the net calc, which we don't want.)

## Open questions (verify before building)

1. **Density/diameter defaults.** Confirm SpoolEase is 100% 1.75 mm, and wire density from
   Spoolman's `GET /external/material`.
3. **`material` convention.** `PLA` + subtype-in-name, vs. `PLA Matte` in `material`.
   Affects Filament dedup and ecosystem compatibility (Moonraker/slicer expectations).
4. **Deletion policy.** Confirm "SpoolEase record gone → Spoolman `archived=true`" (not
   `DELETE`) is the desired behavior to preserve history.
5. **Multi-color.** SpoolEase carries a single `color_code` per record; Spoolman supports
   `multi_color_hexes`. Confirm whether SpoolEase ever represents multi-color (via tags)
   and whether that needs mapping.
6. **Tag format value.** What `format` string to record on `POST /spool/{id}/tag`
   (`bambu` / `ntag` / `openprinttag`) based on SpoolEase's `tag_type` / `data_origin`.
