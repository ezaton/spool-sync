"""SpoolEase source connector — reads all SpoolRecords over HTTP.

Two modes:
  * "json" (recommended): GET {url}/api/spools.json -> [ {record}, ... ]. Needs the small
    SpoolEase endpoint (see ../spoolease-endpoints.md). Clean, stable contract.
  * "encrypted": read the stock AES-GCM /api/spools blob with the device security key.
    NOT implemented in phase 1 — it couples to SpoolEase's internal CSV + crypto. The hook
    is here so you can add it without touching the rest of the bridge.

Writing back to SpoolEase (phase 2) will add a `write_record()` here against the matching
POST endpoint; it is intentionally omitted while phase 1 is one-way.
"""

from __future__ import annotations

import logging

import httpx

from .models import SpoolRecord

log = logging.getLogger("spoolsync.spoolease")


class SpooleaseClient:
    def __init__(self, base_url: str, *, mode: str = "json", token: str | None = None,
                 security_key: str | None = None, timeout: float = 20.0) -> None:
        self._base = base_url.rstrip("/")
        self._mode = mode
        self._token = token
        self._security_key = security_key
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(base_url=self._base, timeout=timeout, headers=headers)

    def close(self) -> None:
        self._http.close()

    def fetch_records(self) -> list[SpoolRecord]:
        if self._mode == "json":
            return self._fetch_json()
        if self._mode == "encrypted":
            return self._fetch_encrypted()
        raise ValueError(f"Unknown SPOOLEASE_MODE: {self._mode!r}")

    def _fetch_json(self) -> list[SpoolRecord]:
        r = self._http.get("/api/spools.json")
        r.raise_for_status()
        data = r.json()
        # Accept either a bare list or {"spools": [...]}.
        rows = data["spools"] if isinstance(data, dict) and "spools" in data else data
        records = [SpoolRecord.from_json(obj) for obj in rows if obj.get("id")]
        log.debug("fetched %d SpoolEase records", len(records))
        return records

    def _fetch_encrypted(self) -> list[SpoolRecord]:
        # Intentionally unimplemented in phase 1. To implement:
        #   1. GET /api/spools -> ciphertext (AES-GCM).
        #   2. key = PBKDF2-HMAC-SHA256(security_key, salt="example_salt", iters=10000).
        #   3. plaintext = AES-GCM decrypt -> CSV rows of SpoolRecord (note: floats are
        #      base64-encoded and bools are "y"/"n" via SpoolEase's serde adapters).
        #   4. Parse those adapters, then build SpoolRecord objects.
        # Prefer adding the JSON endpoint instead — far less coupling.
        raise NotImplementedError(
            "encrypted mode is not implemented; add the SpoolEase JSON endpoint and use SPOOLEASE_MODE=json "
            "(see ../spoolease-endpoints.md)"
        )
