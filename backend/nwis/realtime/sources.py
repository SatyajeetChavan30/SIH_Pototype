"""Live stream sources: how rig data reaches NWIS next to eRTMAC.

    NWIS_STREAM=replay                       stored synthetic stream, one private replay per browser (default)
    NWIS_STREAM=wits0-listen:5501            NWIS listens; the rig / eRTMAC relay pushes WITS-0 over TCP
    NWIS_STREAM=wits0-connect:10.0.0.5:5501  NWIS connects to a WITS-0 TCP server (serial-over-IP box)
    NWIS_STREAM=witsml:https://host/store?well=W&wellbore=WB&log=L[&user=U&password=P]

Every source runs its I/O on a background thread and hands raw channel dicts to `poll()`. Reconnects use
exponential back-off because rig links (VSAT) drop; statistics feed the topbar health tag.
"""
from __future__ import annotations

import queue
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit, urlunsplit

from ..ingest.wits0 import Wits0Decoder, packet_time
from ..ingest.witsml_log import WitsmlLogPoller


class StreamSource:
    kind = "abstract"

    def __init__(self):
        self.q: queue.Queue = queue.Queue(maxsize=100_000)
        self.packets = 0
        self.errors = 0
        self.reconnects = 0
        self.last_packet_wall: float | None = None
        self.connected = False
        self.peer: str | None = None
        self.last_error: str | None = None
        self._stop = threading.Event()

    def start(self) -> "StreamSource":
        threading.Thread(target=self._run, daemon=True, name=f"nwis-{self.kind}").start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _put(self, row: dict) -> None:
        row.setdefault("t_epoch", packet_time(row) or time.time())
        self.packets += 1
        self.last_packet_wall = time.time()
        try:
            self.q.put_nowait(row)
        except queue.Full:
            self.errors += 1   # consumer stalled: drop rather than grow without bound

    def poll(self, limit: int = 2000) -> list[dict]:
        out = []
        while len(out) < limit:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                break
        return out

    def describe(self) -> str:
        return self.kind

    def stats(self) -> dict:
        age = None if self.last_packet_wall is None else round(time.time() - self.last_packet_wall, 1)
        return {"kind": self.kind, "describe": self.describe(), "connected": self.connected, "peer": self.peer,
                "packets": self.packets, "errors": self.errors, "reconnects": self.reconnects,
                "last_packet_age_s": age, "last_error": self.last_error}

    def _run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class Wits0TcpSource(StreamSource):
    kind = "wits0"

    def __init__(self, mode: str, host: str, port: int):
        super().__init__()
        self.mode, self.host, self.port = mode, host, port
        self.decoder = Wits0Decoder()
        self.bound_port: int | None = None
        self._ready = threading.Event()

    def describe(self) -> str:
        where = f":{self.bound_port or self.port}" if self.mode == "listen" else f"→ {self.host}:{self.port}"
        return f"WITS-0 {self.mode} {where}"

    def _pump(self, conn: socket.socket) -> None:
        conn.settimeout(1.0)
        self.decoder = Wits0Decoder(self.decoder.mapping)   # a new connection starts a clean frame
        while not self._stop.is_set():
            try:
                data = conn.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                self.last_error = str(e)
                return
            if not data:
                return
            for p in self.decoder.feed(data):
                self._put(p)
            self.errors = self.decoder.bad_lines

    def _run(self) -> None:
        backoff = 1.0
        if self.mode == "listen":
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            while True:   # the port may still be held for a moment by a listener that is shutting down
                try:
                    srv.bind((self.host, self.port))
                    break
                except OSError as e:
                    self.last_error = f"cannot listen on port {self.port}: {e}"
                    if self._stop.wait(2.0):
                        srv.close()
                        return
            self.last_error = None
            srv.listen(1)
            srv.settimeout(1.0)
            self.bound_port = srv.getsockname()[1]
            self._ready.set()
            while not self._stop.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                self.connected, self.peer = True, f"{addr[0]}:{addr[1]}"
                with conn:
                    self._pump(conn)
                self.connected = False
                self.reconnects += 1
            srv.close()
            return
        self._ready.set()
        while not self._stop.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=5.0) as conn:
                    self.connected, self.peer, backoff = True, f"{self.host}:{self.port}", 1.0
                    self._pump(conn)
            except OSError as e:
                self.last_error = str(e)
            self.connected = False
            self.reconnects += 1
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60.0)

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return self._ready.wait(timeout)


class WitsmlPollSource(StreamSource):
    kind = "witsml"

    def __init__(self, poller: WitsmlLogPoller, interval: float = 10.0):
        super().__init__()
        self.poller, self.interval = poller, interval

    def describe(self) -> str:
        u = urlsplit(self.poller.url)
        return f"WITSML 1.4.1 poll {u.hostname}"

    def _run(self) -> None:
        backoff = self.interval
        while not self._stop.is_set():
            try:
                rows = self.poller.poll()
                self.connected, self.peer, backoff = True, self.poller.url, self.interval
                for r in rows:
                    self._put(r)
            except Exception as e:  # noqa: BLE001 - keep polling through server/link errors
                self.connected = False
                self.last_error = str(e)[:200]
                self.reconnects += 1
                backoff = min(backoff * 2, 300.0)
            self._stop.wait(backoff)


def from_spec(spec: str | None) -> StreamSource | None:
    """Build a source from NWIS_STREAM; None means per-browser replay."""
    spec = (spec or "replay").strip()
    if spec in ("", "replay"):
        return None
    kind, _, rest = spec.partition(":")
    if kind == "wits0-listen":
        return Wits0TcpSource("listen", "0.0.0.0", int(rest or 5501))
    if kind == "wits0-connect":
        host, _, port = rest.rpartition(":")
        return Wits0TcpSource("connect", host, int(port))
    if kind == "witsml":
        u = urlsplit(rest)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        url = urlunsplit((u.scheme, u.netloc, u.path, "", ""))
        return WitsmlPollSource(WitsmlLogPoller(url, q["well"], q["wellbore"], q["log"], q.get("user"), q.get("password")),
                                float(q.get("interval", 10)))
    raise ValueError(f"unknown NWIS_STREAM '{spec}'")
