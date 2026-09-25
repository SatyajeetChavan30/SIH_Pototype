"""Unit tests for the VISION.md features that need no demo database:
decision-log hash chain, conformal alarm gate, severity-adjusted mitigation ranking, physics baselines,
DTW alignment and the what-if plan copy."""
import numpy as np
import pytest

from nwis import audit
from nwis.db import DB


@pytest.fixture()
def db(tmp_path):
    d = DB(tmp_path / "t.db")
    d.init()
    return d


def test_decision_log_chain_detects_tampering(db):
    rows = [audit.make_row("opened", session_id="s1", well_id="W", t=i * 60.0, md=1000 + i,
                           alert={"id": f"A{i}", "key": "RT:LOSS:flow-pit", "hazard": "LOSS", "level": "warning"},
                           payload={"i": i}) for i in range(5)]
    assert audit.append_many(db, rows) == 5
    assert audit.append_many(db, [audit.make_row("acknowledged", session_id="s1", actor="Shift lead")]) == 1
    v = audit.verify_chain(db)
    assert v["ok"] and v["n"] == 6
    assert [r["event"] for r in audit.query(db, session_id="s1", limit=2)] == ["acknowledged", "opened"]
    db.execute("UPDATE decision_log SET actor='someone else' WHERE seq=3")
    db.commit()
    v = audit.verify_chain(db)
    assert not v["ok"] and v["first_bad_seq"] == 3


def test_conformal_gate_bounds_false_alarms_on_normal_data():
    from nwis.realtime.calibrate import HINDSIGHT, OnlineConformal
    rng = np.random.default_rng(0)
    oc = OnlineConformal(budget_per_hour=1.0)
    det = "stuck-pipe-index"
    passed = total = 0
    for i in range(HINDSIGHT + 4000):
        oc.update({det: float(rng.normal())})
        if i > HINDSIGHT + 700:
            allow, p, _ = oc.gate(det, "warning")
            total += 1
            passed += allow
        oc.observe(set())
    # exchangeable normal data: pass rate should be about alpha (allow generous slack for a finite sample)
    assert passed / total <= oc.alpha * 3 + 0.002
    # a clear anomaly passes, and critical alerts always pass
    oc.update({det: 10.0})
    assert oc.gate(det, "warning")[0]
    oc.update({det: 0.0})
    assert oc.gate(det, "critical")[0]


def test_conformal_gate_respects_console_budget():
    from nwis.realtime.calibrate import OnlineConformal
    oc = OnlineConformal(budget_per_hour=1.0)
    oc.update({"torque-spike": 1.0})
    allow, _, why = oc.gate("torque-spike", "warning", opened_last_hour=3)
    assert not allow and "budget" in why
    assert oc.gate("torque-spike", "warning", corroborated=True, opened_last_hour=3)[0]


def test_severity_adjustment_removes_case_mix_bias():
    from nwis.risk.recommend import adjusted_efficacy, efficacy
    ev = []
    # PLUG is used on total losses (hard), LCM on seepage (easy). Within each severity both cure equally often.
    for i in range(10):
        ev.append({"id": f"a{i}", "well_id": "W", "severity": "total", "npt_hours": 10,
                   "actions": [{"code": "CEMENT_PLUG", "success": i < 4}]})
        ev.append({"id": f"b{i}", "well_id": "W", "severity": "seepage", "npt_hours": 2,
                   "actions": [{"code": "LCM_FINE", "success": i < 9}]})
    for i in range(2):
        ev.append({"id": f"c{i}", "well_id": "W", "severity": "total", "npt_hours": 10,
                   "actions": [{"code": "LCM_FINE", "success": i < 1}]})
        ev.append({"id": f"d{i}", "well_id": "W", "severity": "seepage", "npt_hours": 2,
                   "actions": [{"code": "CEMENT_PLUG", "success": True}]})
    adj = adjusted_efficacy(ev)
    rows = {r["code"]: r for r in efficacy(ev)}
    gap_crude = rows["LCM_FINE"]["cure_rate_smoothed"] - rows["CEMENT_PLUG"]["cure_rate_smoothed"]
    gap_adj = adj["LCM_FINE"]["cure_rate_adjusted"] - adj["CEMENT_PLUG"]["cure_rate_adjusted"]
    assert gap_crude > 0.25           # raw rates make the plug look far worse
    assert abs(gap_adj) < gap_crude / 2
    assert rows["CEMENT_PLUG"]["confounded"]


def test_physics_baseline_tracks_depth_and_mud_weight():
    from nwis.realtime.physics import PhysicsBaseline
    pb = PhysicsBaseline()
    rng = np.random.default_rng(1)

    def sample(md, mw):
        bf = 1 - mw / 65.5
        return {"md": md, "mw": mw, "flow_in": 720.0, "wob": 20.0, "rop": 15.0,
                "torque": 2.0 + 9.0 * bf * md / 1000 + rng.normal(0, 0.2),
                "hookload": 30 + 50 * bf * md / 1000 + rng.normal(0, 0.8),
                "spp": 300 + 2.4 * mw * (0.72 ** 1.8) * md + rng.normal(0, 15),
                "ecd": mw + 0.3 + 0.01 * 15 + rng.normal(0, 0.01)}
    for md in np.arange(2000, 2300, 1.0):
        pb.observe(sample(md, 10.0), 0.0, quiet=True)
    assert all(pb.calibrated(c) for c in pb.CHANNELS)
    s = sample(2600, 11.0)             # deeper and heavier mud than anything seen: no re-learning needed
    e = pb.expected(s, 0.0)
    assert abs(e["torque"] - (2.0 + 9.0 * (1 - 11 / 65.5) * 2.6)) < 0.8
    assert abs(e["hookload"] - (30 + 50 * (1 - 11 / 65.5) * 2.6)) < 4.0


def test_subsequence_dtw_finds_shifted_boundary():
    from nwis.realtime.toppick import subsequence_dtw
    rng = np.random.default_rng(2)
    template = np.r_[np.full(30, 100.0), np.full(15, 50.0)] + rng.normal(0, 3, 45)
    series = np.r_[np.full(70, 100.0), np.full(40, 50.0)] + rng.normal(0, 3, 110)
    cost, mapping = subsequence_dtw(template, series)
    assert abs(mapping[30] - 70) <= 3   # the template boundary lands on the live boundary


def test_whatif_never_mutates_the_source_plan():
    from nwis.geo import Trajectory
    from nwis.correlation import Target
    from nwis.risk.whatif import apply_overrides
    secs = [{"idx": 0, "hole": "17-1/2\"", "top_md": 0.0, "shoe_md": 1000.0, "mw_ppg": 9.0, "ecd_ppg": 9.3},
            {"idx": 1, "hole": "12-1/4\"", "top_md": 1000.0, "shoe_md": 3000.0, "mw_ppg": 10.0, "ecd_ppg": 10.4}]
    t = Target(None, "t", 27.0, 95.0, Trajectory([0, 3000], [0, 0], [0, 0]), secs, 2026, None, 3000.0)
    s = apply_overrides(t, {"sections": [{"idx": 0, "shoe_md": 1200}, {"idx": 1, "mw_ppg": 10.8, "ecd_ppg": 10.5}]})
    assert secs[0]["shoe_md"] == 1000.0 and secs[1]["mw_ppg"] == 10.0
    assert s.sections[1]["top_md"] == 1200 and s.sections[1]["mw_ppg"] == 10.8
    assert s.sections[1]["ecd_ppg"] >= s.sections[1]["mw_ppg"]
