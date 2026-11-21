import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { api, ApiError } from "./api";
import { OriginBadge } from "./Fleet";
import { fmt } from "./lib";
import { fmtDateTime } from "./shell";
import { Plus } from "lucide-react";
import { MachineFormDialog } from "./MachineForm";

const STATUS_CLASS: Record<string, string> = { active: "st-active", planned: "st-planned", completed: "st-done", cancelled: "st-cancelled" };
const ROLE_NOTE: Record<string, string> = {
  viewer: "Seu papel (visualizador) permite só leitura.",
  analyst: "Máquinas são cadastradas pelo administrador. Operações: pela API (PUT /operations/{id}, papel analista).",
  admin: "Máquinas: cadastre e edite abaixo. Propriedades, talhões e operações: pela API (veja o Guia do usuário).",
};

export function StatusPill({ status, label }: { status: string; label?: string }) {
  return <span className={`pill ${STATUS_CLASS[status] ?? ""}`}>{label ?? status}</span>;
}

/** Planejamento › Operações: cadastro de operações, máquinas e talhões da propriedade escolhida (somente leitura). */
export function PlanningTab({ ctx }: { ctx: Ctx }) {
  const [ops, setOps] = useState<any[] | null>(null);
  const [machines, setMachines] = useState<any[] | null>(null);
  const [err, setErr] = useState("");
  const [form, setForm] = useState<{ machine?: any } | null>(null);
  const [reload, setReload] = useState(0);
  const isAdmin = ctx.role === "admin";

  useEffect(() => {
    let off = false;
    setErr("");
    Promise.all([api("/operations", { params: { origin: ctx.origin } }), api("/registry/machines", { params: { origin: ctx.origin } })])
      .then(([o, m]) => { if (!off) { setOps(o); setMachines(m); } })
      .catch((e) => { if (!off) { setErr(e instanceof ApiError ? String(e.detail) : "falha ao carregar o cadastro"); setOps([]); setMachines([]); } });
    return () => { off = true; };
  }, [ctx.origin, ctx.machines.length, reload]);

  const pid = ctx.propertyId;
  const inProp = (x: { property_id?: string | null }) => !pid || x.property_id === pid;
  const opsV = (ops ?? []).filter(inProp);
  const machV = (machines ?? []).filter((m) => inProp(m) || !m.property_id);   // sem propriedade: aparece em qualquer filtro
  const fieldsV = ctx.fields.filter((f) => (ctx.origin === "all" || f.origin === ctx.origin) && inProp(f));
  const mName = (id: string) => (machines ?? []).find((m) => m.machine_id === id)?.name;
  const fName = (id: string) => ctx.fields.find((f) => f.id === id)?.name;
  const propName = (id: string | null | undefined) => (id ? ctx.properties.find((p) => p.property_id === id)?.name ?? id : "—");
  const tz = ctx.timezone;

  return (
    <div className="stack" data-testid="planning">
      <section className="card">
        <div className="card-head">
          <h2>Operações cadastradas</h2>
          <span className="muted small">{ROLE_NOTE[ctx.role] ?? ROLE_NOTE.viewer} Horários em {tz}.</span>
        </div>
        {err && <p className="err" role="alert">{err}</p>}
        <div className="kpis">
          <div><small>Operações</small><b>{ops ? opsV.length : "—"}</b></div>
          <div><small>Em andamento</small><b>{ops ? opsV.filter((o) => o.status === "active").length : "—"}</b></div>
          <div><small>Máquinas cadastradas</small><b>{machines ? machV.length : "—"}</b></div>
          <div><small>Talhões</small><b>{fieldsV.length}</b></div>
        </div>
        {ops === null ? <p className="muted">Carregando…</p> : !opsV.length ? <p className="muted">Nenhuma operação cadastrada para a propriedade e a origem escolhidas.</p> : (
          <div className="table-wrap">
            <table data-testid="operations-table">
              <thead><tr><th>Operação</th><th>Tipo</th><th>Máquina</th><th>Talhão</th><th>Início</th><th>Fim</th><th>Área planejada</th><th>Largura do implemento</th><th>Situação</th><th>Origem</th><th><span className="sr-only">Ações</span></th></tr></thead>
              <tbody>{opsV.map((o) => (
                <tr key={o.operation_id}>
                  <td><b>{o.name}</b><br /><small className="muted">{o.operation_id}{!pid && o.property_id ? ` · ${propName(o.property_id)}` : ""}</small></td>
                  <td>{o.operation_label ?? o.operation_type}</td>
                  <td>{mName(o.machine_id) ?? o.machine_id}<br /><small className="muted">{o.machine_id}</small></td>
                  <td>{fName(o.field_id) ?? o.field_id}</td>
                  <td className="num">{fmtDateTime(o.start, tz)}</td>
                  <td className="num">{o.end ? fmtDateTime(o.end, tz) : <span className="muted" title="sem fim previsto no cadastro">—</span>}</td>
                  <td className="num">{o.planned_area_ha != null ? fmt(o.planned_area_ha, 1, "ha") : <span className="muted" title="não informada no cadastro">—</span>}</td>
                  <td className="num">{fmt(o.implement_width_m, 1, "m")}</td>
                  <td><StatusPill status={o.status} label={o.status_label} /></td>
                  <td><OriginBadge origin={o.origin} /></td>
                  <td>{o.status === "active" && <button className="ghost sm" onClick={() => ctx.goto("operation", undefined, { operation_id: o.operation_id })}>Ver no painel</button>}</td>
                </tr>))}</tbody>
            </table>
          </div>)}
      </section>

      <div className="grid2 even">
        <section className="card">
          <div className="card-head">
            <h2>Máquinas cadastradas</h2>
            {isAdmin && <button className="sm" onClick={() => setForm({})} data-testid="add-machine"><Plus size={14} aria-hidden="true" /> Cadastrar máquina</button>}
          </div>
          {machines === null ? <p className="muted">Carregando…</p> : !machV.length ? <p className="muted">Nenhuma máquina cadastrada para a propriedade e a origem escolhidas.{isAdmin ? " Use “Cadastrar máquina” para incluir a primeira." : ""}</p> : (
            <div className="table-wrap">
              <table data-testid="registry-machines-table">
                <thead><tr><th>Máquina</th><th>Tipo</th><th>Energia</th><th>Capacidade</th><th>Aviso / crítico</th><th>Manutenção</th><th>Origem</th>{isAdmin && <th><span className="sr-only">Ações</span></th>}</tr></thead>
                <tbody>{machV.map((m) => (
                  <tr key={m.machine_id}>
                    <td><b>{m.name ?? m.machine_id}</b><br /><small className="muted">{m.machine_id}{m.autonomous ? " · autônoma" : ""}{!m.property_id ? " · sem propriedade" : ""}</small></td>
                    <td>{m.kind_label ?? m.kind}</td>
                    <td>{m.energy_label ?? m.energy_source}</td>
                    <td className="num">{m.fuel_tank_capacity_l != null ? fmt(m.fuel_tank_capacity_l, 0, "L") : m.battery_capacity_kwh != null ? fmt(m.battery_capacity_kwh, 0, "kWh") : <span className="muted" title="não informada no cadastro">—</span>}</td>
                    <td className="num">{fmt(m.energy_warning_pct, 0, "%")} / {fmt(m.energy_critical_pct, 0, "%")}</td>
                    <td className="num">{m.maintenance_interval_h != null ? `a cada ${fmt(m.maintenance_interval_h, 0, "h")}` : <span className="muted" title="intervalo não informado">—</span>}</td>
                    <td><OriginBadge origin={m.origin} /></td>
                    {isAdmin && <td><button className="ghost sm" onClick={() => setForm({ machine: m })} aria-label={`Editar ${m.name ?? m.machine_id}`}>Editar</button></td>}
                  </tr>))}</tbody>
              </table>
            </div>)}
        </section>
        <section className="card">
          <h2>Talhões</h2>
          {!fieldsV.length ? <p className="muted">Nenhum talhão para a propriedade e a origem escolhidas.</p> : (
            <div className="table-wrap">
              <table data-testid="fields-table">
                <thead><tr><th>Talhão</th><th>Propriedade</th><th>Geometria</th><th>Origem</th></tr></thead>
                <tbody>{fieldsV.map((f) => (
                  <tr key={f.id}>
                    <td><b>{f.name ?? f.id}</b>{f.name && f.name !== f.id && <><br /><small className="muted">{f.id}</small></>}</td>
                    <td>{propName(f.property_id)}</td>
                    <td>{f.geojson ? "polígono" : <span className="muted">sem geometria</span>}</td>
                    <td><OriginBadge origin={f.origin} /></td>
                  </tr>))}</tbody>
              </table>
            </div>)}
          <p className="muted small">— = não informado no cadastro. Nada é estimado nesta tela.</p>
        </section>
      </div>
      {form && <MachineFormDialog machine={form.machine} properties={ctx.properties} existingIds={(machines ?? []).map((m) => m.machine_id)}
        defaultProperty={ctx.propertyId} onClose={() => setForm(null)} onSaved={() => { setReload((n) => n + 1); ctx.refresh(); }} />}
    </div>
  );
}
