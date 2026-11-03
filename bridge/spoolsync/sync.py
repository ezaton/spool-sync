"""Loop orchestration: wire up clients + state, then reconcile on an interval."""

from __future__ import annotations

import logging
import signal
import time

from .config import Config
from .reconcile import reconcile_once
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


def run(cfg: Config) -> None:
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(name)-22s %(levelname)-7s %(message)s")
    log.info("spoolsync starting: SpoolEase=%s -> Spoolman=%s (mode=%s, interval=%ss, dry_run=%s)",
             cfg.spoolease_url, cfg.spoolman_url, cfg.spoolease_mode, cfg.poll_interval_seconds, cfg.dry_run)

    state = State(cfg.state_path)
    sm = SpoolmanClient(cfg.spoolman_url, dry_run=cfg.dry_run)
    se = SpooleaseClient(cfg.spoolease_url, mode=cfg.spoolease_mode, token=cfg.spoolease_token,
                         security_key=cfg.spoolease_security_key)

    stop = _Stop()
    try:
        sm.ensure_spoolease_id_field()
        density_map = sm.external_material_densities()
        log.info("loaded %d material densities from Spoolman external DB", len(density_map))

        while not stop.flag:
            t0 = time.monotonic()
            try:
                stats = reconcile_once(se, sm, state, cfg, density_map)
                log.info("reconcile: %s", stats)
            except Exception:
                log.exception("reconcile pass failed; will retry next interval")
            # interruptible sleep
            elapsed = time.monotonic() - t0
            remaining = max(0.0, cfg.poll_interval_seconds - elapsed)
            while remaining > 0 and not stop.flag:
                time.sleep(min(1.0, remaining))
                remaining -= 1.0
    finally:
        se.close()
        sm.close()
        state.close()
        log.info("spoolsync stopped")
