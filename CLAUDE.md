# sync-tool

SpoolEase ↔ Spoolman two-way sync. **Start by reading [doc/DESIGN.md](doc/DESIGN.md)** — it
holds the full design: architecture, data flow, data mapping, decision rationale, and
ground-truth references for both codebases.

For code-level details, see [doc/FULL_DETAILS.md](doc/FULL_DETAILS.md).

Ground-truth references (merged into DESIGN.md):
- Spoolman API: see [doc/DESIGN.md#referencespoolman-api](doc/DESIGN.md#referencespoolman-api)
- SpoolEase internals: see [doc/DESIGN.md#referencespoolease-internals](doc/DESIGN.md#referencespoolease-internals)

The bridge code is in [bridge/](bridge/) (two-way sync, **never executed here** — no
Python/Docker on the authoring machine; run `pytest` + a `DRY_RUN=true` pass first).
