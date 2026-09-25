"""Rig simulator: re-transmits the stored active-well stream as real WITS-0 frames over TCP.

It sends only what a rig WITS box sends (Record-01 channels plus gamma ray and ECD on the configured
extension items, and WITS date/time). Hidden truth - formation index, rig state, d-exponent - is never
sent, so NWIS has to infer state, compute the d-exponent and pick tops from gamma ray exactly as it would
on a real feed.
"""
from __future__ import annotations

import datetime as dt
import socket
import time

import numpy as np

from .. import config
from ..ingest.wits0 import active_map, to_packet

SENT = ("md", "rop", "wob", "rpm", "torque", "spp", "flow_in", "flow_out", "pit", "hookload", "gas", "mw", "gr", "ecd")


def frames(start_index: int = 0, limit: int | None = None, t0: dt.datetime | None = None):
    """Yield (stream seconds, WITS-0 packet text) for the stored stream."""
    z = np.load(config.LOGS_DIR / "active_stream.npz")
    t = z["t"]
    mapping = active_map()
    t0 = t0 or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    end = len(t) if limit is None else min(len(t), start_index + limit)
    for i in range(start_index, end):
        stamp = t0 + dt.timedelta(seconds=float(t[i] - t[start_index]))
        sample = {k: float(z[k][i]) for k in SENT}
        sample["hole_depth"] = sample["md"]
        sample["date"] = int(stamp.strftime("%y%m%d"))
        sample["time"] = int(stamp.strftime("%H%M%S"))
        yield float(t[i]), to_packet(sample, mapping)


def start_index_for_md(md: float) -> int:
    z = np.load(config.LOGS_DIR / "active_stream.npz")
    return int(np.searchsorted(z["md"], md))


def _send_all(conn: socket.socket, start: int, limit: int | None, speed: float, log=print) -> int:
    n, prev = 0, None
    for ts, pkt in frames(start, limit):
        if prev is not None and speed > 0:
            time.sleep(max(ts - prev, 0.0) / speed)
        prev = ts
        conn.sendall(pkt.encode("ascii"))
        n += 1
        if n % 500 == 0:
            log(f"  sent {n} frames")
    return n


def run(connect: str | None = None, listen: int | None = None, speed: float = 60.0, start: int = 0,
        limit: int | None = None, log=print) -> int:
    """Push frames to NWIS's listener (connect='host:port') or wait for NWIS to connect (listen=port)."""
    if connect:
        host, _, port = connect.rpartition(":")
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=10) as c:
            log(f"connected to {connect}; streaming at {speed:g}x")
            return _send_all(c, start, limit, speed, log)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", int(listen or 5501)))
    srv.listen(1)
    log(f"rig WITS-0 box listening on :{listen}; waiting for NWIS to connect")
    conn, addr = srv.accept()
    with conn:
        log(f"NWIS connected from {addr[0]}; streaming at {speed:g}x")
        n = _send_all(conn, start, limit, speed, log)
    srv.close()
    return n
