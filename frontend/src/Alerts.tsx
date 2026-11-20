import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { api, ApiError } from "./api";
import { OriginBadge } from "./Fleet";
import { fmt } from "./lib";

const LABEL: Record<string, string> = { flow_deviation: "Desvio de vazão", pressure_outlier: "Pressão atípica", pressure_out_of_band: "Pressão fora da faixa", speed_out_of_range: "Velocidade fora do esperado",
  unexpected_stop: "Parada em estado de trabalho", flow_while_stopped: "Vazão com máquina parada", fault_stop: "Falha/parada reportada", gnss_loss: "Perda de GNSS", gnss_degraded: "GNSS degradado",
  gnss_accuracy_low: "Precisão GNSS baixa", data_gap: "Lacuna de dados", sensor_missing: "Sensor sem dado", sensor_flatline: "Sensor travado", ml_multivariate_outlier: "Atípico multivariado (ML, experimental)",
  // Etapa 5: alertas operacionais (com estado; a reformulação visual é da Etapa 8)
  energy_level: "Nível de energia", autonomy_risk: "Risco de não conclusão", maintenance_due: "Manutenção preventiva", connection_lost: "Perda de conexão" };
const SEV: Record<string, string> = { critical: "Crítico", warning: "Aviso", info: "Informativo" };

export function AlertsTab({ ctx, params = {}, onClearParams }: { ctx: Ctx; params?: Record<string, string>; onClearParams?: () => void }) {
  const [rows, setRows] = useState<any[]>([]);
  const [ev, setEv] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [ml, setMl] = useState(false);
  const [type, setType] = useState("");
  const machineId = params.machine_id, operationId = params.operation_id;   // vindos de ctx.goto("alerts", …, params)
  const load = () => api("/alerts", { params: { origin: ctx.origin, type, sort: "priority", machine_id: machineId, operation_id: operationId } }).then(setRows);
  useEffect(() => { load(); }, [ctx.origin, type, machineId, operationId]);   // eslint-disable-line react-hooks/exhaustive-deps
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
        {(machineId || operationId) && <span className="chip" data-testid="alerts-filter">{machineId ? `Máquina ${machineId}` : `Operação ${operationId}`}{onClearParams && <button className="link" onClick={onClearParams}>limpar filtro</button>}</span>}
        <span className="muted">{msg}</span>
      </div>
      <div className="table-wrap"><table data-testid="alerts-table"><thead><tr><th>Início (UTC)</th><th>Situação</th><th>Máquina</th><th>Origem</th><th>Tipo</th><th>Duração</th><th>Evidência</th></tr></thead>
        <tbody>{rows.map((a) => (
          <tr key={a.id} className={a.severity}>
            <td>{a.start.replace("T", " ").slice(0, 19)}</td>
            <td><b className={a.severity === "critical" ? "err" : a.severity === "warning" ? "warn" : "muted"}>{SEV[a.severity] ?? a.severity}</b><br /><small className="muted">{a.status === "active" ? "ativo" : "resolvido"}</small></td>
            <td>{a.machine_id}</td><td><OriginBadge origin={a.origin} /></td>
            <td title={a.message}><b>{LABEL[a.type] ?? a.type}</b>{a.experimental && " ⚗"}<br /><small>{a.message}</small>{a.severity === "critical" && <><br /><small className="muted">{a.note}</small></>}</td>
            <td>{fmt(a.duration_s, 0, "s")}</td>
            <td><small>{Object.entries(a.evidence).slice(0, 4).map(([k, v]) => `${k}: ${typeof v === "number" ? fmt(v, 2) : JSON.stringify(v)}`).join(" · ")}</small></td>
          </tr>))}</tbody></table></div>
      {!rows.length && <p className="muted">Sem alertas para o filtro. Um analista pode recalcular.</p>}
      {ev && (
        <div className="card inner" data-testid="sim-eval">
          <h3>Avaliação contra a verdade do simulador <span className="badge origin-simulated">SIMULADO</span></h3>
          <p>Recall nos eventos injetados: <b>{fmt(ev.recall !== null ? ev.recall * 100 : null, 0, "%")}</b> ({ev.events.filter((e: any) => e.detected).length}/{ev.events.length}). Alertas sem evento correspondente: {ev.unmatched_alerts.length}.</p>
          <p className="muted">{ev.caveat}</p>
        </div>)}
    </section>
  );
}
