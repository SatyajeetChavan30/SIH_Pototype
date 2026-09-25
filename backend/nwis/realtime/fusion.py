"""Alert fusion: de-duplication, hysteresis, cool-down and escalation when an offset
look-ahead warning is *corroborated* by real-time symptoms (alert-fatigue control)."""
from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field

LEVELS = {"info": 0, "watch": 1, "warning": 2, "critical": 3}
CLEAR_AFTER_S = 900        # detector alert auto-clears after 15 min without supporting signals
COOLDOWN_S = 1800          # same key cannot re-open within 30 min unless more severe
_ids = itertools.count(1)


@dataclass
class Alert:
    id: str
    key: str
    hazard: str
    source: str                 # look-ahead | real-time | mud-window | fused | geology
    level: str
    title: str
    message: str
    md: float
    t: float
    formation: str | None = None
    confidence: float = 0.0
    drivers: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    recommendations: dict | None = None
    analogs: list = field(default_factory=list)
    zone: dict | None = None
    corroborated: bool = False
    p_value: float | None = None   # conformal p-value vs this well's recent normal drilling (None = uncalibrated)
    calibrated: bool = False
    status: str = "active"      # active | acknowledged | cleared
    updated_t: float = 0.0
    pushed_t: float = 0.0
    count: int = 1
    history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class AlertManager:
    def __init__(self, on_event=None):
        self.alerts: dict[str, Alert] = {}
        self.closed: dict[str, float] = {}
        self.changed: set[str] = set()
        # decision-log hook: on_event(alert, event, t, md, extra) for opened/escalated/cleared/acknowledged
        self.on_event = on_event

    def _log(self, a: Alert, event: str, t: float | None, md: float | None, **extra) -> None:
        if self.on_event is not None:
            self.on_event(a, event, t, md, extra)

    def upsert(self, key: str, hazard: str, source: str, level: str, title: str, message: str, md: float, t: float,
               **kw) -> Alert | None:
        a = self.alerts.get(key)
        if a is None or a.status == "cleared":
            last = self.closed.get(key)
            if last is not None and t - last < COOLDOWN_S and (a is None or LEVELS[level] <= LEVELS[a.level]):
                return None
            a = Alert(id=f"A{next(_ids):04d}", key=key, hazard=hazard, source=source, level=level, title=title,
                      message=message, md=md, t=t, updated_t=t, **kw)
            a.history.append({"t": t, "md": md, "level": level, "event": "opened", "title": title})
            self.alerts[key] = a
            self.changed.add(key)
            self._log(a, "opened", t, md, title=title, message=message, confidence=a.confidence,
                      corroborated=a.corroborated, source=source, drivers=a.drivers[:4])
            return a
        escalate = LEVELS[level] > LEVELS[a.level]
        a.updated_t = t
        a.count += 1
        a.md = md if a.source != "look-ahead" else a.md
        if escalate or a.title != title or abs(a.confidence - kw.get("confidence", a.confidence)) > 0.1 \
                or t - a.pushed_t >= 300:
            if escalate:
                a.history.append({"t": t, "md": md, "level": level, "event": "escalated", "title": title})
                if a.status == "acknowledged":
                    a.status = "active"
            a.level = max(a.level, level, key=lambda x: LEVELS[x])
            a.title, a.message = title, message
            for k, v in kw.items():
                if v not in (None, [], {}):
                    setattr(a, k, v)
            self.changed.add(key)
            if escalate:
                self._log(a, "escalated", t, md, title=title, message=message, confidence=a.confidence,
                          corroborated=a.corroborated)
        return a

    def freeze(self, dt: float) -> None:
        """Pause the staleness clock (connections, well shut-in/kill): no drilling data means no evidence to clear."""
        for a in self.alerts.values():
            if a.status != "cleared" and a.source in ("real-time", "fused"):
                a.updated_t += dt

    def clear_stale(self, t: float, keep: set[str]) -> None:
        for key, a in self.alerts.items():
            if a.status != "cleared" and a.source in ("real-time", "fused") and key not in keep and t - a.updated_t > CLEAR_AFTER_S:
                a.status = "cleared"
                a.history.append({"t": t, "md": a.md, "level": a.level, "event": "cleared"})
                self.closed[key] = t
                self.changed.add(key)
                self._log(a, "cleared", t, a.md, reason="no supporting signal for 15 min")

    def clear(self, key: str, t: float, reason: str) -> None:
        a = self.alerts.get(key)
        if a and a.status != "cleared":
            a.status = "cleared"
            a.history.append({"t": t, "md": a.md, "level": a.level, "event": f"cleared: {reason}"})
            self.closed[key] = t
            self.changed.add(key)
            self._log(a, "cleared", t, a.md, reason=reason)

    def ack(self, alert_id: str, actor: str = "RTOC", t: float | None = None, md: float | None = None) -> Alert | None:
        for a in self.alerts.values():
            if a.id == alert_id:
                a.status = "acknowledged"
                a.history.append({"t": t if t is not None else a.updated_t, "md": md if md is not None else a.md,
                                  "level": a.level, "event": f"acknowledged by {actor}"})
                self.changed.add(a.key)
                self._log(a, "acknowledged", t, md, actor=actor)
                return a
        return None

    def active_lookahead(self, hazard: str, md: float, margin: float = 60.0) -> Alert | None:
        for a in self.alerts.values():
            if a.source == "look-ahead" and a.hazard == hazard and a.status != "cleared" and a.zone and \
                    a.zone["md0"] - 200 <= md <= a.zone["md1"] + margin:
                return a
        return None

    def pop_changed(self, t: float | None = None) -> list[dict]:
        for k in self.changed:
            if k in self.alerts and t is not None:
                self.alerts[k].pushed_t = t
        out = [self.alerts[k].to_dict() for k in self.changed if k in self.alerts]
        self.changed.clear()
        return out

    def snapshot(self) -> list[dict]:
        return [a.to_dict() for a in sorted(self.alerts.values(), key=lambda a: -a.t)]
