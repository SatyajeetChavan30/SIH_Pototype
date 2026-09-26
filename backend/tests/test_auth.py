"""Sign-in and role-based access: password hashing, signed sessions, the RULES policy and who the decision log names."""
import json
import shutil

import pytest

from stratasense import auth, config
from stratasense.db import DB


@pytest.fixture()
def db(tmp_path):
    d = DB(tmp_path / "t.db")
    d.init()
    auth.ensure_demo_users(d)
    return d


def test_demo_users_are_seeded_once_with_hashed_passwords(db):
    assert {u["role"] for u in auth.list_users(db)} == set(auth.ROLES)
    auth.ensure_demo_users(db)
    assert len(auth.list_users(db)) == 3
    row = db.one("SELECT * FROM users WHERE username='field'")
    assert config.DEMO_PASSWORD not in row["pw_hash"] and len(row["salt"]) == 32


def test_authenticate_and_session_tokens(db):
    assert auth.authenticate(db, "Field", config.DEMO_PASSWORD)["role"] == "field"
    assert auth.authenticate(db, "field", "wrong") is None
    assert auth.authenticate(db, "nobody", config.DEMO_PASSWORD) is None
    tok = auth.issue_token(db, {"username": "office"})
    assert auth.user_from_token(db, tok)["role"] == "office"
    body, sig = tok.rsplit(".", 1)
    forged = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + sig
    assert auth.user_from_token(db, forged) is None
    assert auth.user_from_token(db, None) is None and auth.user_from_token(db, "garbage") is None


def test_expired_token_is_rejected(db, monkeypatch):
    monkeypatch.setattr(auth, "SESSION_S", -1)
    assert auth.user_from_token(db, auth.issue_token(db, {"username": "field"})) is None


def test_policy_table():
    field, office, admin = ({"role": r} for r in ("field", "office", "admin"))
    assert auth.can(None, "GET", "/api/health") and auth.can(None, "POST", "/api/auth/login")
    assert not auth.can(None, "GET", "/api/wells")
    assert auth.can(field, "GET", "/api/wells") and auth.can(field, "POST", "/api/memo")
    assert auth.can(field, "GET", "/api/aar/E1") and not auth.can(field, "POST", "/api/aar/E1/approve")
    for method, path in (("POST", "/api/ingest"), ("POST", "/api/ingest/sample"), ("GET", "/api/review"),
                         ("POST", "/api/review/R1"), ("POST", "/api/risk/whatif"), ("GET", "/api/analytics")):
        assert not auth.can(field, method, path) and auth.can(office, method, path), path
    assert not auth.can(office, "GET", "/api/audit/verify") and auth.can(admin, "GET", "/api/audit/verify")
    assert auth.can(office, "GET", "/api/audit")
    assert not auth.can(office, "POST", "/api/users") and auth.can(admin, "POST", "/api/users")


# ---------------------------------------------------------------------------- through the API
built = pytest.mark.skipif(not config.DB_PATH.exists(), reason="demo knowledge base not built")


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    from fastapi.testclient import TestClient
    import stratasense.api.main as main
    dst = tmp_path_factory.mktemp("stratasense") / "data"
    shutil.copytree(config.DATA_DIR, dst, ignore=shutil.ignore_patterns("documents", "eval", "uploads"))
    mp = pytest.MonkeyPatch()
    for name, sub in (("DATA_DIR", None), ("DB_PATH", "stratasense.db"), ("LOGS_DIR", "logs"), ("MODELS_DIR", "models"),
                      ("UPLOADS_DIR", "uploads")):
        mp.setattr(config, name, dst / sub if sub else dst)
    mp.setattr(config, "AUTH", True)
    mp.setattr(main, "S", main.State())
    with TestClient(main.app) as c:
        yield c
    mp.undo()


def _login(c, who):
    r = c.post("/api/auth/login", json={"username": who, "password": config.DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["user"]


@built
def test_api_requires_sign_in_and_enforces_roles(app_client):
    c = app_client
    c.cookies.clear()
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/wells").status_code == 401
    me = c.get("/api/auth/me").json()
    assert me["auth"] is True and me["user"] is None and len(me["demo_users"]) == 3
    assert c.post("/api/auth/login", json={"username": "field", "password": "nope"}).status_code == 401

    _login(c, "field")
    assert c.get("/api/auth/me").json()["user"]["role"] == "field"
    assert c.get("/api/wells").status_code == 200
    assert c.post("/api/ingest/sample?name=ddr").status_code == 403
    assert c.get("/api/review").status_code == 403
    assert c.post("/api/risk/whatif", json={}).status_code == 403
    assert c.get("/api/jobs").status_code == 403
    assert c.post("/api/analytics/maintenance/retrain-risk").status_code == 403

    _login(c, "office")
    assert c.get("/api/review").status_code == 200
    assert c.get("/api/audit/verify").status_code == 403
    assert c.post("/api/users", json={"username": "x", "role": "field", "password": "xxxx"}).status_code == 403

    assert c.get("/api/jobs").status_code == 200
    assert c.get("/api/analytics/maintenance").status_code == 200
    assert c.post("/api/admin/build", json={"dataset": "assam"}).status_code == 403     # rebuilds are admin-only

    _login(c, "admin")
    assert c.get("/api/audit/verify").json()["ok"]
    new = c.post("/api/users", json={"username": "Jdoe", "display_name": "J. Doe", "role": "field",
                                     "password": "secret1"}).json()
    assert new == {"username": "jdoe", "display_name": "J. Doe", "role": "field"}
    assert "pw_hash" not in json.dumps(c.get("/api/users").json())
    dup = c.post("/api/users", json={"username": "jdoe", "role": "admin", "password": "takeover"})
    assert dup.status_code == 400 and "exists" in dup.json()["detail"]

    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get("/api/wells").status_code == 401


@built
def test_decision_log_names_the_signed_in_user_not_the_claimed_actor(app_client):
    c = app_client
    c.cookies.clear()
    field = _login(c, "field")
    c.post("/api/alerts/feedback", json={"alert_key": "k-auth", "hazard": "LOSS", "verdict": "useful",
                                         "actor": "Someone Else", "session_id": "auth-test"})
    rows = c.get("/api/audit", params={"session_id": "auth-test"}).json()["rows"]
    assert rows and rows[-1]["actor"] == field["display_name"]

    with c.websocket_connect("/ws/live") as ws:
        init = json.loads(ws.receive_text())
        assert init["type"] == "init"

    c.cookies.clear()
    with c.websocket_connect("/ws/live") as ws:
        msg = json.loads(ws.receive_text())
        assert msg == {"type": "error", "message": "sign in required"}


@built
def test_socket_commands_are_attributed_to_the_signed_in_user(app_client):
    c = app_client
    c.cookies.clear()
    field = _login(c, "field")
    with c.websocket_connect("/ws/live") as ws:
        init = json.loads(ws.receive_text())
        ws.send_text(json.dumps({"cmd": "budget", "value": 0.5, "actor": "Someone Else"}))
        ws.send_text(json.dumps({"cmd": "ack", "id": "A-missing", "key": "RT:LOSS:flow-pit", "actor": "Someone Else",
                                 "queued_offline": True, "acted_at": "2026-09-25T09:00:00Z"}))
        for _ in range(50):                       # wait until both commands have been handled
            msg = json.loads(ws.receive_text())
            if msg["type"] == "tick" and msg["status"]["budget"]["budget_per_hour"] == 0.5:
                break
    rows = c.get("/api/audit", params={"session_id": init["session_id"]}).json()["rows"]
    mine = {r["event"]: r for r in rows if r["event"] in ("budget_changed", "acknowledged")}
    assert set(mine) == {"budget_changed", "acknowledged"}
    assert all(r["actor"] == field["display_name"] for r in mine.values())
    assert mine["acknowledged"]["payload"]["queued_offline"] and mine["acknowledged"]["payload"]["unmatched"]
