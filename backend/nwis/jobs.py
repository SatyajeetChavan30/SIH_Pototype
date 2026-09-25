"""Background jobs started from the dashboard: evaluations, retraining, rebuilds and the rig simulator.

Every job is an `nwis.cli` command run as a subprocess, so it streams its own progress lines, can be cancelled by
stopping the process, and never shares the server's SQLite connections (the DB is in WAL mode, so a writer
subprocess is safe). What each kind runs, who may start it and when it is available is declared by the API
(`api/main.py`), which also knows the server state that the after-hooks refresh.

Rebuilds never write into the folder being served. They build into a staged sibling folder
(`<data>.next-<timestamp>`); on success the rows a rebuild must not lose (users, the session-signing secret, the
decision log, alert feedback) are copied across and the folder is marked READY. The server then either switches
to it at once or, when that is not possible, `swap_pending_build` moves it into place at the next start, before
any database handle is open. The previous folder is kept as `<data>.bak-<timestamp>` (the newest one only).
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config

READY = "READY"
CARRY_TABLES = ("users", "decision_log", "alert_feedback")
CARRY_KV = ("auth_secret",)
MAX_JOBS = 20
MAX_LINES = 400
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")   # colour codes some libraries print (e.g. RapidOCR's logger)


class JobBusy(RuntimeError):
    """Another exclusive job is already running."""


@dataclass
class Kind:
    key: str
    label: str
    description: str
    role: str                                         # "office" (office + admin) or "admin"
    argv: Callable[[dict], list[str]]                 # nwis.cli arguments for validated params
    params: Callable[[dict], dict] = lambda raw: {}   # validates raw params; raises ValueError
    env: Callable[[dict], dict] = lambda p: {}        # extra environment for the subprocess
    check: Callable[[], str | None] = lambda: None    # reason the kind cannot run now, or None
    before: Callable[["Job"], None] | None = None     # runs in the server before the subprocess starts
    after: Callable[["Job"], dict | None] | None = None   # runs in the server after a successful exit
    cleanup: Callable[["Job"], None] | None = None    # runs after a failed or cancelled run
    exclusive: bool = True                            # only one exclusive job at a time

    def describe(self) -> dict:
        reason = self.check()
        return {"key": self.key, "label": self.label, "description": self.description, "role": self.role,
                "exclusive": self.exclusive, "available": reason is None, "reason": reason}


@dataclass
class Job:
    kind: str
    params: dict
    actor: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "running"                           # running | done | failed | cancelled
    started: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    finished: str | None = None
    returncode: int | None = None
    result: dict | None = None
    error: str | None = None
    lines: deque = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    n_lines: int = 0
    workdir: Path | None = None                       # staged data folder of a build
    _proc: subprocess.Popen | None = None
    _cancel: bool = False

    def log(self, line: str) -> None:
        self.lines.append(ANSI.sub("", line).rstrip())
        self.n_lines += 1

    def to_dict(self, tail: int | None = None) -> dict:
        lines = list(self.lines)
        return {"id": self.id, "kind": self.kind, "params": self.params, "actor": self.actor, "status": self.status,
                "started": self.started, "finished": self.finished, "returncode": self.returncode,
                "result": self.result, "error": self.error, "n_lines": self.n_lines,
                "lines": lines if tail is None else lines[-tail:]}


class JobManager:
    def __init__(self, kinds: dict[str, Kind], on_event: Callable[[Job, str], None] | None = None,
                 python: str = sys.executable, cwd: Path | None = None):
        self.kinds = kinds
        self.on_event = on_event or (lambda job, ev: None)
        self.python = python
        self.cwd = cwd or (config.ROOT / "backend")
        self.jobs: list[Job] = []
        self._lock = threading.Lock()

    def command(self, kind: Kind, params: dict) -> list[str]:
        return [self.python, "-m", "nwis.cli", *kind.argv(params)]

    def describe(self) -> dict:
        return {"kinds": [k.describe() for k in self.kinds.values()], "jobs": [j.to_dict(tail=0) for j in self.jobs]}

    def get(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def running(self, kind: str | None = None) -> list[Job]:
        return [j for j in self.jobs if j.status == "running" and (kind is None or j.kind == kind)]

    def start(self, kind_key: str, raw_params: dict | None, actor: str) -> Job:
        kind = self.kinds.get(kind_key)
        if kind is None:
            raise ValueError(f"unknown job {kind_key!r}")
        params = kind.params(raw_params or {})
        with self._lock:
            reason = kind.check()
            if reason:
                raise ValueError(reason)
            clash = [j for j in self.running() if j.kind == kind.key or (kind.exclusive and self.kinds[j.kind].exclusive)]
            if clash:
                raise JobBusy(f"{self.kinds[clash[0].kind].label} is already running")
            job = Job(kind.key, params, actor)
            self.jobs.append(job)
            del self.jobs[:-MAX_JOBS]
        try:
            if kind.before:
                kind.before(job)
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                   "NWIS_DATA_DIR": str(config.DATA_DIR), "NWIS_REGION": config.REGION, **kind.env(job.params)}
            if job.workdir is not None:
                env["NWIS_DATA_DIR"] = str(job.workdir)
            argv = self.command(kind, job.params)
            job.log("$ nwis " + " ".join(kind.argv(job.params)))
            job._proc = subprocess.Popen(argv, cwd=self.cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 - report any start failure on the job itself
            self._finish(job, kind, "failed", error=str(e))
            return job
        self.on_event(job, "started")
        threading.Thread(target=self._watch, args=(job, kind), daemon=True, name=f"job-{job.id}").start()
        return job

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is None or job.status != "running":
            return job
        job._cancel = True
        if job._proc is not None and job._proc.poll() is None:
            job._proc.terminate()
        return job

    def _watch(self, job: Job, kind: Kind) -> None:
        proc = job._proc
        for line in proc.stdout:
            job.log(line)
        job.returncode = proc.wait()
        if job._cancel:
            self._finish(job, kind, "cancelled")
        elif job.returncode != 0:
            last = next((l for l in reversed(job.lines) if l.strip()), "")
            self._finish(job, kind, "failed", error=f"exit code {job.returncode}: {last}"[:400])
        else:
            try:
                result = kind.after(job) if kind.after else None
                self._finish(job, kind, "done", result=result)
            except Exception as e:  # noqa: BLE001 - the job ran but applying it failed
                self._finish(job, kind, "failed", error=f"finished, but applying the result failed: {e}")

    def _finish(self, job: Job, kind: Kind, status: str, result: dict | None = None, error: str | None = None) -> None:
        if status in ("failed", "cancelled") and kind.cleanup:
            try:
                kind.cleanup(job)
            except Exception as e:  # noqa: BLE001
                job.log(f"cleanup failed: {e}")
        job.status, job.result, job.error = status, result, error
        job.finished = dt.datetime.now().isoformat(timespec="seconds")
        if error:
            job.log(error)
        self.on_event(job, status)


# ---------------------------------------------------------------------------------------------- data folders
def staged_dir(home: Path) -> Path:
    return home.with_name(f"{home.name}.next-{dt.datetime.now():%Y%m%d%H%M%S}")


def carry_over(src_db: Path, dst_db: Path) -> dict:
    """Copy users, the session secret, the decision log and alert feedback from the old database into a rebuilt one.

    decision_log rows are copied verbatim (same seq, prev_hash, hash), so the hash chain still verifies."""
    from . import auth
    from .db import DB
    dst = DB(dst_db)
    dst.init()
    auth.ensure_schema(dst)
    dst.close()
    counts = {}
    if not Path(src_db).exists():
        return counts
    con = sqlite3.connect(str(dst_db))
    try:
        con.execute("ATTACH DATABASE ? AS old", (str(src_db),))
        old_tables = {r[0] for r in con.execute("SELECT name FROM old.sqlite_master WHERE type='table'")}
        for t in CARRY_TABLES:
            if t not in old_tables:
                continue
            cols = [r[1] for r in con.execute(f"PRAGMA main.table_info({t})")]
            old_cols = {r[1] for r in con.execute(f"PRAGMA old.table_info({t})")}
            cols = [c for c in cols if c in old_cols]
            col_sql = ",".join(cols)
            con.execute(f"DELETE FROM main.{t}")
            con.execute(f"INSERT INTO main.{t} ({col_sql}) SELECT {col_sql} FROM old.{t}")
            counts[t] = con.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
        if "kv" in old_tables:
            marks = ",".join("?" * len(CARRY_KV))
            con.execute(f"INSERT OR REPLACE INTO main.kv SELECT * FROM old.kv WHERE key IN ({marks})", CARRY_KV)
        con.commit()
        con.execute("DETACH DATABASE old")
    finally:
        con.close()
    return counts


def finalize_build(staged: Path, carry_from: Path) -> dict:
    """Make a finished build complete and mark it READY: carried-over rows and the cached public-data downloads."""
    counts = carry_over(carry_from / "nwis.db", staged / "nwis.db")
    public = carry_from / "public"
    if public.is_dir() and not (staged / "public").exists():
        shutil.copytree(public, staged / "public")
    (staged / READY).write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return counts


def swap_pending_build(home: Path, log=print) -> Path | None:
    """At start-up: move the newest READY staged build into `home`, keeping the old folder as the one backup.

    Staged folders that never finished (no READY marker) are removed. Returns the backup path, if any."""
    home = Path(home)
    staged = sorted(p for p in home.parent.glob(f"{home.name}.next-*") if p.is_dir())
    ready = [p for p in staged if (p / READY).exists()]
    for p in staged:
        if not ready or p != ready[-1]:
            shutil.rmtree(p, ignore_errors=True)
    if not ready:
        return None
    backup = None
    if home.exists():
        backup = home.with_name(f"{home.name}.bak-{dt.datetime.now():%Y%m%d%H%M%S}")
        home.rename(backup)
    ready[-1].rename(home)
    (home / READY).unlink(missing_ok=True)
    for old in sorted(home.parent.glob(f"{home.name}.bak-*")):
        if old != backup:
            shutil.rmtree(old, ignore_errors=True)
    log(f"[nwis] switched to the rebuilt knowledge base; previous data kept in {backup.name if backup else '-'}")
    return backup
