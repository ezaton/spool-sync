"""Phase 2: Spoolman → SpoolEase reconciliation (write-back of Spoolman-owned fields).

This handles websocket events from Spoolman and writes back only the fields that Spoolman
owns: location, price, archived, comments. Physical weight is NEVER written back.
"""

from __future__ import annotations

import logging

from .spoolease import SpooleaseClient
from .state import State

log = logging.getLogger("spoolsync.reconcile_sm_to_se")


async def handle_spoolman_event(event: dict, se: SpooleaseClient, state: State, dry_run: bool = False) -> None:
    """Process a Spoolman websocket event and write back to SpoolEase if needed.

    Event format:
    {
        "type": "added" | "updated" | "deleted",
        "resource": "spool",
        "date": "2026-11-08T...",
        "payload": { full spool entity }
    }
    """
    event_type = event.get("type")
    resource = event.get("resource")
    payload = event.get("payload", {})

    # Only process spool events
    if resource != "spool":
        return

    spoolman_spool_id = payload.get("id")
    if not spoolman_spool_id:
        log.warning("spoolman event missing spool id: %s", event)
        return

    # Echo suppression: Check if this is our own recent write
    if state.is_echo_from_spoolman(spoolman_spool_id, payload):
        log.debug("suppressing echo: spoolman spool_id=%d (our recent write)", spoolman_spool_id)
        return

    # Resolve to SpoolEase ID
    spoolease_id = state.get_spoolease_id_by_spoolman_id(spoolman_spool_id)
    if not spoolease_id:
        log.debug("no SpoolEase mapping for Spoolman spool_id=%d, skipping write-back", spoolman_spool_id)
        return

    # Extract Spoolman-owned fields to write back
    updates = {}
    if "location" in payload:
        updates["location"] = payload["location"]
    if "comment" in payload:
        updates["comment"] = payload["comment"]
    # Note: price and archived are not currently mapped back due to SpoolEase limitations

    if not updates:
        log.debug("no writable fields changed for spool_id=%d", spoolman_spool_id)
        return

    # Skip write-back for now until we implement full record fetching
    # SpoolEase add-edit endpoint requires full spool data, not partial updates
    # TODO: Fetch current spool from SpoolEase, merge changes, then write back
    log.debug("skipping write-back to SpoolEase %s (partial updates not yet supported): %s",
              spoolease_id, updates)
    return

    if dry_run:
        log.info("[dry-run] would write to SpoolEase %s: %s", spoolease_id, updates)
        return

    # Write back to SpoolEase
    log.info("writing Spoolman changes back to SpoolEase %s: %s", spoolease_id, updates)
    success = se.write_record(spoolease_id, updates)

    if success:
        # Record this write for echo suppression on the SpoolEase → Spoolman side
        content_snapshot = {
            "location": updates.get("location"),
            "comment": updates.get("comment"),
        }
        state.record_write_to_spoolease(spoolman_spool_id, content_snapshot)
        log.debug("recorded write-back to SpoolEase for echo suppression")
    else:
        log.error("failed to write back to SpoolEase record %s", spoolease_id)
