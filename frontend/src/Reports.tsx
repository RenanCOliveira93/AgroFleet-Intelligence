import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { api, download } from "./api";
import { fmt, fmtPct, hoursHM } from "./lib";

const KEY: Record<string, string> = { machine: "machines", property: "property_id", field: "field_id", operator: "operator_id" };

export function ReportsTab({ ctx }: { ctx: Ctx }) {
  const [by, setBy] = useState("machine");
  const [start, setStart] = useState(""), [end, setEnd] = useState("");
  const [rep, setRep] = useState<any>(null);
  const [err, setErr] = useState("");
  const iso = (s: string) => (s ? new Date(s).toISOString() : "");
  useEffect(() => { setErr(""); api("/reports", { params: { group_by: by, origin: ctx.origin, start: iso(start), end: iso(end) } }).then(setRep).catch((e) => setErr(e.message)); }, [by, start, end, ctx.origin]);
  const label = (r: any) => (by === "machine" ? r.machines.join(", ") : r.sem_valor ? "(sem valor informado)" : r[KEY[by]]);
  return (
    <section className="card">
      <h2>Relatórios</h2>
      <div className="controls">
        <label>Agrupar por <select value={by} onChange={(e) => setBy(e.target.value)} data-testid="group-by">{[["machine", "máquina"], ["property", "propriedade"], ["field", "talhão"], ["operator", "operador"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label>De <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>Até <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <button onClick={() => download("/reports", { group_by: by, origin: ctx.origin, format: "csv", ...(start ? { start: iso(start) } : {}), ...(end ? { end: iso(end) } : {}) }, `relatorio_${by}.csv`)}>Baixar CSV</button>
      </div>
      {err && <p className="err">{err}</p>}
      {rep && (<table data-testid="report-table"><thead><tr><th>{by}</th><th>Origem</th><th>Em trabalho</th><th>Parada (ligada)</th><th>Distância</th><th>Volume</th><th>Área bruta</th><th>Área única</th><th>Sobreposição</th><th>ha/h (trabalho)</th></tr></thead>
        <tbody>{rep.rows.map((r: any, i: number) => (
          <tr key={i}><td>{label(r)}</td><td>{r.origins.join(", ")}</td><td>{hoursHM(r.time_h.working)}</td><td>{hoursHM(r.time_h.idle)}</td><td>{fmt(r.distance_km, 1, "km")}</td>
            <td>{fmt(r.volume_l, 0, "L")}</td><td>{fmt(r.coverage.gross_ha, 2, "ha")}</td><td>{fmt(r.coverage.unique_ha, 2, "ha")}</td><td>{fmtPct(r.coverage.overlap_pct_of_gross)}</td><td>{fmt(r.unique_ha_per_working_h, 1)}</td></tr>))}</tbody></table>)}
      <p className="muted">Em relatórios por talhão/propriedade/operador, a área única é a união entre as máquinas do grupo. Amostras sem a chave do grupo aparecem como "sem valor informado" (não são atribuídas a ninguém).</p>
    </section>
  );
}
