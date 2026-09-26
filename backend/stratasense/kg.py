"""Drilling knowledge graph: Well -> Event -> Hazard/Formation -> Cause -> Mitigation (with outcomes) -> Lesson."""
from __future__ import annotations

import networkx as nx

from .domain.ontology import FORMATION_BY_CODE, HAZARD_BY_CODE, MITIGATION_BY_CODE, formation_name
from .kb import KnowledgeBase


def build_graph(kb: KnowledgeBase) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for e in kb.events:
        ev = f"event:{e['id']}"
        g.add_node(ev, type="event", label=f"{e['hazard']} @ {e['md'] or 0:,.0f} m", ref=e["id"], hazard=e["hazard"])
        g.add_node(f"well:{e['well_id']}", type="well", label=e["well_id"], ref=e["well_id"])
        g.add_edge(f"well:{e['well_id']}", ev, rel="experienced")
        g.add_node(f"hazard:{e['hazard']}", type="hazard", label=HAZARD_BY_CODE[e["hazard"]].label, ref=e["hazard"])
        g.add_edge(ev, f"hazard:{e['hazard']}", rel="is_a")
        if e["formation"]:
            g.add_node(f"formation:{e['formation']}", type="formation",
                       label=formation_name(e["formation"]), ref=e["formation"])
            g.add_edge(ev, f"formation:{e['formation']}", rel="in")
        if e["cause"]:
            g.add_node(f"cause:{e['cause']}", type="cause", label=e["cause"], ref=e["cause"])
            g.add_edge(ev, f"cause:{e['cause']}", rel="caused_by")
        for a in e["actions"]:
            lab = MITIGATION_BY_CODE[a["code"]].label if a["code"] in MITIGATION_BY_CODE else a["code"]
            g.add_node(f"mitigation:{a['code']}", type="mitigation", label=lab, ref=a["code"])
            g.add_edge(ev, f"mitigation:{a['code']}", rel="mitigated_by", success=bool(a["success"]))
    for l in kb.lessons:
        if not l["well_id"]:
            continue
        g.add_node(f"lesson:{l['id']}", type="lesson", label=l["text"][:80], ref=l["id"])
        g.add_edge(f"well:{l['well_id']}", f"lesson:{l['id']}", rel="taught")
        if l["formation"]:
            g.add_edge(f"lesson:{l['id']}", f"formation:{l['formation']}", rel="about")
    return g


def summary_subgraph(kb: KnowledgeBase, formation: str | None = None, hazard: str | None = None,
                     wells: set[str] | None = None, max_wells: int = 14) -> dict:
    """Aggregated, readable graph: Formation -> Hazard -> Cause -> Mitigation (success ratio) + Wells."""
    evs = [e for e in kb.events if (formation is None or e["formation"] == formation)
           and (hazard is None or e["hazard"] == hazard) and (wells is None or e["well_id"] in wells)]
    nodes: dict[str, dict] = {}
    links: dict[tuple, dict] = {}

    def node(i, t, label, **kw):
        n = nodes.setdefault(i, {"id": i, "type": t, "label": label, "count": 0, **kw})
        n["count"] += 1

    def link(a, b, rel, success=None):
        l = links.setdefault((a, b, rel), {"source": a, "target": b, "rel": rel, "count": 0, "success": 0})
        l["count"] += 1
        if success:
            l["success"] += 1

    well_counts: dict[str, int] = {}
    for e in evs:
        well_counts[e["well_id"]] = well_counts.get(e["well_id"], 0) + 1
    top_wells = set(sorted(well_counts, key=lambda w: -well_counts[w])[:max_wells])
    for e in evs:
        fm = f"formation:{e['formation']}" if e["formation"] else None
        hz = f"hazard:{e['hazard']}"
        node(hz, "hazard", HAZARD_BY_CODE[e["hazard"]].label, color=HAZARD_BY_CODE[e["hazard"]].color)
        if fm:
            node(fm, "formation", formation_name(e["formation"]), color=getattr(FORMATION_BY_CODE.get(e["formation"]), "color", "#888888"))
            link(fm, hz, "hosts")
        if e["cause"]:
            c = f"cause:{e['cause']}"
            node(c, "cause", e["cause"])
            link(hz, c, "caused_by")
        else:
            c = hz
        for a in e["actions"]:
            m = f"mitigation:{a['code']}"
            node(m, "mitigation", MITIGATION_BY_CODE[a["code"]].label if a["code"] in MITIGATION_BY_CODE else a["code"])
            link(c, m, "treated_with", a["success"])
        if e["well_id"] in top_wells:
            w = f"well:{e['well_id']}"
            node(w, "well", e["well_id"])
            link(w, fm or hz, "encountered")
    return {"nodes": list(nodes.values()), "links": list(links.values()), "n_events": len(evs)}
