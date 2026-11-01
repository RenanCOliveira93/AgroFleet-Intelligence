import { useEffect, useMemo, useState } from "react";
import type { Ctx } from "./App";
import { api } from "./api";
import { Legend, MapView } from "./MapView";
import { fmt, msToKmh, ORIGIN_COLOR, ORIGIN_LABEL, STATE_COLOR, STATE_LABEL } from "./lib";

export function OriginBadge({ origin }: { origin: string }) {
  return <span className="badge" style={{ borderColor: ORIGIN_COLOR[origin], color: ORIGIN_COLOR[origin] }} data-testid={`origin-${origin}`}>{ORIGIN_LABEL[origin] ?? origin}</span>;
}

export function FleetTab({ ctx }: { ctx: Ctx }) {
  const [tracks, setTracks] = useState<GeoJSON.FeatureCollection | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const ids = ctx.machines.map((m) => `${m.machine_id}|${m.origin}`).join(",");

  useEffect(() => {
    let off = false;
    (async () => {
      const feats: GeoJSON.Feature[] = [];
      for (const m of ctx.machines.filter((x) => (sel ? x.machine_id === sel : true))) {
        const t = await api("/machines/" + m.machine_id + "/track", { params: { origin: m.origin } });
        feats.push(...t.features);
      }
      if (!off) setTracks({ type: "FeatureCollection", features: feats });
    })().catch(() => setTracks(null));
    return () => { off = true; };
  }, [ids, sel]);   // eslint-disable-line react-hooks/exhaustive-deps

  const fields = useMemo<GeoJSON.FeatureCollection>(() => ({ type: "FeatureCollection", features: ctx.fields.map((f) => ({ type: "Feature", properties: { id: f.id, origin: f.origin }, geometry: f.geojson })) }), [ctx.fields]);
  const markers = ctx.machines.filter((m) => m.lat !== null && m.lon !== null).map((m) => ({ id: m.machine_id, lon: m.lon!, lat: m.lat!, state: m.state, origin: m.origin }));
  const noGnss = ctx.machines.filter((m) => m.lat === null);

  return (
    <div className="grid2">
      <section className="card">
        <h2>Frota ({ctx.machines.length})</h2>
        {!ctx.machines.length && <p className="muted">Nenhuma máquina para o filtro atual.</p>}
        <div className="cards">
          {ctx.machines.map((m) => (
            <article key={m.machine_id + m.origin} className={`mcard ${sel === m.machine_id ? "sel" : ""}`} onClick={() => setSel(sel === m.machine_id ? null : m.machine_id)} data-testid={`machine-${m.machine_id}`}>
              <header><b>{m.machine_id}</b> <OriginBadge origin={m.origin} /></header>
              <div className="state" style={{ color: STATE_COLOR[m.state] }}>● {STATE_LABEL[m.state] ?? m.state}{m.live ? " · ao vivo" : " · histórico"}</div>
              <dl>
                <dt>Velocidade</dt><dd>{fmt(msToKmh(m.speed_ms), 1, "km/h")}</dd>
                <dt>Vazão</dt><dd>{fmt(m.flow_lpm, 1, "L/min")}</dd>
                <dt>Pressão</dt><dd>{fmt(m.pressure_bar, 2, "bar")}</dd>
                <dt>Tanque</dt><dd>{fmt(m.tank_level_l, 0, "L")}</dd>
                <dt>GNSS</dt><dd>{m.gnss_fix}{m.lat === null ? " (sem posição)" : ""}</dd>
                <dt>Alertas</dt><dd>{m.alerts}</dd>
              </dl>
              <small className="muted">último dado: {m.last_ts.replace("T", " ").slice(0, 19)} UTC</small>
            </article>))}
        </div>
        {noGnss.length > 0 && <p className="warn">Sem posição agora: {noGnss.map((m) => m.machine_id).join(", ")} (a posição não é estimada).</p>}
      </section>
      <section className="card">
        <h2>Mapa</h2>
        <MapView data={{ fields, tracks, markers }} fitKey={ctx.fields.length ? "f" + ctx.fields.length : undefined} height={520} />
        <Legend />
      </section>
    </div>
  );
}
