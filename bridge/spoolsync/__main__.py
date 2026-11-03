"""Entrypoint: `python -m spoolsync`."""

from __future__ import annotations

from .config import Config
from .sync import run


def main() -> None:
    run(Config.from_env())


if __name__ == "__main__":
    main()
