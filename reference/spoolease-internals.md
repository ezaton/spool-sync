# Reference: SpoolEase — storage, networking, data flow, weight (ground truth)

Distilled facts a future session needs to read/write SpoolEase without re-exploring. Repo:
`D:\Users\c-etzion.barnoy\GIT\private\SpoolEase` (Rust `no_std` ESP32-S3 firmware). Core
files were read directly; the networking inventory came from an Explore subagent.

## Platform

- Target `xtensa-esp32s3-none-elf`, `no_std` + `alloc` (`core/.cargo/config.toml:19-22`),
  flashed with `espflash`. Board: WT32-SC01 Plus (ESP32-S3 + display + microSD over SPI).
- Async runtime: embassy / esp-rtos; heap via esp-alloc. Much platform code is in an external
  crate **`esp-hal-app-framework` (git tag 0.6.1)** — not vendored here (SD store, web-app
  encryption, HTTP client for OTA, NTP live there).
- Two products: **Console** (this `core/` firmware, talks to Bambu printers) and **Scale**
  (separate device feeding weight to the Console).

## The record — `core/src/spool_record.rs`

`SpoolRecord` (:24-59), keyed by string `id`. Fields (declaration order = CSV column order):
`id, tag_id, material_type, material_subtype, color_name, color_code (RRGGBBAA),
note, brand, weight_advertised, weight_core, weight_new, weight_current, slicer_filament,
added_time, encode_time, added_full, consumed_since_add, consumed_since_weight, ext_has_k,
data_origin, tag_type`.

**CRITICAL serde gotcha:** the struct carries format-agnostic adapters —
`serialize_f32_base64` on `consumed_since_*` (floats → base64) and `serialize_bool_yn` /
y-n-empty on the bools. So serializing `SpoolRecord` to JSON directly yields base64 floats
and `"y"/"n"` bools. The JSON sync endpoint must use a **plain DTO** (see
`../spoolease-endpoints.md`). Extended data (`SpoolRecordExt`: full tag bytes, K/pressure-
advance `k_info`, origin) lives in a per-record sidecar JSON `/store/spools.ext/{…}/{id}.jsn`.

**No modification timestamp.** Only `added_time` / `encode_time` (epoch secs, NTP), set at
create. Change detection must be content-based.

## Storage — `core/src/csvdb.rs`, `core/src/store.rs`

- DB = CSV on microSD at `/store/spools` → `spools.db` (rows) + `spools.dbm` (JSON
  `{version}`). Loaded fully into `HashMap<id, CsvRecordInfo>` at boot (`csvdb.rs:136`);
  `records` is **`pub`** (`csvdb.rs:93`). `insert` (`csvdb.rs:232`) no-ops if the record is
  byte-equal; else in-place rewrite or append. `delete` (:291) overwrites the row with `-`.
- `Store` (the abstraction over `CsvDb`), key methods in `store.rs`:
  - `query_spools() -> Option<String>` (:171-188) — ALL records as one CSV blob.
  - `add_spool` (:235, assigns `last_spool_id+1`, sets `added_time`), `update_spool` (:398),
    `edit_spool_from_web` (:275), `delete_spool` (:190).
  - `get_spool_by_id` (:368), `get_spool_by_tag_id`/`_hex_tag` (:335-347).
  - Secondary in-RAM index `tag_id_index: HashMap<tag_hex → id>` (:129).
  - `edit_spool_from_web` deliberately **won't change `weight_new`/`weight_current` from the
    web** (:295-296) — good: the device owns physical weight.

## Weight semantics (RESOLVED) — `core/src/view_model.rs`

`weight_left_spool` (:1346-1357):
```
remaining = (weight_current - weight_core) - consumed_since_weight          // core known
remaining = (weight_current - (weight_new - weight_advertised)) - consumed_since_weight  // core unknown
```
Therefore:
- **`weight_current` = GROSS scale reading** (filament + core). **`weight_new` = GROSS at
  registration.** **`weight_advertised` = nominal NET full.** **`weight_core` = tare**;
  if unknown, `tare ≈ weight_new − weight_advertised`.
- `consumed_since_weight` = printed since last weighing (reset to 0 on each weigh,
  `view_model.rs:1941-1942`). `consumed_since_add` = printed since the spool was added.
- Bridge rule (in `mapper.compute_remaining`): weighed → `weight_current − tare −
  consumed_since_weight`; never weighed → `weight_advertised − consumed_since_add`; send as
  Spoolman `remaining_weight` with `initial_weight = weight_advertised`, `spool_weight = tare`.

## How a record is created/updated (data-flow entry points)

All funnel through `add_spool`/`update_spool`/`edit_spool_from_web` → `CsvDb::insert`:
- **Web UI** POST `/api/spools/add-edit` (`web_app.rs:293-374`).
- **RFID scan** (PN532) → `get_spool_by_tag_id`; encoding a tag sets `tag_id`/`encode_time`
  (`view_model.rs`, `bambu.rs`, `tag_standards.rs`).
- **Scale** → `update_spool` sets `weight_current`, resets `consumed_since_weight`.
- **Bambu AMS/print** telemetry (printer MQTT) updates `consumed_since_add` when
  `track_print_consume` is on (`app_config.rs:77-78`).

## Networking — `core/src/{web_app,my_mqtt,ssdp,app_ota}.rs`

- **HTTP server (picoserve, port 80)**, captive portal. Routes in `web_app.rs`:
  - `GET /api/spools` (:258-269) → `store.query_spools()` CSV, **AES-GCM encrypted** via the
    `Encryption` extractor + `.encrypt()` (PBKDF2 key from the 7-char device security key).
  - `POST /api/spools/add-edit` (:293-374), `POST /api/spools/delete` (:271),
    `GET /api/spools-in-printers` (:248), config endpoints (:109-246), `GET /api/store-backup`.
  - Unencrypted CSV reference: `GET /spools-catalog` (:166), `/filament-brands` (:174).
  - → A clean read/write needs a new **token-gated JSON** route (`../spoolease-endpoints.md`).
- **Outbound HTTPS exists** but only as OTA: `app_ota.rs:165-175,198-208` (`run_ota`, pinned
  cert). No generic outbound client in app code; the TLS stack (`esp-mbedtls`) + `embassy-net`
  TCP are present (`my_mqtt.rs:449-512`). Building outbound-to-arbitrary-URL is possible but
  unwritten.
- **MQTT** (`my_mqtt.rs`) → the **local printer** (TLS, pinned Bambu CA), not cloud. **SSDP**
  (`ssdp.rs`) = passive LAN discovery of printers.

## Config — `core/src/{settings,app_config}.rs`, `core/config/spoolease.cfg`

- Two layers merged at boot (`main.rs:312-335`): flash settings + optional `spoolease.cfg`
  TOML on SD. `AppConfig` struct ~`app_config.rs:101-116`; config keys ~`:21-28`. `.cfg`
  template has `[wifi] [printer] [tag] [display]`. A `[integration] token` (and a Spoolman
  URL if ever device-hosted) would be added here.
- Richer config is edited through the **encrypted** web `/config` page.

## Catalog data — `core/src/data/*.csv` (maps to Spoolman concepts)

| File | Shape | Maps to |
|---|---|---|
| `materials.csv` | `material, filament_info(code), temp_min, temp_high` (37) | Filament.material + temps |
| `filament-brands.csv` | `brand[, manufacturer]` (316) | Vendor.name (+ hierarchy hint) |
| `base-filaments-index.csv` | `GFAxx, name, temp_lo, temp_hi, material` (98) | Filament `external_id`/type |
| `bambu-color-names.csv` | `GFAxx, RRGGBBAA, name, code` (294) | color_hex + color_name |
| `Spool-Core-Weights.csv` | `spool-model, grams,,` (221) | spool_weight (tare) catalog |
