"""Unit tests for the pure mapping logic — run with: pytest  (no network needed).

These cover the parts most likely to be wrong: the weight arithmetic (per SpoolEase's own
`weight_left_spool`) and the identity/dedup keys. Everything here is deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spoolsync.mapper import (
    map_sm_to_se,  # noqa: E402
    compute_remaining,
    filament_external_id,
    map_extruder_temp,
    map_filament,
    map_spool,
    map_vendor,
    map_variant,
    vendor_external_id,
)
from spoolsync.models import SpoolRecord  # noqa: E402


def test_remaining_when_weighed_uses_gross_minus_tare_minus_consumed():
    # gross current=740, tare(core)=250 => net on scale 490, minus 15 printed since weigh.
    rec = SpoolRecord(id="1", weight_advertised=1000, weight_core=250, weight_current=740,
                      consumed_since_weight=15.0)
    initial, remaining = compute_remaining(rec)
    assert initial == 1000.0
    assert remaining == 475.0  # 740 - 250 - 15


def test_tare_derived_when_core_unknown():
    # No core; weight_new(gross at reg)=1250, advertised(net full)=1000 => tare=250.
    rec = SpoolRecord(id="2", weight_advertised=1000, weight_new=1250, weight_current=900,
                      consumed_since_weight=0.0)
    initial, remaining = compute_remaining(rec)
    assert initial == 1000.0
    assert remaining == 650.0  # 900 - 250 - 0


def test_remaining_never_weighed_falls_back_to_consumed_since_add():
    rec = SpoolRecord(id="3", weight_advertised=1000, consumed_since_add=120.0)
    initial, remaining = compute_remaining(rec)
    assert initial == 1000.0
    assert remaining == 880.0  # 1000 - 120


def test_remaining_clamped_at_zero():
    rec = SpoolRecord(id="4", weight_advertised=1000, weight_core=250, weight_current=250,
                      consumed_since_weight=50.0)
    _, remaining = compute_remaining(rec)
    assert remaining == 0.0  # 250 - 250 - 50 = -50 -> clamped


def test_vendor_and_filament_identity_are_stable_and_dedupe():
    a = SpoolRecord(id="10", brand="Bambu", material_type="PLA", material_subtype="Matte",
                    color_code="1A1A1AFF", slicer_filament="GFA01")
    b = SpoolRecord(id="11", brand="Bambu", material_type="PLA", material_subtype="Matte",
                    color_code="1A1A1AFF", slicer_filament="GFA01")
    # Two physical spools of the same type share vendor+filament identity (=> dedupe).
    assert vendor_external_id(a.brand) == vendor_external_id(b.brand) == "se-brand:bambu"
    assert filament_external_id(a) == filament_external_id(b) == "se-fil:GFA01"


def test_filament_identity_synthesized_without_slicer_code():
    rec = SpoolRecord(id="12", brand="Polymaker", material_type="PETG", color_code="00AABBFF")
    assert filament_external_id(rec) == "se-fil:polymaker|petg||00AABBFF"


def test_map_filament_fills_required_density_diameter_and_normalizes_color():
    rec = SpoolRecord(id="20", brand="Bambu", material_type="PLA", material_subtype="Basic",
                      color_name="Black", color_code="#000000", weight_advertised=1000, weight_core=250)
    f = map_filament(rec, density=1.24, diameter=1.75)
    assert f.density == 1.24 and f.diameter == 1.75  # Spoolman requires both > 0
    assert f.material == "PLA"
    assert f.name == "Basic Black"
    assert f.color_hex == "000000"  # '#' stripped, upper-cased
    assert f.weight == 1000.0 and f.spool_weight == 250.0


def test_map_spool_sets_tag_extra_and_remaining():
    rec = SpoolRecord(id="30", tag_id="04A2B3C4D5E680", brand="Bambu", material_type="PLA",
                      weight_advertised=1000, weight_core=250, weight_current=600,
                      data_origin="BambuLabTag")
    s = map_spool(rec)
    assert s.tag_uid == "04A2B3C4D5E680"
    assert s.tag_format == "bambu"
    assert s.extra["spoolease_id"] == '"30"'  # JSON-encoded text value
    assert s.remaining_weight == 350.0  # 600 - 250 - 0


def test_retired_tag_is_not_linked():
    rec = SpoolRecord(id="31", tag_id="-04A2B3", brand="Bambu", material_type="PLA",
                      weight_advertised=1000)
    s = map_spool(rec)
    assert s.tag_uid is None  # '-' prefix => retired, don't link


def test_map_vendor_none_when_no_brand():
    assert map_vendor(SpoolRecord(id="40")) is None


# ---------------------------------------------------------------------------
# Temperature mapping tests
# ---------------------------------------------------------------------------


def test_known_base_filament_returns_exact_midpoint():
    # GFA01 = Bambu PLA Matte, 190-240 -> midpoint 215
    rec = SpoolRecord(id="t1", slicer_filament="GFA01", material_type="PLA")
    assert map_extruder_temp(rec) == 215


def test_high_temp_filament_returns_correct_midpoint():
    # GFT02 = Bambu PPS-CF, 310-340 -> midpoint 325
    rec = SpoolRecord(id="t2", slicer_filament="GFT02", material_type="PPS-CF")
    assert map_extruder_temp(rec) == 325


def test_odd_range_rounds_down():
    # GFU02 = Bambu TPU for AMS, 220-240 -> midpoint 230
    rec = SpoolRecord(id="t3", slicer_filament="GFU02", material_type="TPU-AMS")
    assert map_extruder_temp(rec) == 230


def test_unknown_slicer_code_falls_back_to_material():
    # Unknown code but known material type
    rec = SpoolRecord(id="t4", slicer_filament="XYZ99", material_type="ABS")
    assert map_extruder_temp(rec) == 260  # (240+280)//2


def test_unknown_slicer_code_falls_back_to_material_subtype():
    # Unknown code, material type with subtype
    rec = SpoolRecord(id="t5", slicer_filament="XYZ99", material_type="PLA-CF")
    assert map_extruder_temp(rec) == 215  # (190+240)//2


def test_completely_unknown_returns_default():
    # No code, no known material
    rec = SpoolRecord(id="t6", slicer_filament="", material_type="MysteryPlastic")
    assert map_extruder_temp(rec) == 215  # default


def test_completely_unknown_respects_custom_default():
    rec = SpoolRecord(id="t7", slicer_filament="", material_type="")
    assert map_extruder_temp(rec, default=200) == 200


def test_map_filament_populates_extruder_temp_from_base_filament():
    rec = SpoolRecord(id="t10", brand="Bambu", material_type="PLA", material_subtype="Matte",
                      color_name="Black", color_code="#000000", weight_advertised=1000,
                      weight_core=250, slicer_filament="GFA01")
    f = map_filament(rec, density=1.24, diameter=1.75, default_extruder_temp=215)
    assert f.settings_extruder_temp == 215  # (190+240)//2
    assert f.settings_bed_temp is None


def test_map_filament_populates_extruder_temp_from_material_fallback():
    rec = SpoolRecord(id="t11", brand="Generic", material_type="ABS", material_subtype="",
                      color_name="Black", color_code="#000000", weight_advertised=500,
                      weight_core=150, slicer_filament="")
    f = map_filament(rec, density=1.04, diameter=1.75, default_extruder_temp=215)
    assert f.settings_extruder_temp == 260  # (240+280)//2


def test_map_filament_uses_custom_default_for_unknown():
    rec = SpoolRecord(id="t12", brand="Unknown", material_type="Mystery",
                      color_name="Red", color_code="#FF0000FF", weight_advertised=500,
                      weight_core=100, slicer_filament="")
    f = map_filament(rec, density=1.0, diameter=1.75, default_extruder_temp=180)
    assert f.settings_extruder_temp == 180


# ---------------------------------------------------------------------------
# Phase C: map_sm_to_se() tests
# ---------------------------------------------------------------------------

def _make_cached(**overrides) -> dict:
    """Build a minimal cached SpoolEase record dict."""
    base = {
        "id": "se-test01",
        "tag_id": "AABBCCDD",
        "color_code": "FF8800FF",
        "color_name": "Orange",
        "material_type": "PLA",
        "material_subtype": "Matte",
        "brand": "Bambu",
        "weight_core": 250,
        "weight_advertised": 1000,
        "note": "Old note",
        "slicer_filament": "GFA01",
        "added_full": True,
        "assigned_location": "",
        "actual_location": "Old location",
        "spools_count": 1,
        "stock": None,
        "td": None,
    }
    base.update(overrides)
    return base


def test_map_sm_to_se_preserves_spoolease_fields():
    """Verify SpoolEase-owned fields come from cache, not from updates."""
    cached = _make_cached()
    updates = {"location": "Drawer 1"}
    payload = map_sm_to_se(cached, updates)

    assert payload["id"] == "se-test01"
    assert payload["tag_id"] == "AABBCCDD"
    assert payload["material"] == "PLA"
    assert payload["subtype"] == "Matte"
    assert payload["brand"] == "Bambu"
    assert payload["core_weight"] == 250
    assert payload["label_weight"] == 1000
    assert payload["slicer_filament"] == "GFA01"
    assert payload["full_unused"] == "y"
    assert payload["spools_count"] == 1


def test_map_sm_to_se_overwrites_location():
    """Verify actual_location comes from updates, not from cached actual_location."""
    cached = _make_cached(actual_location="Old location")
    updates = {"location": "Drawer 1"}
    payload = map_sm_to_se(cached, updates)
    assert payload["actual_location"] == "Drawer 1"


def test_map_sm_to_se_overwrites_comment():
    """Verify note comes from updates.comment, not from cached note."""
    cached = _make_cached(note="Old note")
    updates = {"comment": "New comment from Spoolman"}
    payload = map_sm_to_se(cached, updates)
    assert payload["note"] == "New comment from Spoolman"


def test_map_sm_to_se_empty_updates():
    """With no updates, Spoolman-owned fields default to empty strings."""
    cached = _make_cached(actual_location="Garage", note="Cache note")
    updates = {}
    payload = map_sm_to_se(cached, updates)
    assert payload["actual_location"] == ""  # no location in updates
    assert payload["note"] == ""  # no comment in updates (note comes from updates)


def test_map_sm_to_se_bool_conversion():
    """Verify added_full bool -> 'y'/'n' conversion."""
    cached_true = _make_cached(added_full=True)
    cached_false = _make_cached(added_full=False)
    cached_none = _make_cached(added_full=None)

    assert map_sm_to_se(cached_true, {})["full_unused"] == "y"
    assert map_sm_to_se(cached_false, {})["full_unused"] == "n"
    assert map_sm_to_se(cached_none, {})["full_unused"] == ""


def test_map_sm_to_se_optional_fields():
    """Verify stock and td are only included when not None."""
    cached_with = _make_cached(stock=5, td=12.5)
    cached_without = _make_cached(stock=None, td=None)

    payload_with = map_sm_to_se(cached_with, {})
    assert "stock" in payload_with
    assert payload_with["stock"] == 5
    assert "td" in payload_with
    assert payload_with["td"] == 12.5

    payload_without = map_sm_to_se(cached_without, {})
    assert "stock" not in payload_without
    assert "td" not in payload_without


# --- Variant mapping tests ---

def test_map_variant_returns_subtype_when_present():
    rec = SpoolRecord(id="v1", material_subtype="Matte")
    assert map_variant(rec) == "Matte"


def test_map_variant_returns_none_for_empty_subtype():
    rec = SpoolRecord(id="v2", material_subtype="")
    assert map_variant(rec) is None


def test_map_variant_trims_whitespace():
    rec = SpoolRecord(id="v3", material_subtype="  Silk  ")
    assert map_variant(rec) == "Silk"


def test_map_filament_includes_variant_in_extra():
    rec = SpoolRecord(id="f1", brand="Overture", material_type="PLA",
                      material_subtype="Matte", color_name="Black",
                      color_code="#000000", weight_advertised=1000,
                      weight_core=250)
    f = map_filament(rec, density=1.24, diameter=1.75)
    assert f.extra == {"variant": '"Matte"'}


def test_map_filament_no_variant_when_empty():
    rec = SpoolRecord(id="f2", brand="Generic", material_type="PLA",
                      material_subtype="", color_name="White",
                      color_code="#FFFFFF", weight_advertised=1000,
                      weight_core=200)
    f = map_filament(rec, density=1.24, diameter=1.75)
    assert f.extra == {}
