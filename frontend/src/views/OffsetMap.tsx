import { useEffect, useMemo, useState } from "react";
import { Circle, CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMapEvents } from "react-leaflet";
import { api, qs } from "../api";
import { useApp } from "../context";
import { HAZARD_COLOR, HAZARD_SHORT, fmt } from "../theme";
import type { WellEvent, WellSummary } from "../types";

function ClickCatcher({ onClick }: { onClick: (lat: number, lon: number) => void }) {
  useMapEvents({ click: (e) => onClick(e.latlng.lat, e.latlng.lng) });
  return null;
}

export default function OffsetMap() {
  const { meta, go, fmName, openCitation, params } = useApp();
  const [wells, setWells] = useState<WellSummary[]>([]);
  const [radius, setRadius] = useState(Number(params.radius ?? (meta.ontology.region?.default_radius_km ?? 8)));
  const [center, setCenter] = useState<{ lat: number; lon: number; label: string } | null>(null);
  const [hz, setHz] = useState<string[]>(meta.ribbon_hazards.concat(["FISH"] as any));
  const [selId, setSelId] = useState<string | null>(params.well ?? null);
  const [detail, setDetail] = useState<any>(null);
  const [tilesOk, setTilesOk] = useState(true);

  useEffect(() => { api<WellSummary[]>("/api/wells").then(setWells); }, []);
  const active = wells.find((w) => w.is_active);
  useEffect(() => { if (active && !center) setCenter({ lat: active.lat, lon: active.lon, label: active.name }); }, [active]);
  useEffect(() => { if (selId) api(`/api/wells/${selId}`).then(setDetail); else setDetail(null); }, [selId]);

  const dist = (w: WellSummary) => {
    if (!center) return 0;
    const R = 6371, toR = Math.PI / 180;
    const dLat = (w.lat - center.lat) * toR, dLon = (w.lon - center.lon) * toR;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(center.lat * toR) * Math.cos(w.lat * toR) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  };
  const rows = useMemo(() => wells.filter((w) => !w.is_active).map((w) => ({ ...w, d: dist(w) }))
    .filter((w) => w.d <= radius).sort((a, b) => a.d - b.d), [wells, center, radius]);
  const counts = (w: WellSummary) => Object.entries(w.hazard_counts).filter(([k]) => hz.includes(k));
  const dominant = (w: WellSummary) => counts(w).sort((a, b) => b[1] - a[1])[0]?.[0];
  const totals = useMemo(() => {
    const t: Record<string, number> = {};
    rows.forEach((w) => counts(w).forEach(([k, v]) => { t[k] = (t[k] ?? 0) + v; }));
    return t;
  }, [rows, hz]);

  return <div className="col">
    <div>
      <h2 className="view">Offset Map</h2>
      <p className="lede">Nearby wells within a user-defined radius of the active well, or of any point you click (a planned location). Colour = dominant recorded hazard; size = number of events; lines = well trajectories.</p>
    </div>
    <div className="row wrap card" style={{ gap: 14 }}>
      <label className="row" style={{ gap: 8 }}>Radius <input type="range" min={1} max={40} step={0.5} value={radius} onChange={(e) => setRadius(Number(e.target.value))} />
        <b className="num" style={{ minWidth: 48 }}>{radius} km</b></label>
      <span className="small muted">Centre: <b className="ink2">{center?.label}</b></span>
      {active && center?.label !== active.name && <button className="btn sm" onClick={() => setCenter({ lat: active.lat, lon: active.lon, label: active.name })}>Back to active well</button>}
      <div className="row wrap" style={{ gap: 6 }}>
        {[...meta.ribbon_hazards, "FISH"].map((h) => <span key={h} className={`chip click ${hz.includes(h) ? "" : "off"}`}
          onClick={() => setHz(hz.includes(h) ? hz.filter((x) => x !== h) : [...hz, h])}>
          <span className="swatch" style={{ background: HAZARD_COLOR[h] }} />{HAZARD_SHORT[h]} <span className="muted num">{totals[h] ?? 0}</span></span>)}
      </div>
    </div>
    <div style={{ display: "grid", gridTemplateColumns: "minmax(420px, 1.6fr) minmax(320px, 1fr)", gap: 12 }}>
      <div className={`card ${tilesOk ? "" : "gridbg"}`} style={{ padding: 0, height: 620, position: "relative" }}>
        {active && <MapContainer center={[active.lat, active.lon]} zoom={11} style={{ height: "100%", width: "100%" }} preferCanvas>
          {/* keyless tiles by default; the server can point this at an on-prem tile server (NWIS_TILE_URL) */}
          {tilesOk && <TileLayer url={meta.map_tiles?.url ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png"}
            attribution={meta.map_tiles?.attribution ?? "&copy; OpenStreetMap contributors"} className="basemap"
            eventHandlers={{ tileerror: () => setTilesOk(false) }} />}
          <ClickCatcher onClick={(lat, lon) => { setCenter({ lat, lon, label: `Planned @ ${lat.toFixed(4)}, ${lon.toFixed(4)}` }); setSelId(null); }} />
          {center && <Circle center={[center.lat, center.lon]} radius={radius * 1000} pathOptions={{ color: "#3987e5", weight: 1.5, fillOpacity: 0.05, dashArray: "6 6" }} />}
          {meta.structures.map((s) => <CircleMarker key={s.id} center={[s.lat, s.lon]} radius={0} pathOptions={{ opacity: 0 }}>
            <Tooltip permanent direction="center" className="struct-label">{s.name}</Tooltip></CircleMarker>)}
          {wells.filter((w) => !w.is_active && dist(w) <= radius && w.trajectory).map((w) =>
            <Polyline key={`t${w.id}`} positions={w.trajectory as any} pathOptions={{ color: "#6b6b6b", weight: 1.5, opacity: 0.8 }} />)}
          {wells.filter((w) => !w.is_active).map((w) => {
            const inR = dist(w) <= radius;
            const dom = dominant(w);
            const n = counts(w).reduce((s, [, v]) => s + v, 0);
            return <CircleMarker key={w.id} center={[w.lat, w.lon]} radius={5 + Math.min(n, 6) * 1.6}
              pathOptions={{ color: w.id === selId ? "#000000" : "#ffffff", weight: w.id === selId ? 3 : 2, fillColor: dom ? HAZARD_COLOR[dom] : "#9e9e9e", fillOpacity: inR ? 0.95 : 0.25, opacity: inR ? 1 : 0.4 }}
              eventHandlers={{ click: (e) => { (e as any).originalEvent?.stopPropagation?.(); setSelId(w.id); } }}>
              <Tooltip>{w.id} · {w.spud_year} · {n} events{dom ? ` · mostly ${HAZARD_SHORT[dom]}` : ""}</Tooltip>
            </CircleMarker>;
          })}
          {active && <>
            {active.trajectory && <Polyline positions={active.trajectory as any} pathOptions={{ color: "#000000", weight: 3, dashArray: "4 4" }} />}
            <CircleMarker center={[active.lat, active.lon]} radius={9} pathOptions={{ color: "#000000", weight: 3, fillColor: "#d03b3b", fillOpacity: 1 }}>
              <Tooltip permanent direction="right">{active.name}</Tooltip></CircleMarker>
          </>}
        </MapContainer>}
        {!tilesOk && <div className="small muted" style={{ position: "absolute", bottom: 8, left: 10, zIndex: 500 }}>Offline mode: basemap unavailable, showing wells on grid.</div>}
      </div>
      <div className="col">
        {center && active && center.label !== active.name && <div className="card">
          <h3>Planned location</h3>
          <div className="small muted">{center.lat.toFixed(5)} N, {center.lon.toFixed(5)} E · {rows.length} offsets within {radius} km</div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn primary sm" onClick={() => go("planning", { lat: String(center.lat), lon: String(center.lon), radius: String(radius) })}>Assess drilling risk here →</button>
            <a className="btn sm" href={`/api/brief?${qs({ lat: center.lat, lon: center.lon, radius_km: radius })}`} target="_blank" rel="noreferrer">Hazard brief</a>
          </div>
        </div>}
        {detail ? <div className="card col" style={{ gap: 8 }}>
          <h3>{detail.id} <span className="sub">{detail.status} · spud {detail.spud_year} · {detail.traj_type} · TD {fmt.m(detail.td_md)}</span>
            <button className="btn sm" style={{ marginLeft: "auto" }} onClick={() => setSelId(null)}>✕</button></h3>
          <div className="small muted">Mud: {detail.mud_system} · target {fmName(detail.target)} · NPT {detail.npt_hours} h</div>
          <div className="scroll" style={{ maxHeight: 360 }}>
            <table className="t"><thead><tr><th>Event</th><th className="num">MD</th><th>Source</th></tr></thead>
              <tbody>{detail.events.map((e: WellEvent) => <tr key={e.id}>
                <td><span className="swatch" style={{ background: HAZARD_COLOR[e.hazard] }} /> {e.summary}</td>
                <td className="num">{fmt.n0(e.md)}</td>
                <td>{e.citations[0] && <span className="cite small" onClick={() => openCitation(e.citations[0])}>{e.citations[0].kind ?? "doc"} p.{e.citations[0].page_no}</span>}</td>
              </tr>)}</tbody></table>
            {detail.events.length === 0 && <div className="empty">No complications recorded.</div>}
          </div>
          <div className="row wrap">
            <button className="btn sm" onClick={() => go("correlation", { well: detail.id })}>Correlate</button>
            <button className="btn sm" onClick={() => go("knowledge", { q: detail.id })}>Search its reports</button>
          </div>
        </div> : <div className="card">
          <h3>Offsets within {radius} km <span className="sub">{rows.length} wells</span></h3>
          <div className="scroll" style={{ maxHeight: 520 }}>
            <table className="t"><thead><tr><th>Well</th><th className="num">km</th><th className="num">Spud</th><th>Recorded hazards</th></tr></thead>
              <tbody>{rows.map((w) => <tr key={w.id} className="click" onClick={() => setSelId(w.id)}>
                <td>{w.id}{w.structure_id === active?.structure_id && <span className="muted small"> · same structure</span>}</td>
                <td className="num">{w.d.toFixed(1)}</td><td className="num">{w.spud_year}</td>
                <td>{counts(w).map(([k, v]) => <span key={k} className="chip" style={{ marginRight: 4, padding: "0 6px" }}>
                  <span className="swatch" style={{ background: HAZARD_COLOR[k] }} />{HAZARD_SHORT[k]} {v}</span>)}</td>
              </tr>)}</tbody></table>
          </div>
        </div>}
      </div>
    </div>
  </div>;
}
