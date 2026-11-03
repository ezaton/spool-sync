"""Unit tests for the pure mapping logic — run with: pytest  (no network needed).

These cover the parts most likely to be wrong: the weight arithmetic (per SpoolEase's own
`weight_left_spool`) and the identity/dedup keys. Everything here is deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spoolsync.mapper import (  # noqa: E402
    compute_remaining,
    filament_external_id,
    map_filament,
    map_spool,
    map_vendor,
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
