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
| [bridge/](bridge/) | **`spoolsync`** — the bridge, phase 1 (one-way SpoolEase → Spoolman). Runnable Python; see [bridge/README.md](bridge/README.md) |

## Source of truth (summary)

Ownership is **per-field**, not global:

| Data | Owner | Rationale |
|---|---|---|
| Spool existence, physical weight, consumption, RFID tag binding, K/pressure-advance calibration | **SpoolEase** | It has the scale, AMS/print telemetry, and the NFC reader |
| Filament/Vendor catalog library, density/diameter | **Spoolman** | Richer schema + the external SpoolmanDB mirror |
| Inventory presentation: `location`, `price`, `archived`, comments, reporting | **Spoolman** | It's the ecosystem hub (Moonraker/Klipper/etc.) |

Cross-system join key: **the RFID tag UID** (both sides have it), with the SpoolEase
record `id` as a fallback stored in a Spoolman `extra` field.

## Status

- ✅ Design complete (the docs above).
- ✅ Weight semantics **resolved** from the firmware (`weight_current` = gross scale
  reading; see [data-mapping.md](data-mapping.md#weight-semantics-resolved)).
- ✅ **Phase 1 bridge written** — one-way SpoolEase → Spoolman, in [bridge/](bridge/).
  ⚠️ Not yet executed (no Python/Docker on the authoring machine); run its tests and a
  `DRY_RUN=true` pass before trusting it. Needs the SpoolEase JSON endpoint
  ([spoolease-endpoints.md](spoolease-endpoints.md)).
- ⬜ Phase 2 (Spoolman → SpoolEase write-back) — designed in
  [data-flow.md](data-flow.md), not yet implemented.
- ⬜ Remaining open questions (diameter/density confirmation, material convention) in
  [data-mapping.md](data-mapping.md#open-questions-verify-before-building).
