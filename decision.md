# Decision: External Sync Tool vs. In-Product Modification

**Question:** For two-way sync, can a *minor modification* to SpoolEase or Spoolman do the
job, or do I need an external sync tool between them?

**Answer:** You need an **external sync tool** for the sync *engine*. The minor
modifications worth making are small, optional *affordances on SpoolEase* that make the
external tool cleaner — they are **not an alternative** to it.

---

## What two-way sync actually requires

Any robust bidirectional sync between two heterogeneous systems needs all of these:

1. **Bidirectional change capture** — detect what changed on each side.
2. **Model translation** — SpoolEase's one flat row ↔ Spoolman's 3 normalized tables
   (`Vendor → Filament → Spool`), a 1:N fan-out that must be reversed on the way back.
3. **Stable identity mapping** — remember that SpoolEase record `id=42` *is* Spoolman
   `spool_id=17`, across restarts.
4. **Conflict resolution + echo suppression** — decide who wins when both sides changed,
   and never re-apply a change you yourself just wrote (or you get infinite loops).
5. **Durable sync bookkeeping** — the ID map, the per-side *last-known state* (to diff
   against), and tombstones for deletes.
6. **Resilience** — retries, and tolerance for the device being offline.

## Why "modify one product" can't carry this alone

**Requirement #5 is the decisive one.** The sync bookkeeping is *third-party data that
belongs to neither system*:

- **Embed it in Spoolman** → you must add tables/columns to remember SpoolEase's shadow
  state and the ID map. That's schema pollution and a **maintained fork** that drifts from
  upstream Spoolman forever.
- **Embed it in SpoolEase** → you must store Spoolman's shadow state on the SD card and
  hold the ID map in RAM, on a constrained `no_std` ESP32-S3. Costly, and the mapping +
  conflict logic (requirements #2, #4) is exactly the kind of code you do **not** want to
  iterate on in firmware (reflash cycles, build toolchain).

An **external bridge owns a neutral third store** for all of this. That is precisely the
problem bridges exist to solve.

Two more factors push the same way:

- **Asymmetric models (#2).** Reversing the fan-out is non-trivial: a single filament
  rename on the Spoolman side must propagate to *many* SpoolEase records. This logic needs
  frequent tuning — put it where it's cheap to change (Python), not in firmware.
- **The device is intermittent.** SpoolEase is an appliance; it may be powered off or
  asleep. A bridge reconciles when it reappears. Two embedded peers trying to sync each
  other directly is fragile.

## The modification that *is* worth doing (and why it's minor)

SpoolEase already serves its whole DB at `GET /api/spools`, but **AES-GCM-encrypted** with
a key derived (PBKDF2) from the device's 7-char security key, as CSV. A bridge *can* read
that with zero firmware change by reimplementing the crypto and holding the key — but
that's brittle (couples to the internal CSV schema + crypto).

The clean alternative is **~2 small SpoolEase routes**, following the pattern already in
`web_app.rs`:

- `GET /api/spools.json` — token-gated, returns `store.query_spools()` as JSON (or just
  plaintext CSV). ~20–40 lines.
- `POST /api/spools.json` — token-gated write, reusing the existing `edit_spool_from_web`
  path, so the bridge can push Spoolman-owned fields back. Reuses existing logic.

These let the bridge speak clean JSON over the LAN instead of decrypting an internal blob.
**They assist the bridge; they don't replace it.** Spoolman needs **zero** changes in all
cases.

> There is also a `[spoolman]`-in-firmware option (device POSTs directly to Spoolman). It
> is the **heaviest** path — a full outbound HTTP client + JSON mapper + a Spoolman-change
> listener, all on the ESP32 — and it still can't cleanly host the bidirectional
> bookkeeping. Not recommended. See the matrix below.

## Re-evaluation: when you're willing to upstream the change

The "maintained fork drifts from upstream" objection only applies to a *private* fork. If
a product change is written to be **merged upstream**, it stops being a fork — so modifying
a product becomes a legitimate option, not just a bridge in disguise.

Two findings sharpen this:

- **Spoolman has a ready-made seam for an in-process sync worker.** `main.py:236-240` builds
  a `Scheduler` and calls `externaldb.schedule_tasks(schedule)`. `externaldb.py:243-261` is
  the template: check a config URL, run `_sync()` once at startup, then
  `scheduler.cyclic(interval, _sync)`. A `spoolease_sync.py` module of the same shape slots
  in with a **one-line** `main.py` addition + env vars, and can call Spoolman's internal DB
  layer directly (no self-REST). Low effort to build.
- **The hard dependency is identical in every option: talking to SpoolEase.** Its only HTTP
  interface today is the AES-GCM-encrypted CSV blob. So wherever the sync *engine* lives,
  the smallest and most reusable product change is **~2 small JSON endpoints on SpoolEase**
  (read + write). That is also the most natural PR to land: SpoolEase's whole value
  proposition is "integrates with Bambu, slicers, …", so "Spoolman integration support"
  fits it. By ecosystem convention, other tools integrate *into* Spoolman while Spoolman
  stays device-agnostic — which is why a SpoolEase-specific module merging into *Spoolman
  core* is the least certain to be accepted.

## Where the sync *engine* can live

| Host for the engine | Build effort | Two-way | Upstream-merge fit | Notes |
|---|---|---|---|---|
| **External bridge** + small SpoolEase endpoints | Low (Python) | Clean | Only the small SpoolEase endpoints get merged (welcome PR) | Keeps both cores generic; a separate process to run |
| **Inside Spoolman** (scheduled worker like `externaldb`) | Low (Python, direct DB) | Clean, in-process | Device-specific module — uncertain in Spoolman core | No extra process; one new module + 1 line in `main.py` |
| **Inside SpoolEase** firmware (full integration) | **High** (`no_std` HTTP client + mapper + Spoolman change-listener) | Heaviest | Most natural home, single maintainer | Flash/RAM budget; hardest to iterate |

All three still need the SpoolEase read/write interface (decrypt the blob, **or** the ~2
JSON endpoints). The endpoints are the recommended minimal change in every case.

## Recommendation

- **The one changeset worth landing regardless of choice:** the **~2 SpoolEase JSON
  endpoints** (token-gated read + write, reusing `store.query_spools()` /
  `edit_spool_from_web`). Small, self-contained, and a natural upstream contribution.
- **For the engine, pick by priority:**
  - *Least total change + your preferred topology* → **external bridge** (engine stays out
    of both products; only the SpoolEase endpoints are contributed).
  - *No separate process to run, engine shipped in a product* → **inside Spoolman** (the
    `externaldb`-style worker). Easiest in-product host.
  - *Make it the device's own feature, best upstream fit* → **inside SpoolEase**, accepting
    the firmware effort.
- **Avoid** a private fork of Spoolman that is never upstreamed (that is the only case the
  drift objection truly kills).

Note that "external bridge" and "inside Spoolman" share almost identical Python logic — the
difference is process boundary (out-of-process REST client vs. in-process direct DB calls),
so you can start as a bridge and fold it into Spoolman later with little rework if desired.
