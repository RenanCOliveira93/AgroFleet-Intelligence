import { useEffect, useMemo, useRef, useState } from "react";
import type { Ctx } from "./App";
import { api } from "./api";
import { MapView, Legend, type Marker } from "./MapView";
import { positionAt, trailUntil, STATE_LABEL, type Series } from "./lib";

export function PlaybackTab({ ctx }: { ctx: Ctx }) {
  const [data, setData] = useState<{ t_start: number; t_end: number; machines: Record<string, Series> } | null>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(30);
  const [err, setErr] = useState("");
  const raf = useRef<number>();

  useEffect(() => {
    setData(null);
    api("/playback", { params: { step_s: 5, origin: ctx.origin } }).then((d) => { setData(d); if (d.t_start !== null) setT(d.t_start); }).catch((e) => setErr(String(e.message)));
  }, [ctx.origin]);

  useEffect(() => {
    if (!playing || !data) return;
    let last = performance.now();
    const loop = (now: number) => {
      const dt = (now - last) / 1000; last = now;
      setT((x) => { const n = x + dt * speed; if (n >= data.t_end) { setPlaying(false); return data.t_end; } return n; });
      raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);
    return () => { if (raf.current) cancelAnimationFrame(raf.current); };
  }, [playing, speed, data]);

  const frame = useMemo(() => {
    if (!data) return { markers: [] as Marker[], tracks: null as GeoJSON.FeatureCollection | null, nogps: [] as string[] };
    const markers: Marker[] = [], feats: GeoJSON.Feature[] = [], nogps: string[] = [];
    for (const [id, s] of Object.entries(data.machines)) {
      const p = positionAt(s, t);
      const idx = s.t.findIndex((x) => x > t);
      const i = Math.max((idx < 0 ? s.t.length : idx) - 1, 0);
      if (p) markers.push({ id, lon: p.lon, lat: p.lat, state: p.state, origin: p.origin });
      else if (t >= s.t[0] && t <= s.t[s.t.length - 1]) nogps.push(`${id} (${STATE_LABEL[s.state[i]] ?? s.state[i]})`);
      for (const line of trailUntil(s, t)) feats.push({ type: "Feature", properties: { state: "unknown" }, geometry: { type: "LineString", coordinates: line } });
    }
    return { markers, tracks: { type: "FeatureCollection", features: feats } as GeoJSON.FeatureCollection, nogps };
  }, [data, t]);
  const fields = useMemo<GeoJSON.FeatureCollection>(() => ({ type: "FeatureCollection", features: ctx.fields.map((f) => ({ type: "Feature", properties: {}, geometry: f.geojson })) }), [ctx.fields]);

  if (err) return <p className="err">{err}</p>;
  if (!data) return <p className="muted">Carregando…</p>;
  if (data.t_start === null) return <p className="muted">Sem telemetria para reproduzir.</p>;
  const clock = new Date(t * 1000).toISOString().replace("T", " ").slice(0, 19);
  return (
    <section className="card">
      <h2>Reprodução temporal</h2>
      <div className="controls">
        <button onClick={() => setPlaying((p) => !p)} data-testid="play">{playing ? "Pausar" : "Reproduzir"}</button>
        <input type="range" min={data.t_start} max={data.t_end} step={1} value={t} onChange={(e) => { setPlaying(false); setT(Number(e.target.value)); }} aria-label="instante" data-testid="slider" style={{ flex: 1 }} />
        <label>Velocidade <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[5, 30, 120, 600].map((v) => <option key={v} value={v}>{v}×</option>)}</select></label>
        <code data-testid="clock">{clock} UTC</code>
      </div>
      <MapView data={{ fields, tracks: frame.tracks, markers: frame.markers }} fitKey={ctx.fields.length ? "p" : undefined} height={520} />
      <Legend />
      {frame.nogps.length > 0 && <p className="warn" data-testid="nogps">Sem GNSS neste instante: {frame.nogps.join(", ")}. A posição não é extrapolada.</p>}
      <p className="muted">Posições entre amostras (5 s) são interpoladas linearmente só quando as duas são válidas; nunca sobre lacunas. Origem de cada marcador no contorno.</p>
    </section>
  );
}
