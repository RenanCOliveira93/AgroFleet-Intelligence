import { useState } from "react";
import type { Ctx } from "./App";
import { api, ApiError } from "./api";
import { fmt } from "./lib";

export function UploadTab({ ctx }: { ctx: Ctx }) {
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("csv");
  const [origin, setOrigin] = useState("unknown");
  const [machine, setMachine] = useState("");
  const [tz, setTz] = useState("");
  const [units, setUnits] = useState("");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState("");
  const can = ctx.role === "analyst" || ctx.role === "admin";
  const send = async (commit: boolean) => {
    if (!file) return;
    setErr(""); setRes(null);
    const f = new FormData();
    f.append("file", file); f.append("kind", kind); f.append("origin", origin); f.append("commit", String(commit));
    if (machine) f.append("machine_id", machine);
    if (tz) f.append("assume_tz", tz);
    if (units) f.append("units", units);
    try { const r = await api("/uploads", { method: "POST", form: f }); setRes(r); if (commit) ctx.refresh(); }
    catch (e) { setErr(e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : (e.detail as any)?.msg ?? JSON.stringify(e.detail)) : "erro de rede"); if (e instanceof ApiError && (e.detail as any)?.quality) setRes({ quality: (e.detail as any).quality }); }
  };
  if (!can) return <p className="warn">Upload requer o papel analista ou admin.</p>;
  const q = res?.quality;
  return (
    <section className="card">
      <h2>Upload com validação de qualidade</h2>
      <div className="form">
        <label>Arquivo <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} data-testid="file" /></label>
        <label>Formato <select value={kind} onChange={(e) => setKind(e.target.value)} data-testid="kind"><option value="csv">CSV</option><option value="json">JSON / NDJSON</option><option value="geojson">GeoJSON (trilha com tempo)</option><option value="gpx">GPX</option><option value="isoxml">ISOXML/TASKDATA (não implementado)</option></select></label>
        <label>Origem dos dados <select value={origin} onChange={(e) => setOrigin(e.target.value)} data-testid="origin"><option value="unknown">não declarada</option><option value="real">real</option><option value="simulated">simulada</option></select></label>
        <label>machine_id (GeoJSON/GPX) <input value={machine} onChange={(e) => setMachine(e.target.value)} /></label>
        <label>Fuso se o timestamp não tiver <input value={tz} placeholder="ex.: America/Sao_Paulo" onChange={(e) => setTz(e.target.value)} /></label>
        <label>Unidades (JSON) <input value={units} placeholder='ex.: {"speed":"km/h","pressure":"kPa"}' onChange={(e) => setUnits(e.target.value)} /></label>
      </div>
      <div className="controls"><button onClick={() => send(false)} disabled={!file} data-testid="validate">Validar (não grava)</button><button onClick={() => send(true)} disabled={!file} data-testid="commit">Validar e gravar</button></div>
      {err && <p className="err" role="alert">{err}</p>}
      {q && (
        <div data-testid="quality">
          <h3>Resultado: <span className={`verdict v-${q.verdict}`}>{q.verdict === "ok" ? "sem ressalvas" : q.verdict === "warnings" ? "com avisos" : "bloqueante"}</span></h3>
          <p>{q.rows_ok} linhas válidas de {q.rows_input} ({q.rows_rejected} rejeitadas){res.stored !== null && res.stored !== undefined ? ` · gravadas: ${res.stored} (duplicadas ignoradas: ${res.duplicates_ignored})` : ""}</p>
          <ul>{q.flags.map((f: any, i: number) => <li key={i} className={`f-${f.level}`}>[{f.level}] {f.msg}</li>)}</ul>
          {q.mapping?.columns_mapped && <details><summary>Mapeamento de colunas e unidades usado</summary><pre>{JSON.stringify({ colunas: q.mapping.columns_mapped, ignoradas: q.mapping.columns_ignored, unidades: q.mapping.units }, null, 1)}</pre></details>}
          {q.rejected_sample?.length > 0 && <details><summary>Amostra de linhas rejeitadas</summary><pre>{JSON.stringify(q.rejected_sample.slice(0, 8), null, 1)}</pre></details>}
          {q.null_pct && <details><summary>% de valores ausentes por campo</summary><table className="kv"><tbody>{Object.entries(q.null_pct as Record<string, number>).map(([k, v]) => <tr key={k}><th>{k}</th><td>{fmt(v, 1, "%")}</td></tr>)}</tbody></table></details>}
        </div>)}
    </section>
  );
}
