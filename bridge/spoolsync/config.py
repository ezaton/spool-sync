"""Runtime configuration, loaded from environment variables.

All knobs live here so the rest of the package takes a typed Config, not os.environ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def _get_float(name: str, default: float) -> float:
    raw = _get(name)
    return float(raw) if raw is not None else default


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    return int(raw) if raw is not None else default


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # --- Spoolman (target) ---
    spoolman_url: str  # e.g. http://spoolman:8000/api/v1

    # --- SpoolEase (source) ---
    spoolease_url: str  # e.g. http://192.168.1.50
    spoolease_token: str | None  # bearer token for the JSON endpoint, if configured
    # read mode: "json" (recommended, needs the small SpoolEase endpoint) or "encrypted"
    # (reads the stock AES-GCM /api/spools blob; not implemented in phase 1 — see docs)
    spoolease_mode: str
    spoolease_security_key: str | None  # 7-char device key, only for "encrypted" mode

    # --- Mapping defaults (see ../data-mapping.md) ---
    default_diameter: float  # SpoolEase stores no diameter; Bambu-centric => 1.75
    # Fallback densities by material (g/cm^3) when Spoolman's /external/material lacks one.
    # Spoolman REQUIRES density>0 and diameter>0 on every Filament.
    default_density: float

    # --- Loop / behavior ---
    poll_interval_seconds: int
    dry_run: bool  # log intended writes without performing them
    archive_missing: bool  # SpoolEase record gone -> archive (not delete) in Spoolman
    state_path: str  # sqlite file for sync bookkeeping
    log_level: str
    two_way_sync: bool  # Phase 2: enable Spoolman → SpoolEase write-back via websocket

    @staticmethod
    def from_env() -> "Config":
        spoolman_url = _get("SPOOLMAN_URL")
        spoolease_url = _get("SPOOLEASE_URL")
        missing = [n for n, v in (("SPOOLMAN_URL", spoolman_url), ("SPOOLEASE_URL", spoolease_url)) if not v]
        if missing:
            raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")
        assert spoolman_url and spoolease_url  # for type-checkers
        return Config(
            spoolman_url=spoolman_url.rstrip("/"),
            spoolease_url=spoolease_url.rstrip("/"),
            spoolease_token=_get("SPOOLEASE_TOKEN"),
            spoolease_mode=_get("SPOOLEASE_MODE", "json") or "json",
            spoolease_security_key=_get("SPOOLEASE_SECURITY_KEY"),
            default_diameter=_get_float("DEFAULT_DIAMETER", 1.75),
            default_density=_get_float("DEFAULT_DENSITY", 1.24),  # PLA-ish fallback
            poll_interval_seconds=_get_int("SYNC_INTERVAL_SECONDS", 30),
            dry_run=_get_bool("DRY_RUN", False),
            archive_missing=_get_bool("ARCHIVE_MISSING", True),
            state_path=_get("STATE_PATH", "/state/spoolsync.sqlite") or "/state/spoolsync.sqlite",
            log_level=_get("LOG_LEVEL", "INFO") or "INFO",
            two_way_sync=_get_bool("TWO_WAY_SYNC", False),  # Phase 2: disabled by default
        )
