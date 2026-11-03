"""The SpoolEase record as the bridge consumes it.

The bridge targets a CLEAN JSON contract (one object per spool, plain field types),
which the small SpoolEase `GET /api/spools.json` endpoint is expected to emit. This is
deliberately NOT the stock `/api/spools` payload: that one is AES-GCM-encrypted and, worse,
serializes some fields through SpoolEase's tag-encoding serde adapters (floats as base64,
bools as "y"/"n"), so it is awkward to parse. The endpoint changeset
(../spoolease-endpoints.md) serializes a plain DTO instead.

Field names mirror `SpoolEase/core/src/spool_record.rs::SpoolRecord`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SpoolRecord:
    id: str
    tag_id: str = ""
    material_type: str = ""
    material_subtype: str = ""
    color_name: str = ""
    color_code: str = ""  # RRGGBBAA hex (8 chars) as SpoolEase stores it
    note: str = ""
    brand: str = ""
    weight_advertised: int | None = None  # nominal NET full weight (g)
    weight_core: int | None = None  # empty core/tare (g)
    weight_new: int | None = None  # GROSS at registration (g)
    weight_current: int | None = None  # GROSS current scale reading (g)
    slicer_filament: str = ""  # e.g. base-filament code "GFA01"
    added_time: int | None = None  # epoch seconds
    encode_time: int | None = None  # epoch seconds
    added_full: bool | None = None
    consumed_since_add: float = 0.0  # printed since added (g)
    consumed_since_weight: float = 0.0  # printed since last weighing (g)
    ext_has_k: bool = False  # has pressure-advance (K) calibration
    data_origin: str = ""
    tag_type: str = ""

    # Fields present in the JSON but not modeled above are ignored on parse.
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "SpoolRecord":
        known = {f for f in cls.__dataclass_fields__ if not f.startswith("_")}
        kwargs = {k: v for k, v in obj.items() if k in known}
        extra = {k: v for k, v in obj.items() if k not in known}
        rec = cls(**kwargs)  # type: ignore[arg-type]
        rec._extra = extra
        return rec

    def has_valid_tag(self) -> bool:
        """Mirror SpoolRecord::has_valid_tag_id — a '-' prefix marks a retired tag."""
        return bool(self.tag_id) and not self.tag_id.startswith("-")

    def content_hash(self) -> str:
        """Stable hash of the record's syncable content.

        SpoolEase has no `updated_at`, so change detection is by content hash. We hash the
        full record (minus bookkeeping-only fields that don't affect Spoolman) so any edit
        that matters triggers a re-sync and nothing else does.
        """
        payload = {k: v for k, v in asdict(self).items() if k != "_extra"}
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
