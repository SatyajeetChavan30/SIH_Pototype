"""WITS Level-0 (ASCII) record parser - the lowest-common-denominator rig data feed.

A WITS-0 packet looks like:
    &&
    0108 2105.30
    0113 21.4
    ...
    !!
Each data line is a 4-digit item id (2-digit record + 2-digit item) followed by the
value. The mapping below covers WITS Record 01 (general time-based drilling data);
it is configurable because rigs sometimes remap channels.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Iterable, Iterator

DEFAULT_MAP = {
    "0105": "date",          # YYMMDD
    "0106": "time",          # HHMMSS
    "0108": "hole_depth",
    "0110": "md",            # bit depth (measured)
    "0111": "tvd",           # bit depth (vertical)
    "0113": "rop",
    "0114": "hookload",
    "0116": "wob",
    "0118": "torque",
    "0120": "rpm",
    "0121": "spp",
    "0126": "pit",
    "0128": "flow_out",      # mud flow out %
    "0130": "flow_in",
    "0132": "mw",            # mud density in
    "0140": "gas",
}
# Channels StrataSense uses that Record 01 does not carry. These item ids are StrataSense defaults, not a WITS standard
# assignment: confirm the real codes with OIL's eRTMAC / mud-logging vendor and override with STRATASENSE_WITS_MAP.
EXTENSION_MAP = {
    "0824": "gr",            # MWD gamma ray (Record 08 area)
    "0142": "ecd",           # equivalent circulating density, if the rig computes it
}


def active_map() -> dict[str, str]:
    """DEFAULT_MAP + EXTENSION_MAP, overridden by a JSON file named in STRATASENSE_WITS_MAP ({"item": "channel"})."""
    m = {**DEFAULT_MAP, **EXTENSION_MAP}
    path = os.environ.get("STRATASENSE_WITS_MAP")
    if path:
        with open(path, encoding="utf-8") as f:
            m.update({str(k): str(v) for k, v in json.load(f).items()})
    return m


def parse_packets(lines: Iterable[str], mapping: dict[str, str] | None = None) -> Iterator[dict]:
    """Yield one dict per &&...!! packet. Malformed lines are skipped (rig links are noisy)."""
    mapping = mapping or DEFAULT_MAP
    cur: dict | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line == "&&":
            cur = {}
            continue
        if line == "!!":
            if cur:
                yield cur
            cur = None
            continue
        if cur is None or len(line) < 5 or not line[:4].isdigit():
            continue
        item, val = line[:4], line[4:].strip()
        key = mapping.get(item)
        if key is None:
            continue
        try:
            cur[key] = float(val)
        except ValueError:
            continue


class Wits0Decoder:
    """Incremental decoder for a byte stream (TCP or serial-over-IP): bytes in, complete packets out.

    Packets split across reads are reassembled; bytes that are not valid ASCII, stray lines and
    half-received packets after a reconnect are dropped without raising.
    """

    def __init__(self, mapping: dict[str, str] | None = None):
        self.mapping = mapping or active_map()
        self._buf = ""
        self._cur: dict | None = None
        self.packets = 0
        self.bad_lines = 0

    def feed(self, data: bytes) -> list[dict]:
        self._buf += data.decode("ascii", errors="replace")
        *lines, self._buf = self._buf.replace("\r", "\n").split("\n")
        out = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line == "&&":
                self._cur = {}
            elif line == "!!":
                if self._cur:
                    out.append(self._cur)
                    self.packets += 1
                self._cur = None
            elif self._cur is not None and len(line) >= 5 and line[:4].isdigit():
                key = self.mapping.get(line[:4])
                if key is None:
                    continue
                try:
                    self._cur[key] = float(line[4:].strip())
                except ValueError:
                    self.bad_lines += 1
            else:
                self.bad_lines += 1
        return out


def packet_time(p: dict) -> float | None:
    """Epoch seconds from WITS 0105 (YYMMDD) + 0106 (HHMMSS), if both are present."""
    if "date" not in p or "time" not in p:
        return None
    try:
        d, t = f"{int(p['date']):06d}", f"{int(p['time']):06d}"
        return dt.datetime.strptime(d + t, "%y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


def to_packet(sample: dict, mapping: dict[str, str] | None = None) -> str:
    """Encode a sample as a WITS-0 packet (used by the rig simulator)."""
    inv = {v: k for k, v in (mapping or DEFAULT_MAP).items()}
    lines = ["&&"]
    for k, v in sample.items():
        if k in inv and v is not None:
            lines.append(f"{inv[k]}{int(v):06d}" if k in ("date", "time") else f"{inv[k]}{float(v):.3f}")
    lines.append("!!")
    return "\r\n".join(lines) + "\r\n"
