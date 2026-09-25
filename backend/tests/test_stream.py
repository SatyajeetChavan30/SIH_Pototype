"""Live rig-feed tests: WITS-0 byte-stream decoding, WITSML log parsing, and an end-to-end run in which the
rig simulator pushes real WITS-0 frames over TCP into NWIS and the S1 loss is detected on the live path."""
import threading
import time

import pytest

from nwis import config
from nwis.ingest.wits0 import Wits0Decoder, packet_time, to_packet
from nwis.ingest.witsml_log import parse_log


def test_wits0_decoder_reassembles_split_and_noisy_packets():
    mapping = {"0110": "md", "0113": "rop", "0105": "date", "0106": "time"}
    pkt = to_packet({"md": 2105.3, "rop": 21.4, "date": 260925, "time": 101530}, mapping)
    stream = b"garbage\x00\xff\r\n" + pkt.encode() + b"&&\r\n0110 notanumber\r\n0113 5\r\n!!\r\n" + pkt.encode()
    d = Wits0Decoder(mapping)
    out = []
    for i in range(0, len(stream), 7):          # deliver in awkward 7-byte chunks, like a slow serial link
        out += d.feed(stream[i:i + 7])
    assert len(out) == 3
    assert out[0]["md"] == pytest.approx(2105.3) and out[0]["rop"] == pytest.approx(21.4)
    assert out[1] == {"rop": 5.0} and d.bad_lines >= 2
    assert packet_time(out[0]) is not None


def test_witsml_log_rows_are_mapped_and_converted():
    xml = """<logs xmlns="http://www.witsml.org/schemas/1series" version="1.4.1.1"><log uidWell="W" uidWellbore="WB" uid="L">
      <logCurveInfo><mnemonic>TIME</mnemonic><unit>s</unit></logCurveInfo>
      <logCurveInfo><mnemonic>DBTM</mnemonic><unit>ft</unit></logCurveInfo>
      <logCurveInfo><mnemonic>SPPA</mnemonic><unit>psi</unit></logCurveInfo>
      <logData><mnemonicList>TIME,DBTM,SPPA,XYZ</mnemonicList>
        <data>2026-09-25T10:00:00Z,10000,2450,1</data><data>2026-09-25T10:00:10Z,10001,,1</data></logData></log></logs>"""
    rows = parse_log(xml)
    assert len(rows) == 2
    assert rows[0]["md"] == pytest.approx(3048.0) and rows[0]["spp"] == 2450
    assert "spp" not in rows[1] and rows[1]["t_epoch"] - rows[0]["t_epoch"] == 10


@pytest.mark.skipif(not config.DB_PATH.exists(), reason="demo knowledge base not built")
def test_live_wits0_feed_detects_s1_losses():
    from nwis.kb import KnowledgeBase
    from nwis.realtime import simulator
    from nwis.realtime.engine import LiveSession
    from nwis.realtime.sources import Wits0TcpSource

    kb = KnowledgeBase()
    src = Wits0TcpSource("listen", "127.0.0.1", 0).start()
    assert src.wait_ready()
    s1 = next(e for e in kb.db.kv_get("active_episodes") if e["id"] == "S1")
    start = simulator.start_index_for_md(s1.get("onset_md", s1["md"]) - 380)
    th = threading.Thread(target=simulator.run, kwargs={"connect": f"127.0.0.1:{src.bound_port}", "speed": 0,
                                                        "start": start, "limit": 1500, "log": lambda *_: None})
    th.start()
    session = LiveSession(kb, None, None, source=src)
    alerts = {}
    deadline = time.time() + 60
    while time.time() < deadline:
        rows = src.poll()
        if rows:
            session.ingest(rows)
            r = session.step(session.n - session.i)
            alerts.update({a["key"]: a for a in r["alerts"]})
        elif not th.is_alive() and session.i >= session.n:
            break
        else:
            time.sleep(0.05)
    th.join(5)
    src.stop()
    st = session.status()
    assert st["mode"] == "live" and st["stream"]["packets"] >= 1400
    assert {"state", "dxc", "tvd"} <= set(st["stream"]["derived"])      # inferred, never sent by the rig
    hit = [a for a in alerts.values() if a["hazard"] == "LOSS" and a["source"] in ("real-time", "fused")
           and s1.get("onset_md", s1["md"]) - 120 <= a["md"] <= s1["md"] + 60]
    assert hit, "S1 losses were not detected from the live WITS-0 feed"

@pytest.mark.skipif(not config.DB_PATH.exists(), reason="demo knowledge base not built")
def test_live_hub_broadcasts_and_flags_stream_gap():
    import asyncio
    from nwis.kb import KnowledgeBase
    from nwis.realtime import simulator
    from nwis.realtime.hub import LiveHub
    from nwis.realtime.sources import Wits0TcpSource

    async def scenario():
        src = Wits0TcpSource("listen", "127.0.0.1", 0)
        logged = []
        hub = LiveHub(KnowledgeBase(), None, src, logged.extend, gap_s=1.0)
        assert src.wait_ready()
        q1, q2 = hub.subscribe(), hub.subscribe()
        await asyncio.to_thread(simulator.run, f"127.0.0.1:{src.bound_port}", None, 0, 0, 200, lambda *_: None)
        for _ in range(20):
            await hub.step_once()
            if hub.session.n >= 200 and hub.session.i >= hub.session.n:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.3)                  # feed goes quiet for longer than gap_s
        await hub.step_once()
        src.stop()
        msgs1 = [q1.get_nowait() for _ in range(q1.qsize())]
        msgs2 = [q2.get_nowait() for _ in range(q2.qsize())]
        return hub, msgs1, msgs2

    hub, m1, m2 = asyncio.run(scenario())
    assert hub.session.n == 200 and hub.session.i == 200
    assert len(m1) == len(m2) > 0 and sum(len(m["samples"]) for m in m1) == 200     # every console gets every sample
    assert hub.in_gap and any(e["type"] == "stream_gap" for m in m1 for e in m["events"])
