"""Spoolman REST client — idempotent upserts over the public /api/v1 API.

Spoolman requires NO auth for a non-browser client (it sends no Origin header), so this is
a plain JSON client. See the endpoint map in ../architecture.md and ../data-mapping.md.
Nothing here modifies Spoolman.
"""

from __future__ import annotations

import logging

import httpx

from .mapper import FilamentSpec, SpoolSpec, VendorSpec

log = logging.getLogger("spoolsync.spoolman")


class SpoolmanClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0, dry_run: bool = False) -> None:
        self._base = base_url.rstrip("/")
        self._dry = dry_run
        self._http = httpx.Client(base_url=self._base, timeout=timeout, headers={"Content-Type": "application/json"})

    def close(self) -> None:
        self._http.close()

    # --- low-level ---
    def _get(self, path: str, **params) -> httpx.Response:
        r = self._http.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r

    def _write(self, method: str, path: str, json_body: dict) -> dict | None:
        if self._dry:
            log.info("[dry-run] %s %s %s", method, path, json_body)
            return None
        r = self._http.request(method, path, json=json_body)
        r.raise_for_status()
        return r.json() if r.content else None

    # --- reference data ---
    def external_material_densities(self) -> dict[str, float]:
        """material(lowercased) -> density, from Spoolman's own external material catalog.

        Used to fill Spoolman's REQUIRED Filament.density, which SpoolEase doesn't store.
        Best-effort: an empty map just means the configured DEFAULT_DENSITY is used.
        """
        try:
            rows = self._get("/external/material").json()
        except httpx.HTTPError as e:
            log.warning("could not load /external/material (%s); using default density", e)
            return {}
        out: dict[str, float] = {}
        for m in rows:
            name = (m.get("material") or "").strip().lower()
            density = m.get("density")
            if name and density:
                out[name] = float(density)
        return out

    # --- one-time setup ---
    def ensure_spoolease_id_field(self) -> None:
        """Register the `spoolease_id` extra field on spools (idempotent)."""
        body = {"name": "SpoolEase ID", "field_type": "text", "order": 100}
        try:
            self._write("POST", "/field/spool/spoolease_id", body)
        except httpx.HTTPStatusError as e:  # already exists / validation — non-fatal
            log.debug("ensure spoolease_id field: %s", e)

    # --- Vendor ---
    def upsert_vendor(self, spec: VendorSpec) -> int | None:
        existing = self._get("/vendor", external_id=spec.external_id).json()
        body = {
            "name": spec.name,
            "external_id": spec.external_id,
            "empty_spool_weight": spec.empty_spool_weight,
        }
        if existing:
            vid = existing[0]["id"]
            self._write("PATCH", f"/vendor/{vid}", body)
            return vid
        created = self._write("POST", "/vendor", body)
        return created["id"] if created else None

    # --- Filament ---
    def upsert_filament(self, spec: FilamentSpec, vendor_id: int | None) -> int | None:
        existing = self._get("/filament", external_id=spec.external_id).json()
        body = {
            "name": spec.name,
            "vendor_id": vendor_id,
            "material": spec.material,
            "color_hex": spec.color_hex,
            "weight": spec.weight,
            "spool_weight": spec.spool_weight,
            "density": spec.density,
            "diameter": spec.diameter,
            "article_number": spec.article_number,
            "external_id": spec.external_id,
            "settings_extruder_temp": spec.settings_extruder_temp,
            "settings_bed_temp": spec.settings_bed_temp,
        }
        body = {k: v for k, v in body.items() if v is not None}
        if existing:
            fid = existing[0]["id"]
            self._write("PATCH", f"/filament/{fid}", body)
            return fid
        created = self._write("POST", "/filament", body)
        return created["id"] if created else None

    # --- Spool ---
    def find_spool_id(self, spec: SpoolSpec) -> int | None:
        """Resolve an existing spool: by tag UID first, then by extra.spoolease_id.

        Only used as a cold-start fallback (no shadow yet); the shadow store normally
        supplies the spool id. Best-effort — returns None on any lookup trouble so the
        caller creates a fresh spool rather than crashing.
        """
        try:
            if spec.tag_uid:
                hits = self._get("/spool", tag=spec.tag_uid, allow_archived="true").json()
                if hits:
                    return hits[0]["id"]
            # extra text values are JSON-encoded in Spoolman, so the stored value is a quoted
            # string; an exact match is requested by wrapping the query value in quotes.
            params = {"extra.spoolease_id": f'"{spec.spoolease_id}"', "allow_archived": "true"}
            hits = self._get("/spool", **params).json()
            return hits[0]["id"] if hits else None
        except httpx.HTTPError as e:
            log.warning("spool lookup failed for spoolease_id=%s (%s); treating as new", spec.spoolease_id, e)
            return None

    def upsert_spool(self, spec: SpoolSpec, filament_id: int, existing_id: int | None) -> int | None:
        body: dict = {
            "filament_id": filament_id,
            "initial_weight": spec.initial_weight,
            "spool_weight": spec.spool_weight,
            "comment": spec.comment,
            "extra": spec.extra,
            "archived": False,
        }
        if spec.remaining_weight is not None:
            body["remaining_weight"] = spec.remaining_weight  # Spoolman derives used_weight
        body = {k: v for k, v in body.items() if v is not None}

        if existing_id is not None:
            self._write("PATCH", f"/spool/{existing_id}", body)
            spool_id = existing_id
        else:
            created = self._write("POST", "/spool", body)
            spool_id = created["id"] if created else None

        if spool_id is not None and spec.tag_uid:
            self._link_tag(spool_id, spec.tag_uid, spec.tag_format)
        return spool_id

    def _link_tag(self, spool_id: int, uid: str, fmt: str | None) -> None:
        try:
            self._write("POST", f"/spool/{spool_id}/tag", {"uid": uid, "format": fmt})
        except httpx.HTTPStatusError as e:
            # 409 = tag already linked (to this or another spool); 201 handled by raise_for_status.
            if e.response is not None and e.response.status_code == 409:
                log.debug("tag %s already linked: %s", uid, e.response.text)
            else:
                raise

    def archive_spool(self, spool_id: int) -> None:
        self._write("PATCH", f"/spool/{spool_id}", {"archived": True})
