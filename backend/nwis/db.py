"""SQLite persistence (zero-install). Production would use PostgreSQL + PostGIS."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS structures (
  id TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL, prod_start INTEGER);
CREATE TABLE IF NOT EXISTS wells (
  id TEXT PRIMARY KEY, name TEXT, structure_id TEXT, lat REAL, lon REAL, spud_date TEXT, spud_year INTEGER,
  td_md REAL, td_tvd REAL, status TEXT, traj_type TEXT, target TEXT, mud_system TEXT, is_active INTEGER DEFAULT 0,
  synthetic INTEGER DEFAULT 1, source TEXT DEFAULT 'synthetic');
CREATE TABLE IF NOT EXISTS tops (
  well_id TEXT, formation TEXT, md REAL, tvd REAL, source TEXT, PRIMARY KEY (well_id, formation));
CREATE TABLE IF NOT EXISTS surveys (
  well_id TEXT, md REAL, inc REAL, azi REAL, tvd REAL, north REAL, east REAL);
CREATE INDEX IF NOT EXISTS ix_surveys_well ON surveys(well_id);
CREATE TABLE IF NOT EXISTS sections (
  well_id TEXT, idx INTEGER, hole TEXT, casing TEXT, top_md REAL, shoe_md REAL, mw_ppg REAL, ecd_ppg REAL,
  PRIMARY KEY (well_id, idx));
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, well_id TEXT, kind TEXT, title TEXT, path TEXT, pages INTEGER, ocr_pages INTEGER,
  status TEXT, created TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS pages (
  doc_id TEXT, page_no INTEGER, text TEXT, ocr INTEGER, ocr_conf REAL, PRIMARY KEY (doc_id, page_no));
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, well_id TEXT, hazard TEXT, subtype TEXT, md REAL, tvd REAL, formation TEXT, rel REAL,
  severity TEXT, rate_bbl_hr REAL, volume_bbl REAL, npt_hours REAL, mw_ppg REAL, ecd_ppg REAL, cause TEXT,
  date TEXT, confidence REAL, status TEXT, summary TEXT, resolved INTEGER, extra TEXT);
CREATE INDEX IF NOT EXISTS ix_events_well ON events(well_id);
CREATE TABLE IF NOT EXISTS event_actions (
  event_id TEXT, seq INTEGER, code TEXT, text TEXT, success INTEGER);
CREATE TABLE IF NOT EXISTS citations (
  event_id TEXT, doc_id TEXT, page_no INTEGER, start INTEGER, end INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS ix_cit_event ON citations(event_id);
CREATE TABLE IF NOT EXISTS lessons (
  id TEXT PRIMARY KEY, well_id TEXT, hazard TEXT, formation TEXT, text TEXT, doc_id TEXT, page_no INTEGER,
  start INTEGER, end INTEGER);
CREATE TABLE IF NOT EXISTS truth_events (
  id TEXT PRIMARY KEY, well_id TEXT, hazard TEXT, subtype TEXT, md REAL, tvd REAL, formation TEXT, rel REAL,
  severity TEXT, cause TEXT, npt_hours REAL, mw_ppg REAL, ecd_ppg REAL, attempts TEXT, extra TEXT);
CREATE TABLE IF NOT EXISTS review_queue (
  id TEXT PRIMARY KEY, doc_id TEXT, kind TEXT, payload TEXT, confidence REAL, reason TEXT, status TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS alert_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT, alert_key TEXT, hazard TEXT, useful INTEGER, note TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS decision_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts_wall TEXT, session_id TEXT, well_id TEXT, t REAL, md REAL, alert_id TEXT,
  alert_key TEXT, hazard TEXT, event TEXT, level TEXT, actor TEXT, payload TEXT, prev_hash TEXT, hash TEXT);
CREATE INDEX IF NOT EXISTS ix_dlog_session ON decision_log(session_id);
CREATE INDEX IF NOT EXISTS ix_dlog_alert ON decision_log(alert_id);
"""


class DB:
    def __init__(self, path: Path | str | None = None):
        self.path = str(path or config.DB_PATH)
        self._local = threading.local()

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
        return c

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        self.conn.executemany(sql, [tuple(r) for r in rows])

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        r = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None

    def commit(self) -> None:
        self.conn.commit()

    def insert(self, table: str, row: dict, replace: bool = True) -> None:
        cols = list(row.keys())
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in row.values()]
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        self.conn.execute(f"{verb} INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)

    def kv_set(self, key: str, value: Any) -> None:
        self.insert("kv", {"key": key, "value": json.dumps(value)})
        self.commit()

    def kv_get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default
