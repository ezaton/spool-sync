# SpoolEase changeset: a clean JSON sync endpoint

This is the **one small product change** the external-bridge approach wants (see
[decision.md](decision.md)). It lets the bridge read SpoolEase over plain JSON instead of
reimplementing the device's AES-GCM web encryption. Spoolman needs **no** change.

> **Scope:** phase 1 needs only the **read** endpoint. The **write** endpoint is for phase-2
> two-way write-back; include it now or later.
>
> ⚠️ **This is a reviewed design + annotated Rust sketch, not a compiled patch.** SpoolEase
> is `no_std` ESP32-S3 firmware built against an external framework crate; it can't be
> compiled in this environment. Treat the code as a starting point to build & flash, not a
> drop-in diff.

## Why a new endpoint (and not the existing `/api/spools`)

`GET /api/spools` (`web_app.rs:258-269`) returns `store.query_spools()` — the whole DB as
CSV — **AES-GCM-encrypted** with a PBKDF2 key from the 7-char device security key. Two
problems for a sync tool: it must hold the key and reimplement the crypto, **and** the CSV
is serialized through `SpoolRecord`'s tag-encoding serde adapters (floats as base64, bools
as `"y"`/`"n"`), so it's awkward to parse. A dedicated JSON endpoint sidesteps both.

## The critical detail: serialize a plain DTO, not `SpoolRecord`

`SpoolRecord` carries `#[serde(serialize_with = "serialize_f32_base64")]` and
`serialize_bool_yn` (for the tag-URL encoding). If you `serde_json::to_string(&spool_record)`
directly, those adapters leak into the JSON. Define a **plain DTO** with normal types and
convert into it. This DTO *is* the bridge's contract (`bridge/spoolsync/models.py`).

## Change 1 — `core/src/store.rs`: add a JSON serializer next to `query_spools()`

`query_spools()` is at `store.rs:171-188`. Add a sibling:

```rust
use serde::Serialize;

// Plain-typed mirror of SpoolRecord for external JSON consumers. Normal number/bool
// serialization (unlike SpoolRecord, whose serde adapters base64 floats and y/n bools).
#[derive(Serialize)]
pub struct SpoolRecordJson {
    pub id: String,
    pub tag_id: String,
    pub material_type: String,
    pub material_subtype: String,
    pub color_name: String,
    pub color_code: String,
    pub note: String,
    pub brand: String,
    pub weight_advertised: Option<i32>,
    pub weight_core: Option<i32>,
    pub weight_new: Option<i32>,
    pub weight_current: Option<i32>,
    pub slicer_filament: String,
    pub added_time: Option<i32>,
    pub encode_time: Option<i32>,
    pub added_full: Option<bool>,
    pub consumed_since_add: f32,
    pub consumed_since_weight: f32,
    pub ext_has_k: bool,
    pub data_origin: String,
    pub tag_type: String,
}

impl From<&SpoolRecord> for SpoolRecordJson {
    fn from(r: &SpoolRecord) -> Self {
        Self {
            id: r.id.clone(), tag_id: r.tag_id.clone(),
            material_type: r.material_type.clone(), material_subtype: r.material_subtype.clone(),
            color_name: r.color_name.clone(), color_code: r.color_code.clone(),
            note: r.note.clone(), brand: r.brand.clone(),
            weight_advertised: r.weight_advertised, weight_core: r.weight_core,
            weight_new: r.weight_new, weight_current: r.weight_current,
            slicer_filament: r.slicer_filament.clone(),
            added_time: r.added_time, encode_time: r.encode_time, added_full: r.added_full,
            consumed_since_add: r.consumed_since_add, consumed_since_weight: r.consumed_since_weight,
            ext_has_k: r.ext_has_k, data_origin: r.data_origin.clone(), tag_type: r.tag_type.clone(),
        }
    }
}

impl Store {
    /// All spool records as a JSON array string (for the external sync endpoint).
    pub fn query_spools_json(&self) -> Option<String> {
        let spools_db = self.spools_db.get()?;
        let items: alloc::vec::Vec<SpoolRecordJson> =
            spools_db.records.borrow().values().map(|ri| SpoolRecordJson::from(&ri.data)).collect();
        serde_json::to_string(&items).ok()
    }
}
```

(`spools_db.records` is `pub` on `CsvDb`, `csvdb.rs:93`.)

## Change 2 — `core/src/app_config.rs`: add an integration token

Add a field to `AppConfig` (struct around `app_config.rs:101-116`) and load it from the
existing flash/TOML config path, exactly like the other settings:

```rust
pub integration_token: Option<String>,   // shared secret for /api/*.json endpoints
```

Expose it in `core/config/spoolease.cfg`:

```toml
[integration]
token = "choose-a-long-random-string"
```

## Change 3 — `core/src/web_app.rs`: the routes

Add alongside the other `router.route(...)` calls (near `/api/spools`, `web_app.rs:258`).
These do **not** use the `Encryption` extractor; they gate on the token instead. Check the
token from an `Authorization: Bearer <token>` header (or `?token=` query — whichever
picoserve extractor you wire up; confirm the exact API against the picoserve version in
`cargo.toml`).

```rust
// GET /api/spools.json  -> [ {SpoolRecordJson}, ... ]   (plain JSON, token-gated)
let router = router.route(
    "/api/spools.json",
    get(async move |state: State<ConsoleAppState>, /* token extractor */| {
        // if token != state.0.app_config.borrow().integration_token { return 401 }
        let body = state.0.store.query_spools_json().unwrap_or_else(|| "[]".to_string());
        picoserve::response::Response::new(StatusCode::OK, body)
            .with_header("content-type", "application/json")
    }),
);

// POST /api/spools.json  -> create/update one record   (PHASE 2 — two-way write-back)
// Accept a SpoolRecordJson, convert to SpoolRecord, and route through the SAME path the
// web UI uses so all invariants hold:
//   - new (empty id):            store.add_spool(rec, SpoolRecordExt::default())
//   - existing:                  store.edit_spool_from_web(rec)   (web_app.rs:293-374)
// Note edit_spool_from_web deliberately does NOT let the web change weight_new/weight_current
// (store.rs:295-296) — good, because SpoolEase owns physical weight; the bridge only writes
// Spoolman-owned fields (location/comment/etc.) back, matching the ownership table in
// data-mapping.md.
```

## Security

Both routes are token-gated and intended for the **LAN only** (the bridge runs next to
Spoolman). The token is a shared secret in `spoolease.cfg`; rotate it like a password. These
endpoints expose/accept the spool DB in plaintext JSON, so don't expose port 80 to the
internet (that was already true for the captive portal).

## Build & verify

1. Set a token in `spoolease.cfg`.
2. Build/flash the console firmware (per SpoolEase docs; `espflash`, target
   `xtensa-esp32s3-none-elf`).
3. `curl -H "Authorization: Bearer <token>" http://<device-ip>/api/spools.json` → a JSON
   array of records.
4. Point the bridge at it: `SPOOLEASE_MODE=json`, `SPOOLEASE_TOKEN=<token>`.

## Upstreaming

This fits SpoolEase's "integrates with everything" design and is self-contained (one store
method + one config field + one or two routes), which makes it a reasonable PR. Frame it as
"optional JSON API for external integrations (e.g. Spoolman sync)," off unless a token is
configured.
