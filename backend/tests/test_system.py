"""System tests against the built demo knowledge base (run `python -m nwis.cli build-demo` first).

They check the claims made in docs/SOLUTION.md: extraction quality on held-out phrasing,
risk-model skill vs baselines, MW-window recovery of the latent truth, the live alert
sequence, and the API surface.
"""
import json

import pytest

from nwis import config

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="demo knowledge base not built")


@pytest.fixture(scope="module")
def kb():
    from nwis.kb import KnowledgeBase
    return KnowledgeBase()


@pytest.fixture(scope="module")
def model():
    from nwis.risk.model import RiskModel
    return RiskModel.load(config.MODELS_DIR / "risk_model.joblib")


def test_extraction_quality(kb):
    ev = kb.db.kv_get("extraction_eval")
    assert ev["held_out"]["f1"] >= 0.85
    assert ev["held_out"]["precision"] >= 0.9
    assert ev["held_out"]["formation_accuracy"] >= 0.95


def test_scanned_reports_keep_their_events(kb):
    ev = kb.db.kv_get("ocr_eval")
    if ev is None:
        pytest.skip("no OCR engine was installed when the demo was built")
    assert ev["standard"]["recall"] >= 0.9 and ev["standard"]["precision"] >= 0.9
    assert ev["poor"]["recall"] >= 0.8


def test_every_event_is_cited(kb):
    assert kb.events and all(e["citations"] for e in kb.events)


def test_risk_model_beats_baselines(kb):
    m = kb.db.kv_get("risk_metrics")["pooled"]
    assert m["ml"]["auc"] > m["base_rate"]["auc"] > m["nearest_offset"]["auc"]
    assert m["ml"]["auc"] >= 0.85


def test_mw_window_recovers_latent_tipam_loss_gradient(kb):
    from nwis.correlation import target_from_well
    from nwis.data.synth import STRUCT_BY_ID, loss_gradient
    from nwis.risk.mw_window import mw_window
    t = target_from_well(kb, kb.active_id)
    w = mw_window(kb, t, 10.0)["formations"]["TIPAM"]
    truth = loss_gradient("TIPAM", t.lat, t.lon, STRUCT_BY_ID["NDH"], t.spud_year)
    assert w["loss"]["ecd_p50"] is not None
    assert abs(w["loss"]["ecd_p50"] - truth) <= 0.5


def test_lookahead_zones_cover_hidden_active_well_hazards(kb, model):
    from nwis.correlation import target_from_well
    from nwis.risk.evidence import risk_profile
    prof = risk_profile(kb, target_from_well(kb, kb.active_id), 8.0, model=model)
    episodes = {e["id"]: e for e in kb.db.kv_get("active_episodes")}
    for ep_id in ("S1", "S3", "S4"):          # offset-predictable hazards
        ep = episodes[ep_id]
        assert any(z["hazard"] == ep["hazard"] and z["md0"] - 60 <= ep["md"] <= z["md1"] + 60 for z in prof["zones"]), ep_id


def test_live_replay_catches_every_episode(kb, model):
    from nwis.realtime.engine import LiveSession
    s = LiveSession(kb, model, None)
    opened = []
    while True:
        r = s.step(50)
        opened += [a for a in r["alerts"] if a["source"] in ("real-time", "fused")]
        if r["done"]:
            break
    for ep in s.episodes:
        hit = [a for a in opened if a["hazard"] == ep["hazard"] and ep.get("onset_md", ep["md"]) - 120 <= a["md"] <= ep["md"] + 60]
        assert hit, f"episode {ep['id']} ({ep['label']}) not detected"
    assert any(a["corroborated"] for a in opened), "look-ahead should corroborate at least one detection"


def test_api_smoke():
    from fastapi.testclient import TestClient
    from nwis.api.main import app
    with TestClient(app) as c:
        assert c.get("/api/meta").json()["active_well"]
        assert len(c.get("/api/wells").json()) > 40
        p = c.get("/api/risk/profile").json()
        assert p["bins"] and p["zones"]
        s = c.get("/api/search", params={"q": "losses in Tipam within 5 km"}).json()
        assert s["query"]["formation"] == "TIPAM" and s["results"]
        a = c.get("/api/ask", params={"q": "What worked for losses in Sylhet?"}).json()
        assert a["citations"] and "Sylhet" in a["answer"]
        assert "Offset Hazard Brief" in c.get("/api/brief").text
        with c.websocket_connect("/ws/live") as ws:
            init = json.loads(ws.receive_text())
            assert init["type"] == "init" and init["episodes"]
            tick = json.loads(ws.receive_text())
            assert tick["type"] == "tick" and tick["samples"]
