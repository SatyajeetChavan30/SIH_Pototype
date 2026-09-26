"""Everything that used to be a command is now a dashboard action: first-run set-up, background jobs, staged
rebuilds that keep accounts and the decision log, settings, the live-feed switch, the rig simulator, bulk import."""
import io
import json
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest

from nwis import auth, config, ops
from nwis.db import DB
from nwis.jobs import JobCancelled, JobManager


# ---------------------------------------------------------------------------------------------- units
def test_settings_file_round_trip_and_env_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "s.json")
    config.save_settings({"top_pick_mode": "dtw"})
    config.save_settings({"llm_backend": "ollama"})
    assert config.read_settings() == {"top_pick_mode": "dtw", "llm_backend": "ollama"}
    monkeypatch.setenv("NWIS_TOP_PICK", "mudlogger")
    assert "top_pick_mode" in config.locked()      # an environment variable wins and is shown as locked
    monkeypatch.delenv("NWIS_TOP_PICK")
    assert "top_pick_mode" not in config.locked()


def test_job_manager_runs_logs_and_keeps_exclusive_jobs_apart():
    jm = JobManager()
    gate = []

    def slow(job):
        job.log("step 1")
        while not gate:
            job.check_cancel()
            time.sleep(0.01)
        return {"n": 1}
    a = jm.start("build", "A", slow, exclusive=True)
    with pytest.raises(RuntimeError):
        jm.start("build", "B", slow, exclusive=True)
    quick = jm.start("import", "C", lambda j: 42)      # non-exclusive work still runs alongside
    gate.append(1)
    for _ in range(200):
        if a.status != "running" and quick.status != "running":
            break
        time.sleep(0.01)
    assert (a.status, a.result, quick.result) == ("done", {"n": 1}, 42)
    assert a.payload()["lines"] == ["step 1"]

    c = jm.start("build", "D", lambda j: (time.sleep(0.05), j.check_cancel()), exclusive=True)
    assert jm.cancel(c.id)
    for _ in range(100):
        if c.status != "running":
            break
        time.sleep(0.01)
    assert c.status == "cancelled"
    with pytest.raises(JobCancelled):
        c.check_cancel()


def _mini_db(path: Path, doc_path: str, users: list[str], log_rows: int = 0) -> None:
    d = DB(path)
    d.init()
    auth.ensure_schema(d)
    for u in users:
        auth.create_user(d, u, u, "field", "pass1")
    d.insert("documents", {"id": "D1", "path": doc_path, "title": "t"})
    for i in range(log_rows):
        d.insert("decision_log", {"seq": i + 1, "event": "ack", "actor": "x", "hash": f"h{i}"})
    d.commit()
    d.conn.close()


def test_staged_rebuild_is_promoted_with_accounts_log_and_paths(tmp_path):
    final = tmp_path / "data"
    st = ops.staging_of(final)
    final.mkdir()
    st.mkdir()
    _mini_db(final / "nwis.db", str(final / "documents" / "a.pdf"), ["alice"], log_rows=3)
    _mini_db(st / "nwis.db", str(st / "documents" / "a.pdf"), [])
    assert not ops.promote_staged(final)                  # no BUILD_OK marker: an unfinished build is never used
    (st / ops.BUILD_OK).write_text("x")
    assert ops.promote_staged(final, log=lambda *_: None)
    assert not st.exists() and not final.with_name("data.old").exists()
    con = sqlite3.connect(final / "nwis.db")
    assert con.execute("SELECT path FROM documents").fetchone()[0] == str(final / "documents" / "a.pdf")
    assert [r[0] for r in con.execute("SELECT username FROM users")] == ["alice"]
    assert con.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0] == 3
    con.close()
    assert not (final / ops.BUILD_OK).exists()


def test_zip_uploads_are_flattened_and_filtered(tmp_path):
    from nwis.api.main import _save_uploads

    class Up:
        def __init__(self, name, data):
            self.filename, self.file = name, io.BytesIO(data)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.pdf", b"%PDF")
        z.writestr("reports/2019/NDH-3_DDR.pdf", b"%PDF")
        z.writestr("reports/readme.txt", b"hi")
        z.writestr("__MACOSX/reports/._NDH-3_DDR.pdf", b"junk")
    out = tmp_path / "up"
    saved = _save_uploads([Up("batch.zip", buf.getvalue()), Up("one.xml", b"<x/>"), Up("notes.docx", b"x")], out,
                          (".pdf", ".xml"))
    assert sorted(p.name for p in saved) == ["NDH-3_DDR.pdf", "evil.pdf", "one.xml"]
    assert all(p.parent == out for p in saved)          # nothing escapes the upload folder


def test_access_policy_for_operations():
    admin, office, field = ({"role": r} for r in ("admin", "office", "field"))
    assert auth.can(None, "GET", "/api/setup/status") and auth.can(None, "POST", "/api/setup/build")
    for path in ("/api/admin/status", "/api/admin/stream", "/api/admin/build", "/api/admin/restart"):
        assert auth.can(admin, "POST", path) and not auth.can(office, "POST", path) and not auth.can(field, "POST", path)
    assert auth.can(office, "GET", "/api/jobs/x") and not auth.can(field, "GET", "/api/jobs/x")
    assert auth.can(office, "POST", "/api/ingest/batch") and not auth.can(field, "POST", "/api/ingest/batch")
    assert auth.can(office, "POST", "/api/analytics/evaluate-live") and not auth.can(field, "POST", "/api/analytics/evaluate-live")


def test_first_run_server_offers_setup_instead_of_failing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import nwis.api.main as main
    empty = tmp_path / "data"
    for name, sub in (("DATA_DIR", None), ("DB_PATH", "nwis.db"), ("LOGS_DIR", "logs"), ("MODELS_DIR", "models"),
                      ("UPLOADS_DIR", "uploads")):
        monkeypatch.setattr(config, name, empty / sub if sub else empty)
    monkeypatch.setattr(main, "S", main.State())
    with TestClient(main.app) as c:
        st = c.get("/api/setup/status").json()
        assert st["ready"] is False and st["datasets"]["assam"]["built"] is False and st["job"] is None
        r = c.get("/api/meta")
        assert r.status_code == 503 and "Build knowledge base" in r.json()["detail"]
        assert c.post("/api/setup/build", json={"dataset": "mars"}).status_code == 400


# ---------------------------------------------------------------------------------------------- system
built = pytest.mark.skipif(not config.DB_PATH.exists() or not (config.LOGS_DIR / "active_stream.npz").exists(),
                           reason="demo knowledge base not built")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    import nwis.api.main as main
    dst = tmp_path_factory.mktemp("nwis") / "data"
    shutil.copytree(config.DATA_DIR, dst, ignore=shutil.ignore_patterns("documents", "eval", "uploads"))
    mp = pytest.MonkeyPatch()
    for name, sub in (("DATA_DIR", None), ("DB_PATH", "nwis.db"), ("LOGS_DIR", "logs"), ("MODELS_DIR", "models"),
                      ("UPLOADS_DIR", "uploads")):
        mp.setattr(config, name, dst / sub if sub else dst)
    mp.setattr(config, "STREAM", "replay")
    mp.setattr(main, "S", main.State())
    with TestClient(main.app) as c:
        yield c
    ops.SIM.stop()
    mp.undo()


def _wait_job(c, job_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.3)
    raise AssertionError("job did not finish")


@built
def test_setup_is_closed_once_ready_and_admin_status_reports(client):
    assert client.post("/api/setup/build", json={"dataset": "assam"}).status_code == 409
    st = client.get("/api/admin/status").json()
    assert st["region"] == "assam" and st["datasets"]["assam"]["current"] and st["stream"]["live"] is False
    assert st["simulator"]["available"] and len(st["episodes"]) >= 4


@built
def test_settings_apply_immediately(client):
    r = client.post("/api/admin/settings", json={"top_pick_mode": "dtw", "stream_gap_s": 120})
    assert r.status_code == 200 and config.TOP_PICK_MODE == "dtw" and config.STREAM_GAP_S == 120
    assert client.get("/api/meta").json()["top_pick_mode"] == "dtw"
    assert client.post("/api/admin/settings", json={"top_pick_mode": "guess"}).status_code == 400
    assert client.post("/api/admin/settings", json={"auth": False}).status_code == 409   # NWIS_AUTH is set by the tests
    client.post("/api/admin/settings", json={"top_pick_mode": "auto", "stream_gap_s": 300})


@built
def test_simulated_rig_feeds_the_live_console_end_to_end(client):
    r = client.post("/api/admin/stream", json={"spec": "wits0-listen:0"})     # port 0: any free port
    assert r.status_code == 200 and r.json()["live"] and client.get("/api/meta").json()["stream"]["live_available"]
    r = client.post("/api/admin/simulator/start", json={"episode": "S1", "speed": 0})
    assert r.status_code == 200, r.text
    deadline = time.time() + 60
    while time.time() < deadline:
        s = client.get("/api/admin/status").json()
        if s["stream"]["samples"] > 300:
            break
        time.sleep(0.5)
    assert s["stream"]["stats"]["connected"] and s["stream"]["samples"] > 300 and s["simulator"]["frames"] > 300
    with client.websocket_connect("/ws/live?mode=live") as ws:
        init = ws.receive_json()
        assert init["type"] == "init" and init["mode"] == "live"
    client.post("/api/admin/simulator/stop")
    r = client.post("/api/admin/stream", json={"spec": "replay"})
    assert r.status_code == 200 and not r.json()["live"]
    assert client.post("/api/admin/stream", json={"spec": "carrier-pigeon:1"}).status_code == 400


@built
def test_bulk_import_of_a_zip_runs_as_a_job(client):
    sample = config.DATA_DIR / "samples" / "NDH-21_DDR_latest.pdf"
    xml = config.DATA_DIR / "samples" / "drillreport_sample.xml"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(sample, "reports/NDH-21_DDR_latest.pdf")
        z.write(xml, "reports/drillreport_sample.xml")
        z.writestr("reports/broken.pdf", b"not a pdf")
    r = client.post("/api/ingest/batch", files=[("files", ("reports.zip", buf.getvalue(), "application/zip"))])
    assert r.status_code == 200, r.text
    j = _wait_job(client, r.json()["id"])
    assert j["status"] == "done" and j["result"]["n_files"] == 3 and j["result"]["errors"] == 1
    assert j["result"]["events"] >= 2
    assert client.post("/api/ingest/batch", files=[("files", ("a.txt", b"x", "text/plain"))]).status_code == 400


@built
def test_maintenance_jobs_list_refuse_when_unavailable_and_retrain_in_place(client):
    import nwis.api.main as main
    kinds = {k["key"]: k for k in client.get("/api/analytics/maintenance").json()}
    assert set(kinds) == {"evaluate-ocr", "retrain-risk", "retrain-classifier"} and kinds["retrain-risk"]["available"]
    assert client.post("/api/analytics/maintenance/rebuild-everything").status_code == 404
    saved = main.S.db.kv_get("verified_sentences")
    main.S.db.kv_set("verified_sentences", None)     # no review verdicts: nothing to retrain the classifier on
    try:
        r = client.post("/api/analytics/maintenance/retrain-classifier")
        assert r.status_code == 409 and "review" in r.json()["detail"]
    finally:
        main.S.db.kv_set("verified_sentences", saved)
    old_model = main.S.model
    r = client.post("/api/analytics/maintenance/retrain-risk", json={"actor": "tester"})
    assert r.status_code == 200, r.text
    j = _wait_job(client, r.json()["id"], timeout=600)
    assert j["status"] == "done", j.get("error")
    assert main.S.model is not old_model                     # the server now uses the retrained model
    rows = main.S.db.query("SELECT actor, payload FROM decision_log WHERE event='job' ORDER BY seq")
    assert [json.loads(r["payload"])["status"] for r in rows][-2:] == ["started", "done"]
    assert rows[-1]["actor"] == "tester"
