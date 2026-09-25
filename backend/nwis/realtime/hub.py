"""Live hub: one shared NWIS session per real rig feed, broadcast to every connected console.

A replay is private to each browser (anyone can jump or pause). A real feed is not: the RTOC wall screen,
the duty engineer and the rig-site tablet must all see the same alerts, and an acknowledgement on one
console is an acknowledgement for all. The hub polls its stream source every second, steps the session over
the new samples, flushes decision-log rows, and fans the result out to subscriber queues.
"""
from __future__ import annotations

import asyncio
from typing import Callable

from .. import config
from .engine import LiveSession
from .sources import StreamSource


def zones_payload(session: LiveSession) -> list[dict]:
    return [session._zone_payload(z) | {"evidence": z["evidence"][:6]} for z in session.zones]


def init_payload(session: LiveSession, well_summary: dict, sections: list) -> dict:
    return {"type": "init", "session_id": session.session_id, "well": well_summary, "episodes": session.episodes,
            "zones": zones_payload(session), "tops": session.tops, "window": session.win["formations"],
            "grid": session.win["grid"], "status": session.status(), "alerts": session.alerts.snapshot(),
            "ribbon": session.ribbon(0, 6000), "sections": sections, "mode": "live" if session.live else "replay"}


def tick_payload(session: LiveSession, r: dict, full: bool) -> dict:
    r["type"] = "tick"
    for smp in r["samples"]:
        smp.pop("fm_est", None)
    if full or r["events"]:
        r["ribbon"] = session.ribbon(0, 6000)
        r["zones"] = zones_payload(session)
        r["tops"] = session.tops
    return r


class LiveHub:
    def __init__(self, kb, model, source: StreamSource, flush_audit: Callable[[list[dict]], None],
                 analogs_getter: Callable[[], object] | None = None, gap_s: float | None = None):
        self.source = source.start()
        self.session = LiveSession(kb, model, None, source=source)
        self.flush_audit = flush_audit
        self.analogs_getter = analogs_getter
        self.gap_s = gap_s if gap_s is not None else config.STREAM_GAP_S
        self.lock = asyncio.Lock()
        self.subs: set[asyncio.Queue] = set()
        self.in_gap = False
        self.loops = 0

    # ------------------------------------------------------------------ subscribers
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=300)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)

    def broadcast(self, msg: dict) -> None:
        for q in list(self.subs):
            if q.full():                       # a slow console loses its oldest tick, never blocks the hub
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)

    # ------------------------------------------------------------------ loop
    def _gap_check(self) -> None:
        age = self.source.stats()["last_packet_age_s"]
        s = self.session.recent[-1] if self.session.recent else {"t": 0.0, "md": 0.0}
        if age is not None and age > self.gap_s and not self.in_gap:
            self.in_gap = True
            ev = {"type": "stream_gap", "t": s["t"], "md": round(s["md"], 1), "source": "stream",
                  "message": f"No data from {self.source.describe()} for {age / 60:.0f} min: alerts and look-ahead "
                             f"are frozen at the last bit depth until the feed resumes"}
            self.session.events_log.append(ev)
            self.session.history_events.append(ev)
        elif self.in_gap and age is not None and age <= self.gap_s:
            self.in_gap = False
            ev = {"type": "stream_resumed", "t": s["t"], "md": round(s["md"], 1), "source": "stream",
                  "message": f"Feed resumed ({self.source.describe()})"}
            self.session.events_log.append(ev)
            self.session.history_events.append(ev)

    async def step_once(self) -> dict | None:
        rows = self.source.poll()
        async with self.lock:
            if self.session.analogs is None and self.analogs_getter is not None:
                self.session.analogs = self.analogs_getter()
            if rows:
                await asyncio.to_thread(self.session.ingest, rows)
            pending = self.session.n - self.session.i
            r = await asyncio.to_thread(self.session.step, min(pending, 600)) if pending > 0 else None
            self._gap_check()
            if r is None:
                r = {"samples": [], "alerts": self.session.alerts.pop_changed(), "status": self.session.status(),
                     "events": self.session._pop_events(), "done": False}
            self.flush_audit(self.session.pop_audit())
            self.loops += 1
            msg = tick_payload(self.session, r, full=self.loops % 10 == 0)
        self.broadcast(msg)
        return msg

    async def run(self, interval: float = 1.0) -> None:
        while True:
            try:
                await self.step_once()
            except Exception as e:  # noqa: BLE001 - one bad packet must never stop the live console
                self.source.last_error = f"engine: {e}"[:200]
            await asyncio.sleep(interval)
