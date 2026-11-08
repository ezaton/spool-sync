"""Pure mapping: a flat SpoolEase SpoolRecord -> Spoolman Vendor / Filament / Spool specs.

No I/O here — these functions are deterministic and unit-tested (see tests/test_mapper.py).
The weight logic follows the semantics confirmed in SpoolEase/core/src/view_model.rs
(`weight_left_spool`); see ../data-mapping.md#weight-semantics-resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import SpoolRecord


@dataclass(frozen=True)
class VendorSpec:
    name: str
    external_id: str
    empty_spool_weight: float | None


@dataclass(frozen=True)
class FilamentSpec:
    external_id: str
    name: str | None
    material: str | None
    color_hex: str | None
    weight: float | None  # nominal full NET weight (g)
    spool_weight: float | None  # tare (g)
    density: float  # REQUIRED by Spoolman (>0)
    diameter: float  # REQUIRED by Spoolman (>0)
    article_number: str | None
    settings_extruder_temp: int | None
    settings_bed_temp: int | None


@dataclass(frozen=True)
class SpoolSpec:
    spoolease_id: str
    tag_uid: str | None  # link if present & valid
    tag_format: str | None
    initial_weight: float | None  # net full (g)
    spool_weight: float | None  # tare (g)
    remaining_weight: float | None  # net remaining (g) — Spoolman derives used_weight
    comment: str | None
    extra: dict[str, str]  # JSON-encoded values, per Spoolman's extra-field contract


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")


def _norm_hex(color_code: str) -> str | None:
    """SpoolEase stores RRGGBBAA; Spoolman accepts 6 or 8 hex chars, '#' stripped."""
    h = color_code.strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", h) or re.fullmatch(r"[0-9a-fA-F]{8}", h):
        return h.upper()
    return None


def vendor_external_id(brand: str) -> str:
    return f"se-brand:{_slug(brand)}"


def map_vendor(rec: SpoolRecord) -> VendorSpec | None:
    if not rec.brand.strip():
        return None
    return VendorSpec(
        name=rec.brand.strip(),
        external_id=vendor_external_id(rec.brand),
        empty_spool_weight=float(rec.weight_core) if rec.weight_core is not None else None,
    )


def filament_external_id(rec: SpoolRecord) -> str:
    """Stable identity for the shared filament *type*.

    Uses SpoolEase's base-filament code plus color, since the same product code (e.g. 'GFSNL08')
    can have multiple color variants. Falls back to synthesizing from distinguishing attributes.
    """
    if rec.slicer_filament.strip():
        # Include color to distinguish variants (e.g., GFSNL08 comes in red, black, etc.)
        color = _norm_hex(rec.color_code) or ""
        return f"se-fil:{rec.slicer_filament.strip()}:{color}"
    key = "|".join([_slug(rec.brand), _slug(rec.material_type), _slug(rec.material_subtype), (_norm_hex(rec.color_code) or "")])
    return f"se-fil:{key}"


def map_material(rec: SpoolRecord) -> str | None:
    # Convention: keep subtype out of `material` (so "PLA", not "PLA Matte"); subtype goes
    # into the filament name. See open question #3 in ../data-mapping.md — flip here if you
    # prefer subtype in material.
    return rec.material_type.strip() or None


def map_filament_name(rec: SpoolRecord) -> str | None:
    parts = [p for p in (rec.material_subtype.strip(), rec.color_name.strip()) if p]
    name = " ".join(parts).strip()
    return name or None


def map_filament(rec: SpoolRecord, *, density: float, diameter: float) -> FilamentSpec:
    return FilamentSpec(
        external_id=filament_external_id(rec),
        name=map_filament_name(rec),
        material=map_material(rec),
        color_hex=_norm_hex(rec.color_code),
        weight=float(rec.weight_advertised) if rec.weight_advertised is not None else None,
        spool_weight=_tare(rec),
        density=density,
        diameter=diameter,
        article_number=rec.slicer_filament.strip() or None,
        settings_extruder_temp=None,  # could be filled from materials.csv; left None in phase 1
        settings_bed_temp=None,
    )


def _tare(rec: SpoolRecord) -> float | None:
    """Empty core/spool weight. Direct if known, else gross-at-reg minus net-full."""
    if rec.weight_core is not None:
        return float(rec.weight_core)
    if rec.weight_new is not None and rec.weight_advertised is not None:
        return float(rec.weight_new - rec.weight_advertised)
    return None


def compute_remaining(rec: SpoolRecord) -> tuple[float | None, float | None]:
    """Return (initial_net, remaining_net) in grams, per SpoolEase's own formula.

    weight_current is a GROSS scale reading; weight_advertised is nominal NET full.
      - weighed:      remaining = weight_current - tare - consumed_since_weight
      - never weighed: remaining = weight_advertised - consumed_since_add
    """
    tare = _tare(rec)
    initial = float(rec.weight_advertised) if rec.weight_advertised is not None else None

    remaining: float | None = None
    if rec.weight_current is not None and tare is not None:
        remaining = (rec.weight_current - tare) - rec.consumed_since_weight
    elif rec.weight_advertised is not None:
        remaining = rec.weight_advertised - rec.consumed_since_add

    if remaining is not None:
        remaining = max(0.0, round(remaining, 1))
    return initial, remaining


def tag_format(rec: SpoolRecord) -> str | None:
    origin = (rec.data_origin or rec.tag_type or "").lower()
    if "bambu" in origin:
        return "bambu"
    if "openprint" in origin or "opt" in origin:
        return "openprinttag"
    if origin:
        return origin
    return None


def map_spool(rec: SpoolRecord) -> SpoolSpec:
    initial, remaining = compute_remaining(rec)
    # Only `spoolease_id` is set here because it's the only extra field the bridge registers
    # (Spoolman rejects values for unregistered extra fields). To also mirror K calibration,
    # register a `spoolease_has_k` boolean field at startup and add it here.
    extra = {"spoolease_id": _json_str(rec.id)}
    return SpoolSpec(
        spoolease_id=rec.id,
        tag_uid=rec.tag_id if rec.has_valid_tag() else None,
        tag_format=tag_format(rec),
        initial_weight=initial,
        spool_weight=_tare(rec),
        remaining_weight=remaining,
        comment=rec.note.strip() or None,
        extra=extra,
    )


def _json_str(value: str) -> str:
    """Spoolman's `extra` values are JSON-encoded strings (text -> a quoted JSON string)."""
    import json

    return json.dumps(value)
