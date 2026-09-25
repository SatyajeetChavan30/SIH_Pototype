"""Background jobs started from the dashboard (knowledge-base builds, bulk imports, evaluations, installs).

A job runs on its own thread and keeps a log the browser polls, so long operations show progress in the page
instead of in a terminal. Jobs marked `exclusive` (anything that rebuilds data or installs software) never run
two at a time. Jobs live in memory: a server restart forgets them, which the UI treats as "finished".
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from typing import Callable

MAX_LOG = 4000


class JobCancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str, title: str, exclusive: bool):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.title, self.exclusive = kind, title, exclusive
        self.status = "running"            # running | done | failed | cancelled
        self.lines: list[str] = []
        self.started = time.time()
        self.finished: float | None = None
        self.result = None
        self.error: str | None = None
        self.progress: float | None = None   # 0..1 when the job knows how far it is
        self.stage: str | None = None        # human-readable current step
        self.cancel_requested = False
        self.proc: subprocess.Popen | None = None

    def log(self, msg) -> None:
        for line in str(msg).splitlines() or [""]:
            self.lines.append(line.rstrip())
        if len(self.lines) > MAX_LOG:
            del self.lines[: len(self.lines) - MAX_LOG]

    def check_cancel(self) -> None:
        if self.cancel_requested:
            raise JobCancelled()

    def payload(self, since: int = 0) -> dict:
        end = self.finished or time.time()
        return {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
                "started": self.started, "finished": self.finished, "elapsed_s": round(end - self.started, 1),
                "progress": self.progress, "stage": self.stage, "error": self.error, "result": self.result,
                "n_lines": len(self.lines), "lines": self.lines[since:] if since else self.lines[-400:],
                "cancellable": self.status == "running"}


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def running_exclusive(self) -> Job | None:
        return next((j for j in self.jobs.values() if j.exclusive and j.status == "running"), None)

    def start(self, kind: str, title: str, fn: Callable[[Job], object], exclusive: bool = False,
              on_success: Callable[[Job], None] | None = None) -> Job:
        with self.lock:
            busy = self.running_exclusive()
            if exclusive and busy is not None:
                raise RuntimeError(f"'{busy.title}' is still running; wait for it to finish or cancel it")
            job = Job(kind, title, exclusive)
            self.jobs[job.id] = job

        def run():
            try:
                job.result = fn(job)
                job.status = "done"
            except JobCancelled:
                job.status = "cancelled"
                job.log("Cancelled.")
            except Exception as e:  # noqa: BLE001 - any failure must end up in the job log, not kill the server
                job.status = "cancelled" if job.cancel_requested else "failed"
                job.error = str(e)[:500] or type(e).__name__
                job.log(traceback.format_exc(limit=4))
            finally:
                job.finished = time.time()
                if job.progress is not None and job.status == "done":
                    job.progress = 1.0
            if job.status == "done" and on_success is not None:
                try:
                    on_success(job)
                except Exception as e:  # noqa: BLE001
                    job.log(f"Follow-up step failed: {e}")
        threading.Thread(target=run, daemon=True, name=f"nwis-job-{kind}").start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status != "running":
            return False
        job.cancel_requested = True
        if job.proc is not None and job.proc.poll() is None:
            job.proc.terminate()
        return True

    def list(self) -> list[dict]:
        rows = sorted(self.jobs.values(), key=lambda j: -j.started)[:30]
        return [{k: v for k, v in j.payload().items() if k != "lines"} for j in rows]


def run_process(job: Job, args: list[str], env: dict | None = None, cwd: str | None = None,
                on_line: Callable[[str], None] | None = None) -> None:
    """Run a Python helper process, streaming its output into the job log; raises if it fails."""
    full_env = {**os.environ, **(env or {}), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    job.log(f"$ {' '.join(os.path.basename(a) if i == 0 else a for i, a in enumerate(args))}")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    job.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env=full_env, cwd=cwd, creationflags=flags)
    assert job.proc.stdout is not None
    for line in job.proc.stdout:
        job.log(line)
        if on_line:
            on_line(line)
    rc = job.proc.wait()
    if job.cancel_requested:
        raise JobCancelled()
    if rc != 0:
        raise RuntimeError(f"step failed (exit code {rc}); see the log above")


JOBS = JobManager()
