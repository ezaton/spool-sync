"""Entrypoint: `python -m spoolsync`."""

from __future__ import annotations

import asyncio

from .config import Config
from .sync import run
from .sync_twoway import run_twoway


def main() -> None:
    cfg = Config.from_env()

    if cfg.two_way_sync:
        # Phase 2: Run two-way sync with websocket listener
        asyncio.run(run_twoway(cfg))
    else:
        # Phase 1: Run one-way sync (polling only)
        run(cfg)


if __name__ == "__main__":
    main()
