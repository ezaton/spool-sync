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
        """Decrypt and parse the stock AES-GCM /api/spools CSV."""
        if not self._security_key:
            raise ValueError("SPOOLEASE_SECURITY_KEY required for encrypted mode")

        import base64
        import csv
        import io
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        # Fetch encrypted blob (base64-encoded)
        r = self._http.get("/api/spools")
        r.raise_for_status()
        ciphertext = base64.b64decode(r.content)

        # Derive key: PBKDF2-HMAC-SHA256(security_key, "example_salt", 10000 iterations)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"example_salt",
            iterations=10000,
        )
        key = kdf.derive(self._security_key.encode("utf-8"))

        # Decrypt AES-GCM (first 12 bytes = nonce, rest = ciphertext + tag)
        aesgcm = AESGCM(key)
        nonce = ciphertext[:12]
        ct_and_tag = ciphertext[12:]
        plaintext = aesgcm.decrypt(nonce, ct_and_tag, None)

        # Parse CSV with adapted fields (base64 floats, y/n bools)
        # Note: CSV has NO header row, so we must specify field names manually
        csv_text = plaintext.decode("utf-8")
        field_names = [
            "id", "tag_id", "material_type", "material_subtype", "color_name", "color_code",
            "note", "brand", "weight_advertised", "weight_core", "weight_new", "weight_current",
            "slicer_filament", "added_time", "encode_time", "added_full",
            "consumed_since_add", "consumed_since_weight", "ext_has_k",
            "data_origin", "tag_type", "_unknown1", "_unknown2", "_unknown3"
        ]
        reader = csv.DictReader(io.StringIO(csv_text), fieldnames=field_names)

        records = []
        for row in reader:
            # Decode base64-encoded floats (consumed_since_add, consumed_since_weight)
            # Note: Empty strings mean 0.0, not base64
            for field in ["consumed_since_add", "consumed_since_weight"]:
                if field in row:
                    val = row[field].strip() if row[field] else ""
                    if not val:
                        row[field] = 0.0
                    else:
                        try:
                            # Try base64 decoding first
                            decoded = base64.b64decode(val)
                            import struct
                            row[field] = struct.unpack("<f", decoded)[0]
                        except Exception:
                            # If base64 fails, try as plain float
                            try:
                                row[field] = float(val)
                            except ValueError:
                                row[field] = 0.0

            # Decode y/n bools (added_full, ext_has_k)
            for field in ["added_full", "ext_has_k"]:
                if field in row:
                    row[field] = row[field].lower() in ("y", "yes", "true")

            # Convert integer fields (empty strings become None)
            for field in ["weight_advertised", "weight_core", "weight_new", "weight_current",
                         "added_time", "encode_time"]:
                if field in row:
                    val = row[field].strip() if row[field] else ""
                    if val:
                        try:
                            row[field] = int(val)
                        except ValueError:
                            row[field] = None
                    else:
                        row[field] = None

            if row.get("id"):
                records.append(SpoolRecord.from_json(row))

        log.debug("decrypted and parsed %d SpoolEase records", len(records))
        return records
