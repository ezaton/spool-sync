"""Phase 2: Two-way sync orchestration with websocket listener.

Runs two concurrent sync loops:
1. SpoolEase → Spoolman (polling, same as phase 1)
2. Spoolman → SpoolEase (websocket-driven, real-time)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from .config import Config
from .reconcile import reconcile_once
from .reconcile_sm_to_se import handle_spoolman_event
from .spoolease import SpooleaseClient
from .spoolman import SpoolmanClient
from .state import State

log = logging.getLogger("spoolsync")


class _Stop:
    def __init__(self) -> None:
        self.flag = False
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, *_: object) -> None:
        log.info("shutdown requested")
        self.flag = True


async def run_twoway(cfg: Config) -> None:
    """Run two-way sync with concurrent polling and websocket listening."""
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(name)-22s %(levelname)-7s %(message)s")
    log.info("spoolsync TWO-WAY starting: SpoolEase=%s <-> Spoolman=%s (mode=%s, interval=%ss, dry_run=%s)",
             cfg.spoolease_url, cfg.spoolman_url, cfg.spoolease_mode, cfg.poll_interval_seconds, cfg.dry_run)

    state = State(cfg.state_path)
    sm = SpoolmanClient(cfg.spoolman_url, dry_run=cfg.dry_run)
    se = SpooleaseClient(cfg.spoolease_url, mode=cfg.spoolease_mode, token=cfg.spoolease_token,
                         security_key=cfg.spoolease_security_key)

    stop = _Stop()

    try:
        # Initialize
        sm.ensure_spoolease_id_field()
        density_map = sm.external_material_densities()
        log.info("loaded %d material densities from Spoolman external DB", len(density_map))

        # Create event handler callback for websocket
        async def on_spoolman_event(event: dict) -> None:
            await handle_spoolman_event(event, se, state, dry_run=cfg.dry_run)

        # Run both sync loops concurrently
        await asyncio.gather(
            _poll_spoolease_to_spoolman(se, sm, state, cfg, density_map, stop),
            _listen_spoolman_to_spoolease(sm, on_spoolman_event, stop),
        )

    finally:
        se.close()
        sm.close()
        state.close()
        log.info("spoolsync stopped")


async def _poll_spoolease_to_spoolman(se: SpooleaseClient, sm: SpoolmanClient, state: State,
                                      cfg: Config, density_map: dict[str, float], stop: _Stop) -> None:
    """SpoolEase → Spoolman polling loop (phase 1 direction)."""
    while not stop.flag:
        t0 = time.monotonic()
        try:
            stats = reconcile_once(se, sm, state, cfg, density_map)
            log.info("SE→SM reconcile: %s", stats)
        except Exception:
            log.exception("SE→SM reconcile pass failed; will retry next interval")

        # Interruptible sleep
        elapsed = time.monotonic() - t0
        remaining = max(0.0, cfg.poll_interval_seconds - elapsed)
        while remaining > 0 and not stop.flag:
            await asyncio.sleep(min(1.0, remaining))
            remaining -= 1.0


async def _listen_spoolman_to_spoolease(sm: SpoolmanClient, on_event_callback, stop: _Stop) -> None:
    """Spoolman → SpoolEase websocket listener (phase 2 direction)."""
    try:
        await sm.listen_websocket(on_event_callback)
    except asyncio.CancelledError:
        log.info("websocket listener cancelled")
        raise
    except Exception:
        log.exception("websocket listener failed")
        stop.flag = True
