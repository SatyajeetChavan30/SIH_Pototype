"""Dashboard background jobs: the subprocess runner, and rebuilds that keep users and the decision log."""
import sqlite3
import sys
import time

import pytest

from nwis import audit, auth, jobs
from nwis.db import DB


class ScriptManager(jobs.JobManager):
    """Runs `python -c <argv[0]>` instead of an nwis.cli command."""

    def command(self, kind, params):
        return [sys.executable, "-c", kind.argv(params)[0]]


def _kind(key, code, **kw):
    return jobs.Kind(key, key, "", "office", lambda p: [code], **kw)


def _wait(job, timeout=20.0):
    t0 = time.time()
    while job.status == "running" and time.time() - t0 < timeout:
        time.sleep(0.05)
    return job


def test_job_success_failure_and_after_hook():
    events = []
    kinds = {k.key: k for k in [
        _kind("ok", "print('step 1'); print('step 2')", after=lambda job: {"lines": job.n_lines}),
        _kind("bad", "import sys; print('boom'); sys.exit(3)"),
    ]}
    m = ScriptManager(kinds, on_event=lambda job, ev: events.append((job.kind, ev)))
    ok = _wait(m.start("ok", {}, "tester"))
    assert ok.status == "done" and "step 2" in ok.lines and ok.result == {"lines": 3}   # "$ nwis ..." + 2 lines
    bad = _wait(m.start("bad", {}, "tester"))
    assert bad.status == "failed" and bad.returncode == 3 and "boom" in bad.error
    assert events == [("ok", "started"), ("ok", "done"), ("bad", "started"), ("bad", "failed")]


def test_one_exclusive_job_at_a_time_and_cancel():
    cleaned = []
    kinds = {k.key: k for k in [
        _kind("slow", "import time; print('go', flush=True); time.sleep(60)", cleanup=lambda job: cleaned.append(job.id)),
        _kind("other", "print(1)"),
        _kind("side", "print(1)", exclusive=False),
    ]}
    m = ScriptManager(kinds)
    slow = m.start("slow", {}, "t")
    with pytest.raises(jobs.JobBusy):
        m.start("other", {}, "t")
    assert _wait(m.start("side", {}, "t")).status == "done"      # non-exclusive jobs run alongside
    m.cancel(slow.id)
    assert _wait(slow).status == "cancelled" and cleaned == [slow.id]
    assert _wait(m.start("other", {}, "t")).status == "done"


def test_unavailable_kind_and_bad_params_are_refused():
    def params(raw):
        if raw.get("x") == "bad":
            raise ValueError("x is bad")
        return raw
    kinds = {"k": _kind("k", "print(1)", check=lambda: "not here", params=params)}
    m = ScriptManager(kinds)
    with pytest.raises(ValueError, match="not here"):
        m.start("k", {}, "t")
    with pytest.raises(ValueError, match="x is bad"):
        m.start("k", {"x": "bad"}, "t")
    with pytest.raises(ValueError, match="unknown"):
        m.start("nope", {}, "t")


def _data_folder(path, users=True, log_rows=3):
    path.mkdir(parents=True)
    db = DB(path / "nwis.db")
    db.init()
    if users:
        auth.ensure_demo_users(db)
        auth._secret(db)
    audit.append_many(db, [audit.make_row("acknowledged", actor=f"user{i}", payload={"i": i}) for i in range(log_rows)])
    db.kv_set("marker", str(path.name))
    return db


def _kv(path, key):
    db = DB(path / "nwis.db")
    try:
        return db.kv_get(key)
    finally:
        db.close()


def test_rebuild_carries_users_secret_and_a_verifiable_decision_log(tmp_path):
    old = _data_folder(tmp_path / "data")
    (tmp_path / "data" / "public" / "sodir").mkdir(parents=True)
    (tmp_path / "data" / "public" / "sodir" / "x.csv").write_text("a,b\n", encoding="utf-8")
    staged = jobs.staged_dir(tmp_path / "data")
    new = _data_folder(staged, users=False, log_rows=0)       # what a fresh build looks like
    counts = jobs.finalize_build(staged, tmp_path / "data")
    assert counts["users"] == 3 and counts["decision_log"] == 3
    assert (staged / jobs.READY).exists() and (staged / "public" / "sodir" / "x.csv").exists()
    new = DB(staged / "nwis.db")
    assert new.kv_get("auth_secret") == old.kv_get("auth_secret")
    assert new.kv_get("marker") == staged.name                 # rebuilt content is not overwritten
    assert audit.verify_chain(new)["ok"] and audit.verify_chain(new)["n"] == 3
    assert auth.authenticate(new, "office", auth.config.DEMO_PASSWORD)["role"] == "office"


def test_startup_swap_moves_the_ready_build_into_place_and_keeps_one_backup(tmp_path):
    home = tmp_path / "data"
    _data_folder(home).close()
    unfinished = home.with_name("data.next-20000101000000")
    unfinished.mkdir()
    assert jobs.swap_pending_build(home, log=lambda m: None) is None       # nothing READY: no swap
    assert not unfinished.exists() and home.exists()

    for n in range(2):
        staged = home.with_name(f"data.next-2099010100000{n}")
        _data_folder(staged, users=False, log_rows=0).close()
        jobs.finalize_build(staged, home)
        backup = jobs.swap_pending_build(home, log=lambda m: None)
        time.sleep(1.1)                                                   # backups are named to the second
        assert backup is not None and backup.exists()
        assert _kv(home, "marker") == staged.name
        assert not (home / jobs.READY).exists()
    assert len(list(tmp_path.glob("data.bak-*"))) == 1
    con = sqlite3.connect(home / "nwis.db")
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
    con.close()


def test_retrain_from_reviews(tmp_path, monkeypatch):
    from nwis import config
    from nwis.ingest import nlp
    import nwis.validate.volve as volve
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    base = [{"text": t, "label": lab} for lab, texts in {
        "LOSS": ["partial losses of 40 bbl/hr while drilling", "lost circulation, pumped LCM pill",
                 "mud losses observed at the shoe", "total losses, spotted LCM"],
        "NONE": ["drilled ahead to 1,650 m", "circulated hole clean", "ran in hole with bit", "made up BHA"]}.items()
        for t in texts]
    monkeypatch.setattr(volve, "training_sentences", lambda: base)
    db = DB(tmp_path / "t.db")
    db.init()
    with pytest.raises(ValueError, match="no reviewed sentences"):
        nlp.retrain_from_reviews(db, log=lambda m: None)
    db.kv_set("verified_sentences", [{"text": "seepage losses 5 bbl/hr", "label": "LOSS"},
                                     {"text": "no losses observed", "label": "NONE"}])
    info = nlp.retrain_from_reviews(db, log=lambda m: None)
    assert info["n_base"] == 8 and info["n_verified"] == 2 and info["labels"] == {"LOSS": 1, "NONE": 1}
    assert db.kv_get("classifier_retrain")["n_verified"] == 2
    clf = nlp.SentenceClassifier.load(tmp_path / "sentence_clf.joblib")
    assert clf.pipe is not None
