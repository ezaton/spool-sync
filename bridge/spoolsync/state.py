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
        # Phase 2: Echo suppression tracking
        # Track recent writes to prevent syncing back our own changes
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS recent_writes (
                spoolman_spool_id INTEGER NOT NULL,
                timestamp         REAL NOT NULL,
                direction         TEXT NOT NULL,  -- 'to_spoolman' or 'to_spoolease'
                content_snapshot  TEXT NOT NULL   -- JSON of key fields to compare
            )
            """
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_recent_writes_spool ON recent_writes(spoolman_spool_id)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_recent_writes_time ON recent_writes(timestamp)")
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

    # --- Phase 2: Echo suppression methods ---
    def record_write_to_spoolman(self, spoolman_spool_id: int, content_snapshot: dict) -> None:
        """Track a write we made to Spoolman to suppress echo events."""
        import json
        import time
        self._db.execute(
            "INSERT INTO recent_writes (spoolman_spool_id, timestamp, direction, content_snapshot) VALUES (?, ?, ?, ?)",
            (spoolman_spool_id, time.time(), "to_spoolman", json.dumps(content_snapshot)),
        )
        self._db.commit()
        # Clean up old entries (older than 60 seconds)
        self._db.execute("DELETE FROM recent_writes WHERE timestamp < ?", (time.time() - 60,))
        self._db.commit()

    def record_write_to_spoolease(self, spoolman_spool_id: int, content_snapshot: dict) -> None:
        """Track a write we made to SpoolEase to suppress echo events."""
        import json
        import time
        self._db.execute(
            "INSERT INTO recent_writes (spoolman_spool_id, timestamp, direction, content_snapshot) VALUES (?, ?, ?, ?)",
            (spoolman_spool_id, time.time(), "to_spoolease", json.dumps(content_snapshot)),
        )
        self._db.commit()
        self._db.execute("DELETE FROM recent_writes WHERE timestamp < ?", (time.time() - 60,))
        self._db.commit()

    def is_echo_from_spoolman(self, spoolman_spool_id: int, current_content: dict, window_seconds: float = 30.0) -> bool:
        """Check if this Spoolman change is an echo of our recent write.

        Returns True if we recently wrote to Spoolman with matching content.
        """
        import json
        import time
        cutoff = time.time() - window_seconds
        cur = self._db.execute(
            "SELECT content_snapshot FROM recent_writes WHERE spoolman_spool_id = ? AND direction = 'to_spoolman' AND timestamp > ? ORDER BY timestamp DESC LIMIT 1",
            (spoolman_spool_id, cutoff),
        )
        r = cur.fetchone()
        if r is None:
            return False
        # Compare key fields to see if this matches what we just wrote
        recorded = json.loads(r["content_snapshot"])
        return self._content_matches(recorded, current_content)

    def is_echo_from_spoolease(self, spoolman_spool_id: int, current_content: dict, window_seconds: float = 30.0) -> bool:
        """Check if this SpoolEase change is an echo of our recent write-back.

        Returns True if we recently wrote to SpoolEase with matching content.
        """
        import json
        import time
        cutoff = time.time() - window_seconds
        cur = self._db.execute(
            "SELECT content_snapshot FROM recent_writes WHERE spoolman_spool_id = ? AND direction = 'to_spoolease' AND timestamp > ? ORDER BY timestamp DESC LIMIT 1",
            (spoolman_spool_id, cutoff),
        )
        r = cur.fetchone()
        if r is None:
            return False
        recorded = json.loads(r["content_snapshot"])
        return self._content_matches(recorded, current_content)

    def _content_matches(self, recorded: dict, current: dict) -> bool:
        """Compare key fields to determine if content is essentially the same."""
        # Compare only the fields we track for echo suppression
        for key in ["location", "price", "archived", "comment", "remaining_weight"]:
            if key in recorded or key in current:
                if recorded.get(key) != current.get(key):
                    return False
        return True

    def get_spoolease_id_by_spoolman_id(self, spoolman_spool_id: int) -> str | None:
        """Reverse lookup: Spoolman spool ID → SpoolEase ID."""
        cur = self._db.execute("SELECT spoolease_id FROM shadow WHERE spoolman_spool_id = ?", (spoolman_spool_id,))
        r = cur.fetchone()
        return r["spoolease_id"] if r else None
