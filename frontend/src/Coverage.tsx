import { useEffect, useMemo, useState } from "react";
import type { Ctx } from "./App";
import { api } from "./api";
import { MapView } from "./MapView";
import { fmt, fmtPct } from "./lib";
import { OriginBadge } from "./Fleet";

const EXC: Record<string, string> = { no_position: "sem posição (ex.: perda de GNSS)", not_working: "fora do estado de aplicação", gap: "lacuna de dados", low_accuracy: "precisão pior que o limite",
  no_width: "largura de trabalho ausente", low_speed: "velocidade ~0", no_flow: "sem vazão" };

export function CoverageTab({ ctx }: { ctx: Ctx }) {
  const names = useMemo(() => [...new Set(ctx.machines.map((m) => m.machine_id))], [ctx.machines]);
  const [sel, setSel] = useState<string[]>([]);
  const [field, setField] = useState("");
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => { if (!sel.length && names.length) setSel([names[0]]); }, [names]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!sel.length) return;
    setErr(""); setRes(null);
    api("/coverage", { params: { machine_ids: sel.join(","), field_id: field, origin: ctx.origin } }).then(setRes).catch((e) => setErr(String(e.message)));
  }, [sel, field, ctx.origin]);
  const s = res?.summary;
  const fields = res?.field ? ({ type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: res.field }] } as GeoJSON.FeatureCollection) : null;
  return (
    <div className="grid2">
      <section className="card">
        <h2>Cobertura geométrica</h2>
        <fieldset><legend>Máquinas</legend>{names.map((n) => (<label key={n} className="chk"><input type="checkbox" checked={sel.includes(n)} onChange={(e) => setSel(e.target.checked ? [...sel, n] : sel.filter((x) => x !== n))} /> {n}</label>))}</fieldset>
        <label>Talhão <select value={field} onChange={(e) => setField(e.target.value)} data-testid="field-select"><option value="">(nenhum)</option>{ctx.fields.map((f) => <option key={f.id} value={f.id}>{f.id} — {f.origin}</option>)}</select></label>
        {err && <p className="err">{err}</p>}
        {s && (<>
          <p>{res.origins.map((o: string) => <OriginBadge key={o} origin={o} />)}</p>
          <table className="kv" data-testid="coverage-table"><tbody>
            <tr><th title="Σ comprimento × largura dos segmentos em trabalho">Área bruta</th><td>{fmt(s.gross_ha, 2, "ha")}</td></tr>
            <tr><th title="Área da união das faixas">Área única coberta</th><td data-testid="unique-ha">{fmt(s.unique_ha, 2, "ha")}</td></tr>
            <tr><th title="bruta − única">Excesso (área reaplicada)</th><td>{fmt(s.excess_ha, 2, "ha")} ({fmtPct(s.overlap_pct_of_gross)} da bruta)</td></tr>
            <tr><th title="Onde o produto caiu 2 ou mais vezes">Região coberta ≥ 2×</th><td>{fmt(s.overlap_region_ha, 2, "ha")}</td></tr>
            {s.field_ha !== undefined && (<>
              <tr><th>Talhão</th><td>{fmt(s.field_ha, 2, "ha")}</td></tr>
              <tr><th>Coberto dentro do talhão</th><td>{fmt(s.covered_in_field_ha, 2, "ha")} ({fmtPct(s.coverage_pct_of_field)})</td></tr>
              <tr><th>Não coberto no talhão</th><td>{fmt(s.uncovered_in_field_ha, 2, "ha")}</td></tr>
              <tr><th>Fora do talhão</th><td>{fmt(s.outside_field_ha, 2, "ha")}</td></tr></>)}
          </tbody></table>
          <h3>Segmentos descartados (e por quê)</h3>
          <ul>{Object.entries(s.excluded_segments as Record<string, number>).filter(([, v]) => v > 0).map(([k, v]) => <li key={k}><b>{v}</b> — {EXC[k] ?? k}</li>)}</ul>
          <p className="muted">Estimativa geométrica (largura efetiva × trajetória). Não há interpolação sobre perda de GNSS: área sob lacuna aparece como "não coberta", e isso é subestimativa, não verdade de campo.</p>
        </>)}
      </section>
      <section className="card"><MapView data={{ fields, union: res?.union, overlap: res?.overlap }} fitKey={res ? sel.join() + field : undefined} height={560} />
        <div className="legend"><span><i style={{ background: "#2e7d32", opacity: 0.5 }} />área única</span><span><i style={{ background: "#c62828" }} />coberto ≥ 2×</span><span><i style={{ background: "#fff", border: "1px dashed #455a64" }} />talhão</span></div></section>
    </div>
  );
}
