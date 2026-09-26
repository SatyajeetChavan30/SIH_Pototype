"""Operations the dashboard runs instead of command-line steps.

    build a knowledge base     (was: stratasense.cli build-demo / build-public)
    switch dataset             (was: STRATASENSE_REGION + STRATASENSE_DATA_DIR, restart)
    restart the server         (supervised by `stratasense.cli start`; otherwise the process re-executes itself)
    simulated rig feed         (was: stratasense.cli simulate-rig)
    install optional engines   (was: pip install "stratasense[ocr]" / "stratasense[asr]")

A rebuild never touches the knowledge base in use: it is built in a staging folder next to it by a separate
Python process (so it can be cancelled and cannot crash the server), and swapped in when the server next starts,
before anything opens the database. People's accounts and the decision log are carried over into the new base.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config
from .jobs import Job, run_process

RESTART_EXIT = 75          # exit code that tells the `stratasense.cli start` supervisor to start the server again
BUILD_OK = "BUILD_OK"      # marker written into a staging folder once its build finished cleanly
DATASETS = {
    "assam": {"label": "Synthetic Upper-Assam demo", "synthetic": True,
              "about": "About 60 fictitious offset wells built from published Upper-Assam geology, their DDR/WCR PDFs "
                       "(some scanned), drilling logs and the eRTMAC stream of the active well. Fully offline."},
    "norway": {"label": "Real public data: Norwegian North Sea (Sodir)", "synthetic": False,
               "about": "Real wellbores, formation tops, casing, mud and wellbore histories from the Norwegian "
                        "Offshore Directorate's FactPages (NLOD open licence). Live Ops replays real Volve rig data "
                        "(Equinor) once it has been imported with import-volve-stream."},
}
ASSAM_BUILD_STAGES = [          # (log text, progress when it appears, label)
    ("generating synthetic", 0.02, "Generating wells, tops and trajectories"),
    ("rendering DDR", 0.05, "Rendering daily drilling and completion reports"),
    ("training sentence classifier", 0.12, "Training the sentence classifier"),
    ("ingesting documents", 0.14, "Reading every report (NLP + OCR)"),
    ("evaluating extraction", 0.42, "Scoring extraction on held-out phrasing"),
    ("evaluating OCR", 0.47, "Scoring OCR on scanned reports"),
    ("writing drilling-parameter", 0.62, "Writing drilling logs and the eRTMAC stream"),
    ("preparing ingestion demo", 0.66, "Preparing ingestion samples"),
    ("training risk model", 0.68, "Training the risk model (leave-wells-out)"),
    ("replaying the active well", 0.80, "Replaying the active well for alert evaluation"),
    ("done in", 0.99, "Finishing"),
]
NORWAY_BUILD_STAGES = [
    ("downloading", 0.05, "Downloading FactPages exports"),
    ("wellbores", 0.25, "Loading wellbores, tops, casing and mud"),
    ("group tops", 0.35, "Loading formation tops"),
    ("wellbore histories", 0.70, "Reading wellbore histories (NLP)"),
    ("training the risk model", 0.75, "Training the risk model"),
]
COMPONENTS = {
    "ocr": {"label": "OCR engine for scanned reports (RapidOCR, ONNX)", "packages": ["rapidocr-onnxruntime>=1.3"]},
    "asr": {"label": "Speech-to-text for voice memos (faster-whisper)", "packages": ["faster-whisper>=1.0"]},
}


# ---------------------------------------------------------------------------------------------- datasets
def data_dir(region: str) -> Path:
    if region == config.REGION:
        return config.DATA_DIR
    return config.data_dir_for(region)


def staging_of(final: Path) -> Path:
    return final.with_name(final.name + ".staging")


def _read_kv(db_path: Path, key: str):
    import json
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            r = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        finally:
            con.close()
        return json.loads(r[0]) if r else None
    except sqlite3.Error:
        return None


def dataset_status() -> dict:
    out = {}
    for code, d in DATASETS.items():
        final = data_dir(code)
        config.migrate_legacy_db(final)
        built = (final / config.DB_NAME).exists()
        info = _read_kv(final / config.DB_NAME, "build_info") if built and code != config.REGION else None
        out[code] = {**d, "code": code, "built": built, "current": code == config.REGION, "path": str(final),
                     "stream": (final / "logs" / "active_stream.npz").exists(),
                     "stream_source": _read_kv(final / config.DB_NAME, "stream_source") if built else None,
                     "staged": (staging_of(final) / BUILD_OK).exists(), "build_info": info}
    out["norway"]["needs"] = None if (config.data_dir_for("assam") / "models" / "sentence_clf.joblib").exists() else \
        "Build the synthetic Assam demo first: its report-reading model is reused for the North Sea histories."
    return out


def build_dataset(job: Job, region: str, quadrants: str = "15,16", download: bool = False,
                  csv_dir: Path | None = None) -> dict:
    """Build a knowledge base into the staging folder; `promote_staged` swaps it in on the next start."""
    if region not in DATASETS:
        raise ValueError(f"unknown dataset {region}")
    final = data_dir(region)
    st = staging_of(final)
    shutil.rmtree(st, ignore_errors=True)
    st.mkdir(parents=True)
    env = {"STRATASENSE_DATA_DIR": str(st), "STRATASENSE_REGION": region}
    if region == "assam":
        args = [sys.executable, "-m", "stratasense.cli", "build-demo"]
        stages = ASSAM_BUILD_STAGES
    else:
        need = dataset_status()["norway"]["needs"]
        if need:
            raise RuntimeError(need)
        dst = st / "public" / "sodir"
        src = csv_dir or (final / "public" / "sodir")
        if src.exists() and any(src.glob("*.csv")):
            shutil.copytree(src, dst, dirs_exist_ok=True)
            job.log(f"Using {len(list(dst.glob('*.csv')))} FactPages CSV exports")
        elif not download:
            raise RuntimeError("No FactPages CSV exports yet: tick 'download from Sodir' or upload the CSV files.")
        volve = final / "public" / "volve"      # imported Volve rig stream (import-volve-stream): keep it
        if volve.exists():
            shutil.copytree(volve, st / "public" / "volve", dirs_exist_ok=True)
        q = ",".join(x.strip() for x in (quadrants or "").split(",") if x.strip()) or "all"
        args = [sys.executable, "-m", "stratasense.cli", "build-public", "--quadrants", q] + (["--download"] if download else [])
        stages = NORWAY_BUILD_STAGES
    job.progress, job.stage = 0.0, "Starting"

    def on_line(line: str):
        low = line.lower()
        for key, p, label in stages:
            if key.lower() in low and (job.progress or 0) <= p:
                job.progress, job.stage = p, label

    run_process(job, args, env, cwd=str(config.ROOT / "backend"), on_line=on_line)
    if not (st / config.DB_NAME).exists():
        raise RuntimeError("the build finished without producing a knowledge base")
    (st / BUILD_OK).write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    job.stage = "Built; loading it"
    return {"region": region, "staging": str(st)}


VOLVE_STAGES = [
    ("scanned", 0.10, "Reading the log headers"),
    ("reading", 0.15, "Reading the real-time logs"),
    ("parsed", 0.20, "Reading the real-time logs"),
    ("coded incidents", 0.80, "Matching the drilling reports' incidents"),
    ("window", 0.85, "Picking the replay window"),
    ("installed", 0.95, "Installing the stream"),
]


def import_volve(job: Job, folder: str, ddr_folder: str | None = None, wellbore: str | None = None,
                 hours: float = 12.0) -> dict:
    """Run `import-volve-stream` for the North Sea dataset in a helper process (the XML can run to gigabytes)."""
    if not dataset_status()["norway"]["built"]:
        raise RuntimeError("Build the North Sea knowledge base first: the Volve stream is added to it.")
    env = {"STRATASENSE_DATA_DIR": str(data_dir("norway")), "STRATASENSE_REGION": "norway"}
    args = [sys.executable, "-m", "stratasense.cli", "import-volve-stream", folder, "--hours", str(hours)]
    if ddr_folder:
        args += ["--ddr", ddr_folder]
    if wellbore:
        args += ["--wellbore", wellbore]
    job.progress, job.stage = 0.0, "Starting"

    def on_line(line: str):
        low = line.lower()
        for key, p, label in VOLVE_STAGES:
            if key in low and (job.progress or 0) <= p:
                job.progress, job.stage = p, label

    run_process(job, args, env, cwd=str(config.ROOT / "backend"), on_line=on_line)
    return {"region": "norway", "restart": config.REGION == "norway"}


def _carry_over(old_db: Path, new_db: Path) -> None:
    """Keep people's accounts, the session key and the decision log across a rebuild."""
    from . import auth
    from .db import DB
    db = DB(new_db)
    try:
        auth.ensure_schema(db)         # the users table is created on first load; make sure it exists now
    finally:
        db.close()                     # an open handle stops Windows from renaming the staging folder
    con = sqlite3.connect(new_db)
    try:
        con.execute("ATTACH DATABASE ? AS old", (str(old_db),))
        tables = {r[0] for r in con.execute("SELECT name FROM old.sqlite_master WHERE type='table'")}
        if "users" in tables:
            con.execute("INSERT OR REPLACE INTO users SELECT * FROM old.users")
        if "kv" in tables:
            con.execute("INSERT OR REPLACE INTO kv SELECT * FROM old.kv WHERE key='auth_secret'")
        if "decision_log" in tables and not con.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]:
            con.execute("INSERT INTO decision_log SELECT * FROM old.decision_log")
        con.commit()
        con.execute("DETACH DATABASE old")
    finally:
        con.close()


def promote_staged(final: Path, log=print) -> bool:
    """Swap a finished staging build in for `final`. Must run before the database is opened."""
    st = staging_of(final)
    if not (st / BUILD_OK).exists():
        return False
    config.migrate_legacy_db(st)
    config.migrate_legacy_db(final)
    con = sqlite3.connect(st / config.DB_NAME)
    try:   # stored document paths point into the staging folder
        con.execute("UPDATE documents SET path = ? || substr(path, ?) WHERE path LIKE ?",
                    (str(final), len(str(st)) + 1, str(st) + "%"))
        con.commit()
    finally:
        con.close()
    if (final / config.DB_NAME).exists():
        try:
            _carry_over(final / config.DB_NAME, st / config.DB_NAME)
        except sqlite3.Error as e:
            log(f"[stratasense] could not carry accounts over from the previous knowledge base: {e}")
    old = final.with_name(final.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    try:
        if final.exists():
            final.rename(old)
        st.rename(final)
    except OSError as e:   # e.g. a file in the old folder is open in another program (Windows)
        log(f"[stratasense] could not switch to the new knowledge base yet ({e}); it stays ready in {st}")
        if old.exists() and not final.exists():
            old.rename(final)
        return False
    (final / BUILD_OK).unlink(missing_ok=True)
    shutil.rmtree(old, ignore_errors=True)
    log(f"[stratasense] new knowledge base in {final}")
    return True


def promote_all(log=print) -> None:
    for region in DATASETS:
        promote_staged(data_dir(region), log)


def copy_accounts(src_db: Path, dst_db: Path) -> None:
    """Switching dataset: accounts created in one knowledge base also work in the other, and signed-in people
    stay signed in (same session-signing key)."""
    if not (src_db.exists() and dst_db.exists()):
        return
    from . import auth
    from .db import DB
    db = DB(dst_db)
    try:
        auth.ensure_schema(db)
    finally:
        db.close()                     # do not keep a handle on the other dataset's folder (Windows renames)
    con = sqlite3.connect(dst_db)
    try:
        con.execute("ATTACH DATABASE ? AS src", (str(src_db),))
        if con.execute("SELECT 1 FROM src.sqlite_master WHERE name='users'").fetchone():
            con.execute("INSERT OR IGNORE INTO users SELECT * FROM src.users")
        con.execute("INSERT OR REPLACE INTO kv SELECT * FROM src.kv WHERE key='auth_secret'")
        con.commit()
        con.execute("DETACH DATABASE src")
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------- restart
BOOT_ID = f"{os.getpid()}-{int(time.time())}"


def supervised() -> bool:
    return os.environ.get("STRATASENSE_SUPERVISED") == "1"


def restart_soon(delay: float = 0.8) -> None:
    """Restart the server process after the current response has been sent."""
    def go():
        time.sleep(delay)
        sys.stdout.flush()
        if supervised():
            os._exit(RESTART_EXIT)
        argv = [sys.executable, *list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])[1:]]
        if os.name == "nt":     # Windows execv joins argv with bare spaces: quote paths such as "D:\pd\chosen one\..."
            argv = [subprocess.list2cmdline([a]) for a in argv]
        os.execv(sys.executable, argv)
    threading.Thread(target=go, daemon=True, name="stratasense-restart").start()


# ---------------------------------------------------------------------------------------------- rig simulator
class RigSimulator:
    """The stored active-well stream sent as real WITS-0 frames into StrataSense's own listener (a rig stand-in)."""

    def __init__(self):
        self.thread: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self.frames = 0
        self.total = 0
        self.md: float | None = None
        self.speed = 0.0
        self.error: str | None = None
        self.target: str | None = None
        self.started: float | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def available(self) -> bool:
        return (config.LOGS_DIR / "active_stream.npz").exists()

    def start(self, port: int, speed: float, from_md: float | None, from_index: int | None = None) -> None:
        from .realtime import simulator
        self.stop()
        start = from_index if from_index is not None else simulator.start_index_for_md(from_md) if from_md else 0
        import numpy as np
        self.total = int(len(np.load(config.LOGS_DIR / "active_stream.npz")["t"])) - start
        self.stop_evt = threading.Event()
        self.frames, self.md, self.speed, self.error = 0, None, speed, None
        self.target, self.started = f"127.0.0.1:{port}", time.time()

        def on_frame(n, pkt):
            self.frames = n
            for line in pkt.splitlines():
                if line.startswith("0110"):      # WITS record 01 item 10: bit depth (MD)
                    try:
                        self.md = float(line[4:])
                    except ValueError:
                        pass

        def run():
            for attempt in range(15):           # the listener may still be binding
                if self.stop_evt.is_set():
                    return
                try:
                    simulator.run(self.target, None, speed, start, None, log=lambda *_: None,
                                  stop=self.stop_evt, on_frame=on_frame)
                    return
                except OSError as e:
                    self.error = f"cannot reach the WITS-0 listener on port {port}: {e}"
                    if self.stop_evt.wait(1.0):
                        return
            self.error = (self.error or "") + " (gave up)"
        self.thread = threading.Thread(target=run, daemon=True, name="stratasense-rig-sim")
        self.thread.start()

    def stop(self) -> None:
        self.stop_evt.set()
        if self.thread is not None:
            self.thread.join(timeout=3)
        self.thread = None

    def status(self) -> dict:
        return {"available": self.available(), "running": self.running, "frames": self.frames, "total": self.total,
                "bit_md": self.md, "speed": self.speed, "target": self.target, "error": self.error,
                "started": self.started}


SIM = RigSimulator()


# ---------------------------------------------------------------------------------------------- optional engines
def component_status() -> dict:
    from .ingest.ocr import ocr_status
    from .ingest.voice import asr_status
    from .llm import llm_status
    return {"ocr": {**COMPONENTS["ocr"], **ocr_status()}, "asr": {**COMPONENTS["asr"], **asr_status()},
            "llm": llm_status()}


def install_component(job: Job, name: str) -> dict:
    if name not in COMPONENTS:
        raise ValueError(f"unknown component {name}")
    job.stage = f"Installing {COMPONENTS[name]['label']}"
    run_process(job, [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *COMPONENTS[name]["packages"]])
    importlib.invalidate_caches()
    from .ingest.ocr import get_ocr_engine
    get_ocr_engine.cache_clear()
    st = component_status()[name]
    job.log(f"{'Ready' if st['available'] else 'Installed, but not usable yet: restart the server'}.")
    return st
