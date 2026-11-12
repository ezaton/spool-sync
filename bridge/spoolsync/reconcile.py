"""The SpoolEase -> Spoolman reconciliation pass (phase 1, one-way).

Each pass: fetch all SpoolEase records, diff against the shadow store by content hash, and
upsert Vendor -> Filament -> Spool (in that order, because of the FK chain) for anything
new or changed. Records that disappeared from SpoolEase are archived in Spoolman (not
deleted), preserving history. Unchanged records are skipped cheaply.
"""

from __future__ import annotations

import logging

from .config import Config
from .mapper import map_filament, map_spool, map_vendor
from .models import SpoolRecord
from .spoolease import SpooleaseClient
from .spoolman import SpoolmanClient
from .state import ShadowRow, State

log = logging.getLogger("spoolsync.reconcile")


def _resolve_density(rec: SpoolRecord, density_map: dict[str, float], default: float) -> float:
    material = rec.material_type.strip().lower()
    # exact, then prefix (e.g. "pla matte" -> "pla")
    if material in density_map:
        return density_map[material]
    base = material.split()[0] if material else ""
    return density_map.get(base, default)


def reconcile_once(se: SpooleaseClient, sm: SpoolmanClient, state: State, cfg: Config,
                   density_map: dict[str, float]) -> dict[str, int]:
    stats = {"created": 0, "updated": 0, "unchanged": 0, "archived": 0, "errors": 0}
    records = se.fetch_records()
    seen: set[str] = set()

    for rec in records:
        seen.add(rec.id)
        h = rec.content_hash()
        shadow = state.get(rec.id)
        if shadow is not None and shadow.content_hash == h and not shadow.archived:
            stats["unchanged"] += 1
            continue
        try:
            density = _resolve_density(rec, density_map, cfg.default_density)
            vspec = map_vendor(rec)
            vendor_id = sm.upsert_vendor(vspec) if vspec else None
            filament_id = sm.upsert_filament(map_filament(rec, density=density, diameter=cfg.default_diameter), vendor_id)
            if filament_id is None:  # dry-run or failed create
                continue
            sspec = map_spool(rec)
            existing_id = shadow.spoolman_spool_id if shadow and shadow.spoolman_spool_id else sm.find_spool_id(sspec)
            spool_id = sm.upsert_spool(sspec, filament_id, existing_id)
            state.upsert(ShadowRow(rec.id, h, spool_id, archived=False))

            # Record write for echo suppression (Phase 2)
            if spool_id:
                content_snapshot = {
                    "location": sspec.comment,  # SpoolEase note maps to location
                    "comment": sspec.comment,
                    "remaining_weight": sspec.remaining_weight,
                }
                state.record_write_to_spoolman(spool_id, content_snapshot)

            stats["created" if existing_id is None else "updated"] += 1
        except Exception:  # keep the loop alive; one bad record shouldn't stall the rest
            log.exception("failed to sync SpoolEase record id=%s", rec.id)
            stats["errors"] += 1

    if cfg.archive_missing:
        for sid in state.all_ids() - seen:
            shadow = state.get(sid)
            if shadow and shadow.spoolman_spool_id and not shadow.archived:
                try:
                    sm.archive_spool(shadow.spoolman_spool_id)
                    state.upsert(ShadowRow(sid, shadow.content_hash, shadow.spoolman_spool_id, archived=True))
                    stats["archived"] += 1
                except Exception:
                    log.exception("failed to archive removed SpoolEase id=%s", sid)
                    stats["errors"] += 1

    return stats
