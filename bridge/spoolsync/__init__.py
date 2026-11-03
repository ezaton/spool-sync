"""spoolsync — a one-way-first bridge that mirrors SpoolEase spools into Spoolman.

Phase 1 (implemented here): SpoolEase -> Spoolman reconciliation loop.
Phase 2 (scaffolded, see docs): Spoolman -> SpoolEase write-back of Spoolman-owned fields.

Neither Spoolman nor SpoolEase is modified by this package; see ../README.md and the
design docs in the parent directory. The only (optional, recommended) product change is a
small JSON read/write endpoint on SpoolEase — see ../spoolease-endpoints.md.
"""

__version__ = "0.1.0"
