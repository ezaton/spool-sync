# sync-tool

SpoolEase ↔ Spoolman two-way sync. **Start by reading [AGENTS.md](AGENTS.md)** — it holds
the full handoff: goal, the chosen approach (external bridge; Spoolman unmodified), what's
built, verified-vs-assumed facts, environment constraints, and next steps.

Ground-truth references (so you needn't re-explore the two codebases):
- [reference/spoolman-api.md](reference/spoolman-api.md)
- [reference/spoolease-internals.md](reference/spoolease-internals.md)

The bridge code is in [bridge/](bridge/) (phase-1, one-way, **never executed here** — no
Python/Docker on the authoring machine; run `pytest` + a `DRY_RUN=true` pass first).
