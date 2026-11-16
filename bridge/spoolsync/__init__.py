"""spoolsync — a two-way bridge that keeps filament spool data in sync between SpoolEase and Spoolman.

Phase 1 (implemented): SpoolEase → Spoolman reconciliation loop (polling).
Phase 2 (implemented): Spoolman → SpoolEase write-back (websocket-driven).

Neither Spoolman nor SpoolEase is modified by this package; see ../README.md and the
design docs in the parent directory. The only (optional, recommended) product change is a
small JSON read/write endpoint on SpoolEase — see ../spoolease-endpoints.md.
"""

__version__ = "0.1.0"
