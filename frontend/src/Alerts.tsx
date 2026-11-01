import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { api, ApiError } from "./api";
import { OriginBadge } from "./Fleet";
import { fmt } from "./lib";

const LABEL: Record<string, string> = { flow_deviation: "Desvio de vazão", pressure_outlier: "Pressão atípica", pressure_out_of_band: "Pressão fora da faixa", speed_out_of_range: "Velocidade fora do esperado",
  unexpected_stop: "Parada em estado de trabalho", flow_while_stopped: "Vazão com máquina parada", fault_stop: "Falha/parada reportada", gnss_loss: "Perda de GNSS", gnss_degraded: "GNSS degradado",
  gnss_accuracy_low: "Precisão GNSS baixa", data_gap: "Lacuna de dados", sensor_missing: "Sensor sem dado", sensor_flatline: "Sensor travado", ml_multivariate_outlier: "Atípico multivariado (ML, experimental)" };

export function AlertsTab({ ctx }: { ctx: Ctx }) {
  const [rows, setRows] = useState<any[]>([]);
  const [ev, setEv] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [ml, setMl] = useState(false);
  const [type, setType] = useState("");
  const load = () => api("/alerts", { params: { origin: ctx.origin, type } }).then(setRows);
  useEffect(() => { load(); }, [ctx.origin, type]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api("/demo/evaluation").then(setEv).catch(() => setEv(null)); }, [rows.length]);
  const recompute = async () => {
    setMsg("Recalculando…");
    try { const r = await api("/alerts/recompute", { method: "POST", params: { origin: ctx.origin, with_ml: ml } }); setMsg(`${r.alerts} alertas. Regras puladas: ${r.skipped_rules.length}`); load(); ctx.refresh(); }
    catch (e) { setMsg(e instanceof ApiError ? String(e.detail) : "erro"); }
  };
  const types = [...new Set(rows.map((r) => r.type))];
  return (
    <section className="card">
      <h2>Alertas <small className="muted">(informativos — não geram nenhum comando)</small></h2>
      <div className="controls">
        <label>Tipo <select value={type} onChange={(e) => setType(e.target.value)}><option value="">todos</option>{types.map((t) => <option key={t} value={t}>{LABEL[t] ?? t}</option>)}</select></label>
        {(ctx.role === "analyst" || ctx.role === "admin") && (<>
          <label className="chk"><input type="checkbox" checked={ml} onChange={(e) => setMl(e.target.checked)} /> incluir ML (IsolationForest, experimental)</label>
          <button onClick={recompute} data-testid="recompute">Recalcular alertas</button></>)}
        <span className="muted">{msg}</span>
      </div>
      <table data-testid="alerts-table"><thead><tr><th>Início (UTC)</th><th>Máquina</th><th>Origem</th><th>Tipo</th><th>Duração</th><th>Evidência</th></tr></thead>
        <tbody>{rows.map((a) => (
          <tr key={a.id} className={a.severity}>
            <td>{a.start.replace("T", " ").slice(0, 19)}</td><td>{a.machine_id}</td><td><OriginBadge origin={a.origin} /></td>
            <td title={a.message}><b>{LABEL[a.type] ?? a.type}</b>{a.experimental && " ⚗"}<br /><small>{a.message}</small></td>
            <td>{fmt(a.duration_s, 0, "s")}</td>
            <td><small>{Object.entries(a.evidence).slice(0, 4).map(([k, v]) => `${k}: ${typeof v === "number" ? fmt(v, 2) : JSON.stringify(v)}`).join(" · ")}</small></td>
          </tr>))}</tbody></table>
      {!rows.length && <p className="muted">Sem alertas para o filtro. Um analista pode recalcular.</p>}
      {ev && (
        <div className="card inner" data-testid="sim-eval">
          <h3>Avaliação contra a verdade do simulador <span className="badge" style={{ borderColor: "#b26a00", color: "#b26a00" }}>SIMULADO</span></h3>
          <p>Recall nos eventos injetados: <b>{fmt(ev.recall !== null ? ev.recall * 100 : null, 0, "%")}</b> ({ev.events.filter((e: any) => e.detected).length}/{ev.events.length}). Alertas sem evento correspondente: {ev.unmatched_alerts.length}.</p>
          <p className="muted">{ev.caveat}</p>
        </div>)}
    </section>
  );
}
