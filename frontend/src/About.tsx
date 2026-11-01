import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { api, ApiError } from "./api";

export function AboutTab({ ctx }: { ctx: Ctx }) {
  const [lim, setLim] = useState<any>(null);
  const [health, setHealth] = useState<any>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { api("/limits").then(setLim); api("/health").then(setHealth); }, []);
  const demo = async () => { setMsg("Carregando simulação…"); try { const r = await api("/demo/load", { method: "POST" }); await api("/alerts/recompute", { method: "POST" }); setMsg(`${r.samples} amostras SIMULADAS carregadas.`); ctx.refresh(); } catch (e) { setMsg(e instanceof ApiError ? String(e.detail) : "erro"); } };
  const wipe = async () => { if (!confirm("Apagar todos os dados SIMULADOS?")) return; const r = await api("/data", { method: "DELETE", params: { origin: "simulated" } }); setMsg(`apagados: ${r.telemetry} amostras`); ctx.refresh(); };
  return (
    <section className="card">
      <h2>Sobre e limites</h2>
      {lim && (<>
        <p><b>Escopo.</b> {lim.scope}</p><p><b>Alertas.</b> {lim.alerts}</p><p><b>Procedência.</b> {lim.data_origin}</p>
        <p><b>Cobertura.</b> {lim.coverage}</p><p><b>Pressão.</b> {lim.pressure}</p><p><b>ML.</b> {lim.ml}</p><p><b>ROI.</b> {lim.roi}</p>
        <h3>Fontes de dados</h3>
        <table data-testid="adapters"><thead><tr><th>Fonte</th><th>Status</th><th>Testada</th><th>Observação</th></tr></thead>
          <tbody>{Object.entries(lim.adapters as Record<string, any>).map(([k, v]) => <tr key={k}><td>{k}</td><td className={v.status === "implemented" ? "ok" : "warn"}>{v.status === "implemented" ? "implementada" : "NÃO implementada"}</td><td>{v.tested ? "sim" : "não"}</td><td>{v.note}</td></tr>)}</tbody></table>
        <h3>Não validado nesta entrega</h3><ul>{lim.not_validated.map((x: string) => <li key={x}>{x}</li>)}</ul></>)}
      {health && <p className="muted">Banco: {health.db}{health.postgis ? " + PostGIS" : ""} · MQTT: {health.mqtt.enabled ? (health.mqtt.connected ? `conectado (${health.mqtt.received ?? 0} recebidas, ${health.mqtt.rejected ?? 0} rejeitadas)` : "desconectado") : "desativado"} · esquema {health.schema_version}</p>}
      {ctx.role === "admin" && (<div className="controls"><button onClick={demo} data-testid="load-demo">Carregar demonstração SIMULADA</button><button className="danger" onClick={wipe}>Apagar dados simulados</button><span>{msg}</span></div>)}
    </section>
  );
}
