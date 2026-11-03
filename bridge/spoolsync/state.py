"""Sync bookkeeping — the one place that remembers what was synced.

This is exactly the "neutral third store" the bridge exists to own (see ../decision.md):
the id map (SpoolEase id <-> Spoolman spool id) and a content hash of each record's
last-synced state, so a poll can tell created/changed/unchanged/removed apart without any
`updated_at` from SpoolEase. SQLite file, created on first use.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ShadowRow:
    spoolease_id: str
    content_hash: str
    spoolman_spool_id: int | None
    archived: bool


class State:
    def __init__(self, path: str) -> None:
        p = Path(path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(p))
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow (
                spoolease_id      TEXT PRIMARY KEY,
                content_hash      TEXT NOT NULL,
                spoolman_spool_id INTEGER,
                archived          INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._db.commit()

    def get(self, spoolease_id: str) -> ShadowRow | None:
        cur = self._db.execute("SELECT * FROM shadow WHERE spoolease_id = ?", (spoolease_id,))
        r = cur.fetchone()
        if r is None:
            return None
        return ShadowRow(r["spoolease_id"], r["content_hash"], r["spoolman_spool_id"], bool(r["archived"]))

    def upsert(self, row: ShadowRow) -> None:
        self._db.execute(
            """
            INSERT INTO shadow (spoolease_id, content_hash, spoolman_spool_id, archived)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(spoolease_id) DO UPDATE SET
                content_hash = excluded.content_hash,
                spoolman_spool_id = excluded.spoolman_spool_id,
                archived = excluded.archived
            """,
            (row.spoolease_id, row.content_hash, row.spoolman_spool_id, int(row.archived)),
        )
        self._db.commit()

    def all_ids(self) -> set[str]:
        cur = self._db.execute("SELECT spoolease_id FROM shadow")
        return {r["spoolease_id"] for r in cur.fetchall()}

    def close(self) -> None:
        self._db.close()
