"""Phase 2: Two-way sync orchestration with websocket listener.

Runs two concurrent sync loops:
1. SpoolEase → Spoolman (polling, same as phase 1)
2. Spoolman → SpoolEase (websocket-driven, real-time)

Startup sequence (Phase B):
1. Connect to Spoolman websocket (start listening immediately)
2. Queue incoming events in a thread-safe buffer
3. Poll SpoolEase → populate baseline
4. Poll Spoolman → populate baseline
5. Diff both → populate shadow store (SQLite)
6. Drain queued events → apply changes that arrived during scan
7. Enter normal operation: process websocket events in real-time
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
    """Run two-way sync with concurrent polling and websocket listening.

    Implements a startup sequence that prevents noisy "no mapping" warnings:
    1. Connect websocket (listen immediately)
    2. Queue events during initial scan
    3. Poll SpoolEase → populate baseline
    4. Poll Spoolman → populate baseline
    5. Diff both → populate shadow store
    6. Drain queued events
    7. Normal operation (direct processing)
    """
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(name)-22s %(levelname)-7s %(message)s")
    log.info("spoolsync TWO-WAY starting: SpoolEase=%s <-> Spoolman=%s (mode=%s, interval=%ss, dry_run=%s, verbose=%s)",
             cfg.spoolease_url, cfg.spoolman_url, cfg.spoolease_mode, cfg.poll_interval_seconds, cfg.dry_run, cfg.verbose)

    state = State(cfg.state_path)
    sm = SpoolmanClient(cfg.spoolman_url, dry_run=cfg.dry_run)
    se = SpooleaseClient(cfg.spoolease_url, mode=cfg.spoolease_mode, token=cfg.spoolease_token,
                         security_key=cfg.spoolease_security_key)

    stop = _Stop()

    try:
        # Initialize
        sm.ensure_spoolease_id_field()
        sm.ensure_variant_field()  # variant custom field for filaments
        density_map = sm.external_material_densities()
        log.info("loaded %d material densities from Spoolman external DB", len(density_map))

        # --- Migration phase (optional, one-time, before websocket listener) ---
        if cfg.migration:
            base_url = cfg.spoolman_url.removesuffix("/api/v1")
            log.info("running variant migration (pre-startup, websocket inactive)")
            from .migrate_variant import migrate_filaments
            migrated, skipped, errors = migrate_filaments(
                base_url=base_url,
                dry_run=cfg.dry_run,
            )
            log.info("variant migration complete: migrated=%d, skipped=%d, errors=%d",
                     migrated, skipped, errors)
            if errors > 0:
                log.warning("migration had %d errors — continuing anyway", errors)

        # --- Startup sequence with queue drain ---
        await _startup_sequence(se, sm, state, cfg, density_map, stop)

        # --- Normal operation: both loops running concurrently ---
        # Create event handler callback for websocket (direct processing, no queue)
        async def on_spoolman_event(event: dict) -> None:
            await handle_spoolman_event(event, se, state, cfg=cfg, dry_run=cfg.dry_run)

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


async def _startup_sequence(se: SpooleaseClient, sm: SpoolmanClient, state: State,
                            cfg: Config, density_map: dict[str, float], stop: _Stop) -> None:
    """Execute the startup sequence: connect websocket, queue events, build baseline, drain.

    Steps:
    1. Start websocket listener in background (with event queueing)
    2. Run initial reconcile pass (SpoolEase → Spoolman)
    3. Run initial Spoolman baseline scan
    4. Drain queued events
    5. Log completion and transition to normal operation
    """
    log.info("starting startup sequence: building baseline shadow store")

    # Step 1: Create a queue for events during startup
    event_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
    startup_done = asyncio.Event()

    # Step 2: Start websocket listener in background, queuing events during startup
    async def _startup_websocket_listener() -> None:
        """Websocket listener that queues events during startup, then processes directly."""
        try:
            import json
            import websockets

            ws_url = sm._base.replace("http://", "ws://").replace("https://", "wss://") + "/spool"
            retry_delay = 1.0
            max_retry_delay = 60.0

            while not stop.flag:
                try:
                    log.info("[startup] connecting to Spoolman websocket: %s", ws_url)
                    async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as websocket:
                        log.info("[startup] websocket connected, queuing events during baseline scan...")
                        retry_delay = 1.0

                        async for message in websocket:
                            if stop.flag:
                                return
                            try:
                                event = json.loads(message)
                                if startup_done.is_set():
                                    # Startup complete: process directly
                                    await handle_spoolman_event(event, se, state, cfg=cfg, dry_run=cfg.dry_run)
                                else:
                                    # Still in startup: queue the event
                                    try:
                                        event_queue.put_nowait(event)
                                    except asyncio.QueueFull:
                                        log.warning("[startup] event queue full, dropping oldest event")
                                        # Remove oldest to make room
                                        try:
                                            event_queue.get_nowait()
                                            event_queue.put_nowait(event)
                                        except asyncio.QueueEmpty:
                                            pass
                            except json.JSONDecodeError:
                                log.warning("[startup] received non-JSON websocket message, skipping")
                            except Exception:
                                log.exception("[startup] error processing websocket event")
                except websockets.exceptions.ConnectionClosed:
                    log.warning("[startup] websocket connection closed, reconnecting in %ss", retry_delay)
                except (OSError, ConnectionError) as e:
                    log.warning("[startup] websocket connection error: %s, reconnecting in %ss", e, retry_delay)
                except asyncio.CancelledError:
                    log.info("[startup] websocket listener cancelled")
                    return
                except Exception:
                    log.exception("[startup] websocket listener error, reconnecting in %ss", retry_delay)

                if not stop.flag:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)

        except asyncio.CancelledError:
            log.info("[startup] websocket listener cancelled")
            raise
        except Exception:
            log.exception("[startup] websocket listener failed")
            stop.flag = True

    ws_task = asyncio.create_task(_startup_websocket_listener())

    # Step 3: Run initial reconcile pass (SpoolEase → Spoolman)
    # This populates the shadow store with SpoolEase data
    log.info("[startup] running initial SpoolEase → Spoolman reconcile pass")
    try:
        stats = reconcile_once(se, sm, state, cfg, density_map)
        log.info("[startup] initial SE→SM reconcile: %s", stats)
    except Exception:
        log.exception("[startup] initial SE→SM reconcile pass failed")

    # Step 4: Run initial Spoolman baseline scan
    # This populates the shadow store with Spoolman data that may not be in SpoolEase
    log.info("[startup] running initial Spoolman baseline scan")
    try:
        stats = await _scan_spoolman_baseline(sm, state, cfg)
        log.info("[startup] initial SM baseline scan: %s", stats)
    except Exception:
        log.exception("[startup] initial SM baseline scan failed")

    # Step 5: Signal that startup is complete
    startup_done.set()
    log.info("[startup] baseline complete, switching websocket listener to direct processing")

    # Step 6: Drain queued events
    drained_count = 0
    while not event_queue.empty():
        try:
            event = event_queue.get_nowait()
            await handle_spoolman_event(event, se, state, cfg=cfg, dry_run=cfg.dry_run)
            drained_count += 1
        except asyncio.QueueEmpty:
            break

    log.info("[startup] startup sequence complete: drained %d queued events", drained_count)

    # Step 7: Cancel the startup websocket listener (normal listener will take over)
    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("[startup] startup websocket listener error on cancel")


async def _scan_spoolman_baseline(sm: SpoolmanClient, state: State, cfg: Config) -> dict[str, int]:
    """Scan Spoolman's current spool data and populate the shadow store.

    This ensures the shadow store has entries for Spoolman-only spools (those
    not originating from SpoolEase) so that echo suppression and reverse
    lookups work correctly from the start.

    Returns a stats dict with counts of scanned and placeholders entries.
    """
    stats = {"scanned": 0, "placeholders": 0}

    try:
        spools = sm.get_all_spools()
        stats["scanned"] = len(spools)

        for spool in spools:
            spoolman_id = spool.get("id")
            if not spoolman_id:
                continue

            # Only create placeholders for Spoolman-only spools (no external_id)
            # The reconcile pass will fill in the real spoolease_id for SpoolEase-originated spools
            external_id = spool.get("external_id", "")
            if not external_id.startswith("se-fil:"):
                # Spoolman-only spool — create a placeholder
                shadow = state.get_by_spoolman_id(spoolman_id)
                if shadow is None:
                    state.upsert_placeholder(spoolman_id)
                    stats["placeholders"] += 1
                    if cfg.verbose:
                        log.debug("[SM→SE] baseline placeholder: spoolman_id=%d (no SpoolEase mapping)", spoolman_id)

    except Exception:
        log.exception("[startup] error scanning Spoolman baseline")

    return stats


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
