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

from typing import Iterable, Iterator

DEFAULT_MAP = {
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


def to_packet(sample: dict, mapping: dict[str, str] | None = None) -> str:
    """Encode a sample as a WITS-0 packet (used by the simulator's eRTMAC relay mode)."""
    inv = {v: k for k, v in (mapping or DEFAULT_MAP).items()}
    lines = ["&&"]
    for k, v in sample.items():
        if k in inv and v is not None:
            lines.append(f"{inv[k]}{float(v):.3f}")
    lines.append("!!")
    return "\r\n".join(lines) + "\r\n"
