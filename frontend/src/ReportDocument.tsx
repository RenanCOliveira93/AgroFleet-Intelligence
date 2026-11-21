import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Mail, Printer, Share2 } from "lucide-react";
import type { Ctx } from "./App";
import { api, ApiError } from "./api";
import { fmt, fmtPct, hoursHM } from "./lib";
import { fmtDateTime } from "./shell";
import { Modal } from "./Modal";
import { ALERT_LABEL } from "./Alerts";
import { mailtoUrl, periodLabel, periodRange, Preset, PRESETS, reportText, SEVERITY_LABEL, validEmails } from "./reportDoc";

/** Corpo do relatório: o mesmo componente na prévia e na versão impressa. */
export function ReportBody({ doc, print }: { doc: any; print?: boolean }) {
  const tz = doc.timezone, t = doc.totals;
  const sim = doc.data_origins?.includes("simulated");
  return (
    <article className="rdoc" data-testid={print ? undefined : "report-doc"}>
      <header className="rdoc-head">
        <div>
          <div className="rdoc-brand">AgroFleet <span>Intelligence</span></div>
          <h1>{doc.title}</h1>
          <p><b>{doc.property?.name ?? "Todas as propriedades"}</b> · {periodLabel(doc)} <span className="muted">({tz})</span></p>
        </div>
        <div className="rdoc-meta">
          <div>Gerado em {fmtDateTime(doc.generated_at, tz)}</div>
          {doc.generated_by && <div>por {doc.generated_by}</div>}
        </div>
      </header>
      {sim && <p className="rdoc-sim">DADOS SIMULADOS: este relatório não representa máquinas reais.</p>}
      {doc.origin_notes?.filter((n: string) => !sim || !n.includes("SIMULADOS")).map((n: string) => <p key={n} className="muted small">{n}</p>)}

      <h2>Resumo do período</h2>
      {!t ? <p className="muted">Sem telemetria no período e filtro escolhidos.</p> : (
        <div className="rdoc-kpis">
          <div><small>Área trabalhada (única)</small><b>{fmt(t.coverage?.unique_ha, 2, "ha")}</b></div>
          <div><small>Área bruta · sobreposição</small><b>{fmt(t.coverage?.gross_ha, 2, "ha")}</b><span>{fmtPct(t.coverage?.overlap_pct_of_gross)}</span></div>
          <div><small>Tempo em trabalho</small><b>{hoursHM(t.time_h?.working)}</b></div>
          <div><small>Parada (ligada)</small><b>{hoursHM(t.time_h?.idle)}</b><span>{fmtPct(t.idle_pct_of_engaged, 0)} do tempo ligado</span></div>
          <div><small>Distância</small><b>{fmt(t.distance_km, 1, "km")}</b></div>
          <div><small>Volume aplicado</small><b>{fmt(t.volume_l, 0, "L")}</b></div>
          <div><small>Ritmo (por hora em trabalho)</small><b>{fmt(t.unique_ha_per_working_h, 1, "ha/h")}</b></div>
          <div><small>Máquinas com dados</small><b>{t.machines?.length ?? 0}</b></div>
        </div>)}

      <h2>Operações ({doc.operations?.length ?? 0})</h2>
      {!doc.operations?.length ? <p className="muted">Nenhuma operação no período.</p> : (
        <div className="table-wrap"><table className="rdoc-table">
          <thead><tr><th>Operação</th><th>Máquina</th><th>Talhão</th><th>Situação</th><th>Progresso</th><th>Trabalhado / planejado</th><th>Em trabalho</th><th>Ritmo</th><th>Consumo médio</th><th>Previsão de término</th></tr></thead>
          <tbody>{doc.operations.map((o: any) => {
            const m = o.mission, c = o.consumption;
            return (<tr key={o.operation_id}>
              <td className="rdoc-op"><b>{o.name}</b><br /><small className="muted">início {fmtDateTime(o.start, tz)}</small></td>
              <td>{o.machine_name ?? o.machine_id}</td>
              <td>{o.field_name ?? o.field_id}</td>
              <td>{o.status_label}</td>
              <td className="num">{m ? fmtPct(m.progress_pct, 0) : "—"}</td>
              <td className="num">{m ? `${fmt(m.worked_ha, 1)} / ${fmt(m.planned_ha, 1, "ha")}` : <span className="muted">{o.unavailable ?? "—"}</span>}</td>
              <td className="num">{m ? hoursHM(m.working_h) : "—"}</td>
              <td className="num">{m ? fmt(m.rate_ha_h_working, 1, "ha/h") : "—"}</td>
              <td className="num">{c?.avg != null ? fmt(c.avg, 1, c.unit ?? "") : <span className="muted" title={c?.reason ?? ""}>—</span>}</td>
              <td className="num">{o.status === "active" && m?.eta ? fmtDateTime(m.eta, tz) : "—"}</td>
            </tr>);
          })}</tbody>
        </table></div>)}
      {doc.operations_truncated > 0 && <p className="muted small">E mais {doc.operations_truncated} operações não listadas (reduza o período).</p>}

      <h2>Máquinas</h2>
      {!doc.machines?.length ? <p className="muted">Nenhuma máquina com telemetria no período.</p> : (
        <div className="table-wrap"><table className="rdoc-table">
          <thead><tr><th>Máquina</th><th>Tipo</th><th>Em trabalho</th><th>Parada (ligada)</th><th>Distância</th><th>Volume</th><th>Área única</th><th>Sobreposição</th><th>Ritmo</th></tr></thead>
          <tbody>{doc.machines.map((r: any) => (
            <tr key={r.machine_id}>
              <td><b>{r.name ?? r.machine_id}</b><br /><small className="muted">{r.machine_id}</small></td>
              <td>{r.kind_label ?? "—"}</td>
              <td className="num">{hoursHM(r.time_h?.working)}</td>
              <td className="num">{hoursHM(r.time_h?.idle)}</td>
              <td className="num">{fmt(r.distance_km, 1, "km")}</td>
              <td className="num">{fmt(r.volume_l, 0, "L")}</td>
              <td className="num">{fmt(r.coverage?.unique_ha, 2, "ha")}</td>
              <td className="num">{fmtPct(r.coverage?.overlap_pct_of_gross)}</td>
              <td className="num">{fmt(r.unique_ha_per_working_h, 1, "ha/h")}</td>
            </tr>))}</tbody>
        </table></div>)}

      <h2>Alertas ({doc.alerts?.total ?? 0})</h2>
      <p>Críticos: <b>{doc.alerts?.counts?.critical ?? 0}</b> · Avisos: <b>{doc.alerts?.counts?.warning ?? 0}</b> · Informativos: <b>{doc.alerts?.counts?.info ?? 0}</b> · Ativos agora: <b>{doc.alerts?.active ?? 0}</b></p>
      {doc.alerts?.items?.length > 0 && (
        <div className="table-wrap"><table className="rdoc-table">
          <thead><tr><th>Severidade</th><th>Alerta</th><th>Máquina</th><th>Início</th><th>Situação</th></tr></thead>
          <tbody>{doc.alerts.items.map((a: any, i: number) => (
            <tr key={i}><td><span className={`sev sev-${a.severity}`}>{SEVERITY_LABEL[a.severity] ?? a.severity}</span></td><td>{a.label ?? ALERT_LABEL[a.type] ?? a.type}</td>
              <td>{a.machine_id}</td><td className="num">{fmtDateTime(a.start, tz)}</td><td>{a.status === "active" ? "Ativo" : "Resolvido"}</td></tr>))}</tbody>
        </table></div>)}
      {doc.alerts?.truncated > 0 && <p className="muted small">E mais {doc.alerts.truncated} alertas (veja a tela Alertas).</p>}

      <footer className="rdoc-foot">{doc.notes?.map((n: string) => <p key={n}>{n}</p>)}</footer>
    </article>
  );
}

export function ReportDialog({ ctx, onClose }: { ctx: Ctx; onClose: () => void }) {
  const [preset, setPreset] = useState<Preset>("7d");
  const [from, setFrom] = useState(""), [to, setTo] = useState("");
  const [doc, setDoc] = useState<any>(null);
  const [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const [mail, setMail] = useState(""), [mailErr, setMailErr] = useState(""), [note, setNote] = useState("");
  const tz = ctx.timezone;
  const range = useMemo(() => periodRange(preset, tz, new Date(), { from, to }), [preset, tz, from, to]);

  useEffect(() => {
    let off = false;
    setBusy(true); setErr("");
    api("/reports/document", { params: { ...range, origin: ctx.origin, property_id: ctx.propertyId, tz } })
      .then((d) => { if (!off) setDoc(d); })
      .catch((e) => { if (!off) { setDoc(null); setErr(e instanceof ApiError ? String(e.detail) : "falha ao gerar o relatório"); } })
      .finally(() => { if (!off) setBusy(false); });
    return () => { off = true; };
  }, [range, ctx.origin, ctx.propertyId, tz]);

  useEffect(() => {
    document.body.classList.add("printing-report");
    return () => document.body.classList.remove("printing-report");
  }, []);

  const sendMail = () => {
    if (!doc) return;
    if (mail.trim() && !validEmails(mail)) { setMailErr("Confira os endereços (separe vários por vírgula)."); return; }
    setMailErr("");
    window.location.href = mailtoUrl(mail, doc);
    setNote("Seu programa de e-mail foi aberto com o resumo. Para enviar o relatório completo, salve o PDF e anexe.");
  };
  const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";
  const share = async () => {
    if (!doc) return;
    try { await navigator.share({ title: `Relatório AgroFleet · ${doc.property?.name ?? ""}`, text: reportText(doc) }); }
    catch { /* cancelado pelo usuário */ }
  };

  return (
    <Modal title="Gerar relatório" onClose={onClose} wide className="report-modal">
      <div className="rep-controls">
        <label>Período
          <select value={preset} onChange={(e) => setPreset(e.target.value as Preset)} data-testid="report-period">
            {PRESETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        {preset === "custom" && <>
          <label>De <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>Até <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        </>}
        <span className="muted small">Propriedade e origem seguem os filtros do topo. Horários em {tz}.</span>
      </div>
      <div className="rep-actions">
        <button onClick={() => window.print()} disabled={!doc || busy} data-testid="report-print"><Printer size={16} aria-hidden="true" /> Imprimir / salvar PDF</button>
        <div className="rep-mail">
          <label className="sr-only" htmlFor="rep-mail">E-mail do destinatário</label>
          <input id="rep-mail" type="email" placeholder="destinatario@exemplo.com (opcional)" value={mail} onChange={(e) => setMail(e.target.value)} autoComplete="email" aria-invalid={!!mailErr} />
          <button className="ghost" onClick={sendMail} disabled={!doc || busy} data-testid="report-mail"><Mail size={16} aria-hidden="true" /> Enviar por e-mail</button>
        </div>
        {canShare && <button className="ghost" onClick={share} disabled={!doc || busy}><Share2 size={16} aria-hidden="true" /> Compartilhar</button>}
      </div>
      {mailErr && <p className="err" role="alert">{mailErr}</p>}
      {note && <p className="muted small" role="status">{note}</p>}
      {err && <p className="err" role="alert">{err}</p>}
      <div className="rep-preview" aria-busy={busy}>
        {busy && <p className="rep-busy" role="status">Gerando o relatório… com muitos dados pode levar alguns segundos.</p>}
        {doc && <div className={busy ? "rep-stale" : ""}><ReportBody doc={doc} /></div>}
      </div>
      {doc && createPortal(<div className="print-only"><ReportBody doc={doc} print /></div>, document.body)}
    </Modal>
  );
}
