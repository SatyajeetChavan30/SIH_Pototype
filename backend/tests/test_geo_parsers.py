"""Wellbore geometry, WITS-0 and WITSML parsing, end-to-end extraction on a WITSML report."""
import math
from pathlib import Path

import numpy as np

from nwis.geo import Trajectory, haversine_km, minimum_curvature
from nwis.ingest.pipeline import extract_document
from nwis.ingest.wits0 import parse_packets, to_packet
from nwis.ingest.witsml import load_witsml_pages

FIX = Path(__file__).parent / "fixtures"


def test_min_curvature_vertical_and_tangent():
    tvd, n, e, _ = minimum_curvature([0, 1000], [0, 0], [0, 0])
    assert tvd[-1] == 1000 and n[-1] == 0
    tvd, n, e, _ = minimum_curvature([0, 1000], [30, 30], [90, 90])
    assert math.isclose(tvd[-1], 1000 * math.cos(math.radians(30)), rel_tol=1e-9)
    assert math.isclose(e[-1], 1000 * math.sin(math.radians(30)), rel_tol=1e-9)


def test_min_curvature_matches_circular_arc():
    # constant build 0 -> 30 deg over 300 m: minimum curvature is exact for a circular arc
    inc = math.radians(30.0)
    R = 300 / inc
    tvd, n, _, dls = minimum_curvature([0, 300], [0, 30], [0, 0])
    assert math.isclose(tvd[-1], R * math.sin(inc), rel_tol=1e-9)
    assert math.isclose(n[-1], R * (1 - math.cos(inc)), rel_tol=1e-9)
    assert math.isclose(dls[-1], 3.0, rel_tol=1e-9)   # deg / 30 m


def test_trajectory_md_tvd_roundtrip():
    md = np.arange(0, 3000, 30.0)
    inc = np.clip((md - 800) / 30 * 2.5, 0, 28)
    t = Trajectory(md, inc, np.full_like(md, 132.0))
    for m in (500.0, 1500.0, 2900.0):
        assert abs(float(t.md_at_tvd(t.tvd_at_md(m))) - m) < 0.5


def test_haversine():
    assert abs(float(haversine_km(27.0, 95.0, 27.0 + 1 / 111.195, 95.0)) - 1.0) < 0.01


def test_wits0_roundtrip_and_noise_tolerance():
    pkt = to_packet({"md": 2105.3, "rop": 21.4, "torque": 12.1, "flow_out": 98.2})
    noisy = ["garbage", *pkt.splitlines(), "01xxbad", "&&", "0110 2106.0", "!!"]
    out = list(parse_packets(noisy))
    assert out[0] == {"md": 2105.3, "rop": 21.4, "torque": 12.1, "flow_out": 98.2}
    assert out[1] == {"md": 2106.0}


def test_witsml_drillreport_extraction():
    pages, reports = load_witsml_pages(FIX / "drillreport_sample.xml")
    assert reports[0]["well"] == "XYZ-01" and reports[0]["mw_ppg"] == 10.1
    ex = extract_document(pages, None, lambda wid: None)
    assert ex.well_id == "XYZ-01"
    assert len(ex.events) == 1, "the negated 'No losses observed' line must not become an event"
    e = ex.events[0]
    assert e.hazard == "LOSS" and e.md == 2172 and e.formation == "TIPAM" and e.rate_bbl_hr == 40
    assert [a["code"] for a in e.actions] == ["LCM_FINE"] and e.actions[0]["success"] is True
    assert e.citations and "partial losses" in e.citations[0]["text"]
