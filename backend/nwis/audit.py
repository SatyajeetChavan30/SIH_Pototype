"""Decision black box: an append-only, hash-chained log of what NWIS showed and what people did.

Every alert opening, escalation, acknowledgement, clearance and engineer feedback is written as
one row. Each row stores the SHA-256 of (previous hash + its own canonical content), so editing or
deleting any past row breaks the chain and `verify_chain` reports the first bad sequence number.
There is deliberately no update or delete function: post-incident reviews and OISD audits need
an objective record of whether early signs were visible and acted on.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading

from .db import DB

GENESIS = "0" * 64
FIELDS = ("ts_wall", "session_id", "well_id", "t", "md", "alert_id", "alert_key", "hazard", "event", "level", "actor",
          "payload")
_lock = threading.Lock()


def _canonical(row: dict) -> str:
    return json.dumps({k: row.get(k) for k in FIELDS}, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev_hash: str, row: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(row)).encode("utf-8")).hexdigest()


def make_row(event: str, *, session_id: str | None = None, well_id: str | None = None, t: float | None = None,
             md: float | None = None, alert: dict | None = None, actor: str = "NWIS", payload: dict | None = None) -> dict:
    """Build an unsaved log row. `alert` is an Alert.to_dict()-like mapping (id, key, hazard, level...)."""
    a = alert or {}
    return {"ts_wall": dt.datetime.now().isoformat(timespec="seconds"), "session_id": session_id, "well_id": well_id,
            "t": None if t is None else round(float(t), 1), "md": None if md is None else round(float(md), 1),
            "alert_id": a.get("id"), "alert_key": a.get("key"), "hazard": a.get("hazard"), "event": event,
            "level": a.get("level"), "actor": actor,
            "payload": json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)}


def append_many(db: DB, rows: list[dict]) -> int:
    """Append rows to the chain; returns how many were written."""
    if not rows:
        return 0
    with _lock:
        last = db.one("SELECT hash FROM decision_log ORDER BY seq DESC LIMIT 1")
        prev = last["hash"] if last else GENESIS
        for r in rows:
            h = _digest(prev, r)
            db.execute(f"INSERT INTO decision_log ({','.join(FIELDS)}, prev_hash, hash) "
                       f"VALUES ({','.join('?' * (len(FIELDS) + 2))})", [r.get(k) for k in FIELDS] + [prev, h])
            prev = h
        db.commit()
    return len(rows)


def verify_chain(db: DB) -> dict:
    """Recompute every hash. ok=False and first_bad_seq point at the first tampered or missing link."""
    prev = GENESIS
    n = 0
    for r in db.query(f"SELECT seq, {','.join(FIELDS)}, prev_hash, hash FROM decision_log ORDER BY seq"):
        n += 1
        if r["prev_hash"] != prev or _digest(prev, r) != r["hash"]:
            return {"ok": False, "n": n, "first_bad_seq": r["seq"]}
        prev = r["hash"]
    return {"ok": True, "n": n, "first_bad_seq": None, "head": prev if n else None}


def query(db: DB, session_id: str | None = None, alert_id: str | None = None, alert_key: str | None = None,
          limit: int = 200) -> list[dict]:
    where, params = [], []
    for col, val in (("session_id", session_id), ("alert_id", alert_id), ("alert_key", alert_key)):
        if val:
            where.append(f"{col}=?")
            params.append(val)
    sql = "SELECT * FROM decision_log" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY seq DESC LIMIT ?"
    rows = db.query(sql, params + [int(limit)])
    for r in rows:
        r["payload"] = json.loads(r["payload"]) if r.get("payload") else {}
    return rows


def summary(db: DB) -> dict:
    by_event = {r["event"]: r["n"] for r in db.query("SELECT event, COUNT(*) n FROM decision_log GROUP BY event")}
    sessions = db.one("SELECT COUNT(DISTINCT session_id) n FROM decision_log")["n"]
    return {"by_event": by_event, "sessions": sessions, **verify_chain(db)}
