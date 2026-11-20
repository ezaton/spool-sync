"""Pure mapping: a flat SpoolEase SpoolRecord -> Spoolman Vendor / Filament / Spool specs.

No I/O here — these functions are deterministic and unit-tested (see tests/test_mapper.py).
The weight logic follows the semantics confirmed in SpoolEase/core/src/view_model.rs
(`weight_left_spool`); see ../data-mapping.md#weight-semantics-resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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
    extra: dict[str, str] = field(default_factory=dict)  # NEW


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
        color = _norm_hex(rec.color_code) or ""
        return f"se-fil:{rec.slicer_filament.strip()}:{color}"
    key = "|".join([_slug(rec.brand), _slug(rec.material_type), _slug(rec.material_subtype), (_norm_hex(rec.color_code) or "")])
    return f"se-fil:{key}"


def map_material(rec: SpoolRecord) -> str | None:
    # Convention: keep subtype out of `material` (so "PLA", not "PLA Matte"); subtype goes
    # into the filament name. See open question #3 in ../data-mapping.md — flip here if you
    # prefer subtype in material.
    return rec.material_type.strip() or None


def map_variant(rec: SpoolRecord) -> str | None:
    """Extract the variant/subtype from a SpoolEase record.

    Returns the material_subtype as-is if non-empty, else None.

    Examples:
        material_subtype="Matte" → "Matte"
        material_subtype="Silk"  → "Silk"
        material_subtype=""      → None
    """
    subtype = rec.material_subtype.strip()
    return subtype if subtype else None


def map_filament_name(rec: SpoolRecord) -> str | None:
    parts = [p for p in (rec.material_subtype.strip(), rec.color_name.strip()) if p]
    name = " ".join(parts).strip()
    return name or None


# ---------------------------------------------------------------------------
# Temperature mapping: SpoolEase nozzle temp range -> Spoolman single extruder temp
# ---------------------------------------------------------------------------

# Base filament codes -> (min_temp, max_temp) from base-filaments-index.csv
_BASE_FILAMENT_TEMPS: dict[str, tuple[int, int]] = {
    "GFA00": (190, 240),  # Bambu PLA Basic
    "GFA01": (190, 240),  # Bambu PLA Matte
    "GFA02": (190, 240),  # Bambu PLA Metal
    "GFA05": (190, 240),  # Bambu PLA Silk
    "GFA06": (190, 240),  # Bambu PLA Silk+
    "GFA07": (190, 240),  # Bambu PLA Marble
    "GFA08": (190, 240),  # Bambu PLA Sparkle
    "GFA09": (190, 240),  # Bambu PLA Tough
    "GFA11": (210, 260),  # Bambu PLA Aero
    "GFA12": (190, 240),  # Bambu PLA Glow
    "GFA13": (190, 240),  # Bambu PLA Dynamic
    "GFA15": (190, 240),  # Bambu PLA Galaxy
    "GFA16": (190, 240),  # Bambu PLA Wood
    "GFA17": (190, 240),  # Bambu PLA Translucent
    "GFA18": (190, 240),  # Bambu PLA Lite
    "GFA50": (210, 250),  # Bambu PLA-CF
    "GFB00": (240, 280),  # Bambu ABS
    "GFB01": (240, 280),  # Bambu ASA
    "GFB02": (240, 280),  # Bambu ASA-Aero
    "GFB50": (240, 280),  # Bambu ABS-GF
    "GFB51": (250, 280),  # Bambu ASA-CF
    "GFB60": (240, 280),  # PolyLite ABS
    "GFB61": (240, 280),  # PolyLite ASA
    "GFB98": (240, 280),  # Generic ASA
    "GFB99": (240, 280),  # Generic ABS
    "GFC00": (260, 290),  # Bambu PC
    "GFC01": (260, 290),  # Bambu PC FR
    "GFC99": (260, 290),  # Generic PC
    "GFG00": (230, 270),  # Bambu PETG Basic
    "GFG01": (230, 270),  # Bambu PETG Translucent
    "GFG02": (230, 270),  # Bambu PETG HF
    "GFG50": (240, 270),  # Bambu PETG-CF
    "GFG60": (220, 260),  # PolyLite PETG
    "GFG96": (220, 270),  # Generic PETG HF
    "GFG97": (240, 270),  # Generic PCTG
    "GFG98": (240, 270),  # Generic PETG-CF
    "GFG99": (220, 270),  # Generic PETG
    "GFL00": (190, 240),  # PolyLite PLA
    "GFL01": (190, 240),  # PolyTerra PLA
    "GFL03": (190, 240),  # eSUN PLA+
    "GFL04": (190, 240),  # Overture PLA
    "GFL05": (190, 240),  # Overture Matte PLA
    "GFL06": (250, 290),  # Fiberon PETG-ESD
    "GFL50": (280, 300),  # Fiberon PA6-CF
    "GFL51": (280, 300),  # Fiberon PA6-GF
    "GFL52": (260, 300),  # Fiberon PA12-CF
    "GFL53": (260, 300),  # Fiberon PA612-CF
    "GFL54": (270, 300),  # Fiberon PET-CF
    "GFL55": (240, 270),  # Fiberon PETG-rCF
    "GFL95": (190, 240),  # Generic PLA High Speed
    "GFL96": (190, 240),  # Generic PLA Silk
    "GFL98": (190, 240),  # Generic PLA-CF
    "GFL99": (190, 240),  # Generic PLA
    "GFN03": (260, 300),  # Bambu PA-CF
    "GFN04": (260, 300),  # Bambu PAHT-CF
    "GFN05": (260, 300),  # Bambu PA6-CF
    "GFN06": (280, 320),  # Bambu PPA-CF
    "GFN07": (280, 320),  # Bambu PPA-GF
    "GFN08": (260, 300),  # Bambu PA6-GF
    "GFN96": (280, 320),  # Generic PPA-GF
    "GFN97": (280, 320),  # Generic PPA-CF
    "GFN98": (260, 300),  # Generic PA-CF
    "GFN99": (240, 280),  # Generic PA
    "GFP95": (220, 250),  # Generic PP-GF
    "GFP96": (220, 250),  # Generic PP-CF
    "GFP97": (220, 250),  # Generic PP
    "GFP98": (175, 220),  # Generic PE-CF
    "GFP99": (175, 220),  # Generic PE
    "GFR98": (190, 240),  # Generic PHA
    "GFR99": (175, 220),  # Generic EVA
    "GFS00": (190, 240),  # Bambu Support W
    "GFS01": (260, 300),  # Bambu Support G
    "GFS02": (190, 240),  # Bambu Support For PLA
    "GFS03": (260, 300),  # Bambu Support For PA/PET
    "GFS04": (210, 250),  # Bambu PVA
    "GFS05": (190, 240),  # Bambu Support For PLA/PETG
    "GFS06": (240, 270),  # Bambu Support for ABS
    "GFS97": (190, 240),  # Generic BVOH
    "GFS98": (220, 270),  # Generic HIPS
    "GFS99": (190, 240),  # Generic PVA
    "GFSNL02": (205, 245),  # SUNLU PLA Matte
    "GFSNL03": (190, 240),  # SUNLU PLA+
    "GFSNL04": (190, 240),  # SUNLU PLA+ 2.0
    "GFSNL05": (190, 240),  # SUNLU Silk PLA+
    "GFSNL06": (190, 260),  # SUNLU PLA Marble
    "GFSNL07": (195, 260),  # SUNLU Wood PLA
    "GFSNL08": (230, 280),  # SUNLU PETG
    "GFT01": (260, 290),  # Bambu PET-CF
    "GFT02": (310, 340),  # Bambu PPS-CF
    "GFT97": (300, 340),  # Generic PPS
    "GFT98": (310, 340),  # Generic PPS-CF
    "GFU00": (200, 250),  # Bambu TPU 95A HF
    "GFU01": (200, 250),  # Bambu TPU 95A
    "GFU02": (220, 240),  # Bambu TPU for AMS
    "GFU03": (200, 240),  # Bambu TPU 90A
    "GFU04": (200, 240),  # Bambu TPU 85A
    "GFU98": (200, 250),  # Generic TPU for AMS
    "GFU99": (200, 250),  # Generic TPU
}

# Material types -> (min_temp, max_temp) from materials.csv (fallback when no slicer code)
_MATERIAL_TEMPS: dict[str, tuple[int, int]] = {
    "abs": (240, 280),
    "abs-gf": (240, 280),
    "abs-s": (240, 270),
    "asa": (240, 280),
    "asa-aero": (240, 280),
    "asa-cf": (240, 280),
    "bvoh": (190, 240),
    "eva": (190, 240),
    "hips": (220, 270),
    "pa": (260, 300),
    "pa-cf": (260, 300),
    "pa-gf": (260, 300),
    "pa-s": (260, 300),
    "pa6-cf": (260, 300),
    "pc": (260, 290),
    "pctg": (220, 260),
    "pe": (190, 240),
    "pe-cf": (190, 240),
    "pet-cf": (220, 260),
    "petg": (220, 260),
    "petg-cf": (220, 260),
    "pha": (190, 240),
    "pla": (190, 240),
    "pla-aero": (190, 240),
    "pla-cf": (190, 240),
    "pla-s": (190, 240),
    "pp": (220, 250),
    "pp-cf": (220, 250),
    "pp-gf": (220, 250),
    "ppa-cf": (280, 320),
    "ppa-gf": (280, 320),
    "pps": (300, 340),
    "pps-cf": (300, 340),
    "pva": (190, 240),
    "tpu": (200, 250),
    "tpu-ams": (200, 250),
}


def map_extruder_temp(rec: SpoolRecord, default: int = 215) -> int | None:
    """Return the midpoint of SpoolEase's nozzle temp range, or a default guess.

    SpoolEase stores min/max nozzle temps from its base-material CSVs (e.g. PLA: 190-240).
    We compute the midpoint for Spoolman's single-value extruder temp field.

    For unrecognized/custom filaments (no slicer code, no material match), return the
    caller-provided default - a reasonable PLA-like guess.

    This is a one-way mapping: SpoolEase -> Spoolman only. Spoolman's extruder temp
    override is not written back to SpoolEase.
    """
    code = rec.slicer_filament.strip()
    material = rec.material_type.strip().lower()

    # Known base filament codes - exact midpoint from base-filaments-index.csv
    if code and code in _BASE_FILAMENT_TEMPS:
        lo, hi = _BASE_FILAMENT_TEMPS[code]
        return (lo + hi) // 2

    # Material-based fallback - look up by material type (from materials.csv)
    if material and material in _MATERIAL_TEMPS:
        lo, hi = _MATERIAL_TEMPS[material]
        return (lo + hi) // 2

    # Unknown - fall back to caller-provided default
    return default


def map_filament(rec: SpoolRecord, *, density: float, diameter: float,
                 default_extruder_temp: int = 215) -> FilamentSpec:
    variant = map_variant(rec)
    extra: dict[str, str] = {}
    if variant is not None:
        extra["variant"] = _json_str(variant)  # Spoolman JSON-encodes string values

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
        settings_extruder_temp=map_extruder_temp(rec, default=default_extruder_temp),
        settings_bed_temp=None,  # SpoolEase does not track bed temperatures
        extra=extra,
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

# ---------------------------------------------------------------------------
# Phase C: map_sm_to_se() — Spoolman spool -> SpoolEase DTO
# ---------------------------------------------------------------------------

def map_sm_to_se(cached_spoolease: dict, updates: dict) -> dict:
    """Convert a cached SpoolEase record + Spoolman-owned updates into a SpoolEase DTO.

    The DTO shape matches AddOrEditSpoolRequest for POST /api/spools/add-edit.
    Spoolman-owned fields (location, comment) come from `updates`.
    All other fields come from `cached_spoolease`.

    Args:
        cached_spoolease: Dict from SpooleaseClient.get_spool_by_id() containing
            all SpoolEase fields for the record.
        updates: Dict of Spoolman-owned fields to write back. Currently contains
            'location' (-> actual_location) and/or 'comment' (-> note).

    Returns:
        DTO dict ready for POST to SpoolEase.
    """
    return {
        "id": cached_spoolease.get("id") or "",
        "tag_id": cached_spoolease.get("tag_id") or "",
        "rgba": cached_spoolease.get("color_code") or "",
        "color_name": cached_spoolease.get("color_name") or "",
        "material": cached_spoolease.get("material_type") or "",
        "subtype": cached_spoolease.get("material_subtype") or "",
        "brand": cached_spoolease.get("brand") or "",
        "core_weight": cached_spoolease.get("weight_core") or 0,
        "label_weight": cached_spoolease.get("weight_advertised") or 0,
        "note": updates.get("comment") or "",
        "slicer_filament": cached_spoolease.get("slicer_filament") or "",
        "full_unused": "y" if cached_spoolease.get("added_full") else (
            "n" if cached_spoolease.get("added_full") is False else ""),
        "assigned_location": cached_spoolease.get("assigned_location") or "",
        "actual_location": updates.get("location") or "",
        "spools_count": cached_spoolease.get("spools_count") or 1,
        # Optional fields — only included if they have values
        **(
            {"stock": cached_spoolease["stock"]}
            if cached_spoolease.get("stock") is not None
            else {}
        ),
        **(
            {"td": cached_spoolease["td"]}
            if cached_spoolease.get("td") is not None
            else {}
        ),
    }
