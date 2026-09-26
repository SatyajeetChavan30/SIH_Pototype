"""System tests for the VISION.md features against a *copy* of the built demo data (writes never touch it).

Covers: live alerting under the alarm budget, DTW top picking, what-if planning, expert memos through
peer review, after-action reviews, the shift-handover brief and the decision log over the WebSocket.
"""
import json
import shutil

import pytest

from stratasense import config

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="demo knowledge base not built")


@pytest.fixture(scope="module")
def data_copy(tmp_path_factory):
    src = config.DATA_DIR
    dst = tmp_path_factory.mktemp("stratasense") / "data"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("documents", "eval", "uploads"))
    return dst


@pytest.fixture(scope="module")
def client(data_copy):
    from fastapi.testclient import TestClient
    import stratasense.api.main as main
    mp = pytest.MonkeyPatch()
    for name, sub in (("DATA_DIR", None), ("DB_PATH", "stratasense.db"), ("LOGS_DIR", "logs"), ("MODELS_DIR", "models"),
                      ("UPLOADS_DIR", "uploads")):
        mp.setattr(config, name, data_copy / sub if sub else data_copy)
    mp.setattr(main, "S", main.State())
    with TestClient(main.app) as c:
        yield c
    mp.undo()


@pytest.fixture(scope="module")
def kb():
    from stratasense.kb import KnowledgeBase
    return KnowledgeBase()


@pytest.fixture(scope="module")
def model():
    from stratasense.risk.model import RiskModel
    return RiskModel.load(config.MODELS_DIR / "risk_model.joblib")


def test_alarm_budget_keeps_every_detection_and_cuts_nuisance_alarms(kb, model):
    from stratasense.realtime.evaluate import replay
    ungated = replay(kb, model, None, nuisance=1.0)
    gated = replay(kb, model, 1.0, nuisance=1.0)
    assert gated["detected"] == len(gated["episodes"]) == ungated["detected"]
    assert gated["false_alarms"] < ungated["false_alarms"]
    clean = replay(kb, model, 1.0, nuisance=0.0)
    assert clean["detected"] == len(clean["episodes"]) and clean["false_alarms"] == 0


def test_dtw_top_picks_and_detection_without_mudlogger(kb, model):
    from stratasense.realtime.evaluate import top_pick_eval
    r = top_pick_eval(kb, model)["dtw"]
    assert r["n"] >= 3
    errs = sorted(abs(v) for v in r["per_formation"].values())
    assert errs[len(errs) // 2] <= 15          # median pick within 15 m of the hidden truth
    assert r["episodes_detected"] == r["n_episodes"]


def test_whatif_changes_model_and_window_checks(client):
    body = {"overrides": {"sections": [{"idx": 2, "mw_ppg": 9.6, "ecd_ppg": 10.0}]}}
    r = client.post("/api/risk/whatif", json=body).json()
    assert r["deltas"] and r["note"]
    tipam = next(d for d in r["deltas"] if d["formation"] == "TIPAM" and d["hazard"] == "LOSS")
    assert tipam["scenario"] < tipam["baseline"]        # lighter ECD lowers modelled Tipam loss risk


def test_expert_memo_goes_through_peer_review(client):
    text = ("In Hapjan wells we always lost returns in the Sylhet limestone around 3,700 m. Fine LCM never worked; "
            "only a cement plug cured it. Lesson: go straight to a cement plug in fractured Sylhet.")
    m = client.post("/api/memo", json={"author": "R. Gogoi (retd.)", "text": text}).json()
    assert m["kind"] == "MEMO" and m["review"]
    lessons_before = {l["id"] for l in client.get("/api/lessons").json()}
    rid = next(r for r in m["review"] if r.startswith("R-L-"))
    assert client.post(f"/api/review/{rid}", json={"action": "approve"}).json()["ok"]
    lessons_after = client.get("/api/lessons").json()
    new = [l for l in lessons_after if l["id"] not in lessons_before]
    assert new and "R. Gogoi" in new[0]["text"] and new[0]["doc_id"] == m["doc_id"]


def test_after_action_review_is_cited_and_can_be_approved(client, kb):
    ev = next(e for e in kb.events if e["hazard"] == "LOSS" and e["actions"])
    r = client.get(f"/api/aar/{ev['id']}").json()
    assert r["citations"] and r["draft_lesson"] and r["status"] == "draft"
    assert all(1 <= c["n"] <= len(r["citations"]) for c in r["citations"])
    assert client.post(f"/api/aar/{ev['id']}/approve", json={"text": r["draft_lesson"], "reviewer": "T"}).json()["ok"]
    assert client.get(f"/api/aar/{ev['id']}").json()["status"] == "approved"


def test_live_session_handover_and_decision_log(client):
    with client.websocket_connect("/ws/live") as ws:
        init = json.loads(ws.receive_text())
        assert init["session_id"]
        ws.send_text(json.dumps({"cmd": "jump", "episode": "S1"}))
        alert = None
        for _ in range(400):
            m = json.loads(ws.receive_text())
            rt = [a for a in m.get("alerts", []) if a["source"] in ("real-time", "fused")]
            if rt:
                alert = rt[0]
                break
        assert alert is not None
        ws.send_text(json.dumps({"cmd": "ack", "id": alert["id"], "actor": "Shift lead"}))
        ws.send_text(json.dumps({"cmd": "handover", "hours": 12}))
        brief = None
        for _ in range(400):
            m = json.loads(ws.receive_text())
            if m["type"] == "handover":
                brief = m
                break
        assert brief and brief["sections"] and brief["citations"]
    rows = client.get("/api/audit", params={"session_id": init["session_id"]}).json()["rows"]
    events = {r["event"] for r in rows}
    assert {"opened", "acknowledged"} <= events
    assert any(r["actor"] == "Shift lead" for r in rows)
    assert client.get("/api/audit/verify").json()["ok"]
