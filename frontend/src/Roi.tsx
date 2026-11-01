import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { api, ApiError } from "./api";
import { fmt, fmtMoney } from "./lib";

const GROUPS: [string, [string, string, string][]][] = [
  ["Frota autônoma", [["n_machines", "Nº de máquinas", ""], ["capex_per_machine", "Investimento por máquina", "$"], ["useful_life_years", "Vida útil", "anos"], ["residual_pct", "Valor residual", "%"],
    ["interest_rate_pct", "Custo de capital", "% a.a."], ["maintenance_pct_of_capex_per_year", "Manutenção", "% do capex/ano"], ["energy_cost_per_h", "Energia/combustível", "$/h"], ["supervision_cost_per_h", "Supervisão remota", "$/h"]]],
  ["Operação", [["area_ha_per_year", "Área por ano", "ha"], ["applications_per_year", "Aplicações por ano", ""], ["product_cost_per_ha", "Custo do produto", "$/ha"],
    ["auto_productivity_ha_per_h", "Produtividade autônoma", "ha/h"], ["auto_overlap_pct", "Sobreposição autônoma", "%"]]],
  ["Referência (convencional)", [["baseline_hourly_cost", "Custo horário (máquina+operador)", "$/h"], ["baseline_productivity_ha_per_h", "Produtividade", "ha/h"], ["baseline_overlap_pct", "Sobreposição", "%"], ["discount_rate_pct", "Taxa de desconto", "% a.a."]]],
];
const NAMES: Record<string, string> = Object.fromEntries(GROUPS.flatMap(([, f]) => f.map(([k, l]) => [k, l])));

function Tornado({ rows, base }: { rows: any[]; base: number }) {
  const top = rows.slice(0, 8);
  const span = Math.max(...top.flatMap((r) => [Math.abs((r.low.net_benefit_per_year ?? base) - base), Math.abs((r.high.net_benefit_per_year ?? base) - base)]), 1);
  const W = 560, mid = 330, scale = 200 / span;
  return (
    <svg viewBox={`0 0 ${W} ${top.length * 26 + 28}`} role="img" aria-label="Gráfico de tornado" className="tornado" data-testid="tornado">
      <line x1={mid} x2={mid} y1={4} y2={top.length * 26 + 8} stroke="currentColor" opacity={0.5} />
      {top.map((r, i) => {
        const lo = (r.low.net_benefit_per_year ?? base) - base, hi = (r.high.net_benefit_per_year ?? base) - base;
        const y = 8 + i * 26;
        return (<g key={r.input}>
          <text x={4} y={y + 14} fontSize={11} fill="currentColor">{NAMES[r.input] ?? r.input}</text>
          <rect x={mid + Math.min(0, lo * scale)} y={y} width={Math.abs(lo * scale)} height={9} fill="#1565c0"><title>{`−20%: ${fmtMoney(r.low.net_benefit_per_year)}`}</title></rect>
          <rect x={mid + Math.min(0, hi * scale)} y={y + 10} width={Math.abs(hi * scale)} height={9} fill="#ef6c00"><title>{`+20%: ${fmtMoney(r.high.net_benefit_per_year)}`}</title></rect>
        </g>);
      })}
      <text x={mid + 4} y={top.length * 26 + 22} fontSize={10} fill="currentColor">benefício base: {fmtMoney(base)}/ano · azul = −20% · laranja = +20%</text>
    </svg>
  );
}

export function RoiTab({ ctx }: { ctx: Ctx }) {
  const [inp, setInp] = useState<Record<string, any> | null>(null);
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState("");
  const loadExample = async () => { const r = await api("/roi/example"); setInp(r.inputs); setOut(null); };
  useEffect(() => { loadExample(); }, []);
  const set = (k: string, v: string) => setInp((p) => ({ ...p!, [k]: v === "" ? 0 : Number(v), provenance: { ...(p!.provenance ?? {}), [k]: { source: "user", note: "informado nesta sessão" } }, is_example: false }));
  const run = async (fromTel = false) => {
    setErr("");
    try {
      if (fromTel) { const r = await api("/roi/from-telemetry", { method: "POST", body: inp, params: { origin: ctx.origin } }); setOut(r); setInp({ ...inp!, ...r.result.assumptions, provenance: r.result.provenance, is_example: false }); }
      else setOut(await api("/roi", { method: "POST", body: { inputs: inp, sensitivity_pct: 20, grid: { x: "area_ha_per_year", y: "product_cost_per_ha", xs: [0.5, 0.75, 1, 1.5, 2].map((f) => (inp!.area_ha_per_year as number) * f), ys: [0.7, 0.85, 1, 1.15, 1.3].map((f) => (inp!.product_cost_per_ha as number) * f) } } }));
    } catch (e) { setErr(e instanceof ApiError ? JSON.stringify(e.detail) : "erro"); }
  };
  if (!inp) return <p className="muted">Carregando…</p>;
  const r = out?.result, cur = inp.currency as string;
  return (
    <section className="card">
      <h2>ROI com premissas explícitas</h2>
      {inp.is_example && <p className="warn" data-testid="roi-example-warning">CENÁRIO ILUSTRATIVO — valores fictícios para demonstrar o cálculo; não são preços nem benchmarks de mercado. Substitua todos pelos da sua operação.</p>}
      {GROUPS.map(([g, fs]) => (<fieldset key={g}><legend>{g}</legend><div className="form">{fs.map(([k, l, u]) => (
        <label key={k}>{l} <small className="muted">{u.replace("$", cur)}</small>
          <input type="number" value={inp[k] ?? 0} step="any" onChange={(e) => set(k, e.target.value)} data-testid={`in-${k}`} />
          <small className="prov">{inp.provenance?.[k] ? `${inp.provenance[k].source}${inp.provenance[k].origin ? " · " + inp.provenance[k].origin.join("/") : ""}` : "—"}</small></label>))}</div></fieldset>))}
      <div className="controls"><button onClick={() => run(false)} data-testid="roi-run">Calcular</button><button onClick={() => run(true)}>Preencher produtividade e sobreposição com a telemetria</button><button className="link" onClick={loadExample}>Recarregar exemplo</button></div>
      {err && <p className="err">{err}</p>}
      {r && (<div data-testid="roi-result">
        <div className="kpis">
          <div><small>Custo/ha autônoma</small><b>{fmtMoney(r.autonomous.total_cost_per_ha, cur)}</b></div>
          <div><small>Custo/ha convencional</small><b>{fmtMoney(r.baseline.total_cost_per_ha, cur)}</b></div>
          <div><small>Benefício líquido/ano</small><b className={r.net_benefit_per_year < 0 ? "neg" : ""}>{fmtMoney(r.net_benefit_per_year, cur)}</b></div>
          <div><small>Payback</small><b>{r.payback_years === null ? "sem retorno (benefício ≤ 0)" : `${fmt(r.payback_years, 1)} anos`}</b></div>
          <div><small>VPL na vida útil</small><b>{fmtMoney(r.npv_over_useful_life, cur)}</b></div>
        </div>
        {out.sensitivity && <><h3>Sensibilidade (±20%, uma premissa por vez)</h3><Tornado rows={out.sensitivity.tornado} base={out.sensitivity.base_net_benefit_per_year} /></>}
        {out.break_even && <><h3>Ponto de equilíbrio (benefício = 0)</h3><ul>{Object.entries(out.break_even as Record<string, number | null>).map(([k, v]) => <li key={k}>{NAMES[k] ?? k}: {v === null ? "não cruza zero na faixa avaliada" : fmt(v, 2)} (atual: {fmt(inp[k], 2)})</li>)}</ul></>}
        {out.grid && <><h3>Grade: área por ano × custo do produto → benefício líquido/ano</h3>
          <table className="grid" data-testid="roi-grid"><thead><tr><th></th>{out.grid.xs.map((x: number) => <th key={x}>{fmt(x, 0)} ha</th>)}</tr></thead>
            <tbody>{out.grid.ys.map((y: number, i: number) => <tr key={y}><th>{fmt(y, 0)} {cur}/ha</th>{out.grid.net_benefit_per_year[i].map((v: number | null, j: number) => <td key={j} className={v !== null && v < 0 ? "neg" : "pos"}>{fmtMoney(v, cur)}</td>)}</tr>)}</tbody></table></>}
        <h3>Limites</h3><ul>{r.caveats.map((c: string) => <li key={c}>{c}</li>)}</ul>
      </div>)}
    </section>
  );
}
