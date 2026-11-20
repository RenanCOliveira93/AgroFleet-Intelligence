/** Peças do painel "Operação": primitivas (KpiCard, ProgressBar, AlertItem, Sparkline, Bars, StatusBlock, VDivider) e os
 *  cartões montados a partir do corpo de GET /operations/{id}/status. Só formatam o que o backend calculou. */
import { useId, type ReactNode } from "react";
import {
  BatteryMedium, Bell, ChevronRight, CircleCheck, CircleHelp, Clock, CloudOff, CloudSun, Cog, Droplet, Droplets, Engine, Fuel, Gauge, Info,
  OctagonAlert, Target, TriangleAlert, Wheat, Wifi, WifiOff, Wind, Zap, type LucideIcon,
} from "lucide-react";
import { TractorArt } from "./TractorArt";
import { useNow } from "./useOperation";
import {
  activeAlerts, autonomyView, CONNECTION_TONE, CRITICAL_NOTE, DASH, etaLabel, etaRangeLabel, hms, hm, inHM, int, isElectric, isNum, kmh, lastBuckets,
  machineLine, NOT_REPORTED, num, operationClock, shortDateTime, type Tone, type WeatherView, type WxItem,
} from "./format";

// ================================================================ primitivas

export const VDivider = () => <span className="opd-vdiv" aria-hidden="true" />;
export const Dot = ({ tone }: { tone: Tone }) => <span className={`opd-dot tone-${tone}`} aria-hidden="true" />;

/** Número grande + unidade pequena. `title` explica ausências. */
export function BigValue({ value, unit, title, size = "md", testid }: { value: string; unit?: string; title?: string; size?: "md" | "lg"; testid?: string }) {
  const missing = value === DASH;
  return (
    <span className={`opd-big opd-big-${size}${missing ? " is-missing" : ""}`} title={title} data-testid={testid}>
      <b>{value}</b>{unit && !missing && <span className="opd-unit">{unit}</span>}
    </span>
  );
}

export function CardHead({ icon: Icon, title, id, tone = "accent", right }: { icon: LucideIcon; title: ReactNode; id?: string; tone?: "accent" | "danger" | "muted"; right?: ReactNode }) {
  return (
    <div className="opd-head">
      <Icon className={`opd-ico ico-${tone}`} size={22} strokeWidth={1.9} aria-hidden="true" />
      <h2 id={id}>{title}</h2>
      {right}
    </div>
  );
}

export function KpiCard({ icon: Icon, label, value, unit, sub, title, testid }: {
  icon: LucideIcon; label: string; value: string; unit?: string; sub?: ReactNode; title?: string; testid?: string;
}) {
  return (
    <div className="card opd-card opd-kpi" title={title} data-testid={testid} role="group" aria-label={`${label}: ${value === DASH ? "sem valor" : value}${unit && value !== DASH ? " " + unit : ""}`}>
      <Icon className="opd-ico ico-accent opd-kpi-ico" size={30} strokeWidth={1.7} aria-hidden="true" />
      <div className="opd-kpi-body">
        <span className="opd-label">{label}</span>
        <BigValue value={value} unit={unit} />
        {sub && <span className="opd-kpi-sub">{sub}</span>}
      </div>
    </div>
  );
}

export function ProgressBar({ pct, label }: { pct: number | null | undefined; label: string }) {
  const v = isNum(pct) ? Math.min(100, Math.max(0, pct)) : null;
  return (
    <div className="opd-bar" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={v ?? undefined}
      aria-valuetext={v === null ? "desconhecido" : `${num(v, 0)} %`}>
      <span className="opd-bar-fill" style={{ width: `${v ?? 0}%` }} />
    </div>
  );
}

const SEV: Record<string, { icon: LucideIcon; tone: Tone; label: string }> = {
  critical: { icon: OctagonAlert, tone: "danger", label: "Crítico" },
  warning: { icon: TriangleAlert, tone: "warn", label: "Aviso" },
  info: { icon: Info, tone: "info", label: "Informativo" },
};

export function AlertItem({ alert, onOpen }: { alert: any; onOpen?: (a: any) => void }) {
  const s = SEV[alert.severity] ?? SEV.info;
  const I = s.icon;
  const msg = alert.message || alert.label || alert.type;
  return (
    <li>
      <button type="button" className={`opd-alert sev-${alert.severity ?? "info"}`} onClick={() => onOpen?.(alert)} data-testid="op-alert"
        title={[alert.label, alert.rule, alert.note].filter(Boolean).join("\n")} aria-label={`${s.label}: ${msg}. Abrir em Alertas`}>
        <I className={`opd-ico tone-${s.tone}`} size={20} strokeWidth={2} aria-hidden="true" />
        <span className="opd-alert-text">
          <span className="opd-alert-msg">{msg}</span>
          {alert.severity === "critical" && <span className="opd-alert-note">{alert.note || CRITICAL_NOTE}</span>}
        </span>
        <ChevronRight className="opd-chev" size={20} aria-hidden="true" />
      </button>
    </li>
  );
}

/** Caminhos suaves (cúbica monotônica, sem ultrapassar os pontos) por trecho contínuo; lacunas (null) continuam lacunas. */
export function smoothPaths(values: (number | null)[], w: number, h: number, pad = 3): { line: string; area: string; dots: [number, number][] } {
  const v = values.filter(isNum);
  if (!v.length) return { line: "", area: "", dots: [] };
  let lo = Math.min(...v), hi = Math.max(...v);
  let span = hi - lo;
  if (span < Math.max(Math.abs(hi) * 0.05, 1e-9)) { const m = Math.max(Math.abs(hi) * 0.05, 1); lo -= m; hi += m; span = hi - lo; }
  lo -= span * 0.25; span = hi - lo;                                   // folga embaixo: área sempre visível
  const n = values.length;
  const X = (i: number) => (n === 1 ? w / 2 : (i / (n - 1)) * w);
  const Y = (y: number) => pad + (1 - (y - lo) / span) * (h - 2 * pad);
  const segs: [number, number][][] = [];
  let cur: [number, number][] = [];
  values.forEach((y, i) => { if (isNum(y)) cur.push([X(i), Y(y)]); else { if (cur.length) segs.push(cur); cur = []; } });
  if (cur.length) segs.push(cur);
  let line = "", area = "";
  const dots: [number, number][] = [];
  const f = (x: number) => x.toFixed(1);
  for (const p of segs) {
    if (p.length === 1) { dots.push(p[0]); continue; }
    const k = p.length, d: number[] = [], m: number[] = new Array(k).fill(0);
    for (let i = 0; i < k - 1; i++) d.push((p[i + 1][1] - p[i][1]) / (p[i + 1][0] - p[i][0]));
    m[0] = d[0]; m[k - 1] = d[k - 2];
    for (let i = 1; i < k - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
    for (let i = 0; i < k - 1; i++) {
      if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
      const a = m[i] / d[i], b = m[i + 1] / d[i], s = a * a + b * b;
      if (s > 9) { const t = 3 / Math.sqrt(s); m[i] = t * a * d[i]; m[i + 1] = t * b * d[i]; }
    }
    let seg = `M${f(p[0][0])},${f(p[0][1])}`;
    for (let i = 0; i < k - 1; i++) {
      const dx = (p[i + 1][0] - p[i][0]) / 3;
      seg += `C${f(p[i][0] + dx)},${f(p[i][1] + m[i] * dx)} ${f(p[i + 1][0] - dx)},${f(p[i + 1][1] - m[i + 1] * dx)} ${f(p[i + 1][0])},${f(p[i + 1][1])}`;
    }
    line += seg;
    area += `${seg}L${f(p[k - 1][0])},${f(h)}L${f(p[0][0])},${f(h)}Z`;
  }
  return { line, area, dots };
}

export function Sparkline({ values, width = 150, height = 52, label }: { values: (number | null)[]; width?: number; height?: number; label: string }) {
  const { line, area, dots } = smoothPaths(values, width, height);
  const gid = `opd-sg-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  if (!line && !dots.length) return <span className="opd-chart-empty" style={{ width, height }} role="img" aria-label={`${label}: sem dados`}>sem série</span>;
  return (
    <svg className="opd-spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--accent)" stopOpacity=".45" />
          <stop offset="1" stopColor="var(--accent)" stopOpacity=".03" />
        </linearGradient>
      </defs>
      {area && <path d={area} fill={`url(#${gid})`} />}
      {line && <path d={line} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />}
      {dots.map(([x, y], i) => <circle key={i} cx={x} cy={y} r="2.5" fill="var(--accent)" />)}
    </svg>
  );
}

/** Barras verticais; intervalo incompleto (o atual) em verde mais escuro; lacuna = espaço vazio com traço na base. */
export function Bars({ values, complete, width = 150, height = 52, label }: { values: (number | null)[]; complete?: boolean[]; width?: number; height?: number; label: string }) {
  const v = values.filter(isNum);
  if (!v.length) return <span className="opd-chart-empty" style={{ width, height }} role="img" aria-label={`${label}: sem dados`}>sem série</span>;
  const max = Math.max(...v, 1e-9), n = values.length, gap = 3, bw = Math.max(2, (width - gap * (n - 1)) / n);
  return (
    <svg className="opd-bars" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      {values.map((y, i) => {
        const x = i * (bw + gap);
        if (!isNum(y)) return <rect key={i} x={x} y={height - 2} width={bw} height={2} className="opd-bar-gap" />;
        const bh = Math.max(2, (Math.max(0, y) / max) * (height - 2));
        return <rect key={i} x={x} y={height - bh} width={bw} height={bh} rx={1.5} className={complete && complete[i] === false ? "opd-bar-partial" : "opd-bar-full"} />;
      })}
    </svg>
  );
}

const STATUS_ICON: Record<string, LucideIcon> = { green: CircleCheck, yellow: TriangleAlert, red: OctagonAlert, gray: CircleHelp };
export function StatusBlock({ tone, title, text, tooltip }: { tone: "green" | "yellow" | "red" | "gray"; title: string; text: string; tooltip?: string }) {
  const I = STATUS_ICON[tone];
  return (
    <div className={`opd-status st-${tone}`} title={tooltip} data-testid="autonomy-status" role="status">
      <span className="opd-status-ico"><I size={24} strokeWidth={2.4} aria-hidden="true" /></span>
      <span className="opd-status-text"><b>{title}</b><span>{text}</span></span>
    </div>
  );
}

// ================================================================ cartões do painel

/** Cronômetro "Tempo de operação": avança só com conexão online (re-renderiza só este pedaço). */
export function OperationTimer({ status, skewMs = 0 }: { status: any; skewMs?: number }) {
  const live = status?.connection?.state === "online" && !status?.operation?.end;
  const now = useNow(live, skewMs);
  const c = operationClock(status?.operation, status?.connection, now);
  return (
    <span className="opd-timer-box">
      <span className="opd-label">Tempo de operação</span>
      <b className="opd-timer" data-testid="op-timer" aria-live="off">{hms(c.seconds)}</b>
      {c.note && <span className="opd-small">{c.note}</span>}
    </span>
  );
}

export function VehicleCard({ status, ops, selectedId, onSelect, tz, skewMs }: {
  status: any; ops: any[]; selectedId: string | null; onSelect: (id: string) => void; tz: string; skewMs?: number;
}) {
  const op = status?.operation ?? {}, conn = status?.connection ?? {};
  const name = status?.machine?.name || op.machine_id || "Máquina";
  const line = machineLine(status, tz);
  const ctone = CONNECTION_TONE[conn.state] ?? "muted";
  const W = conn.state === "online" || conn.state === "delayed" ? Wifi : WifiOff;
  const opLabel = op.operation_label || op.name || op.operation_type || DASH;
  return (
    <section className="card opd-card opd-vehicle" aria-labelledby="opd-veh-title" data-testid="vehicle-card">
      <div className="opd-veh-art"><TractorArt width={148} /></div>
      <VDivider />
      <div className="opd-veh-info">
        <div className="opd-veh-head">
          <h2 id="opd-veh-title" title={[name, status?.machine?.kind_label, status?.machine?.energy_label].filter(Boolean).join(" · ")}>{name}</h2>
          {status?.mode === "simulated" && <span className="sim-pill" data-testid="op-sim-pill">{status.mode_label || "MODO SIMULAÇÃO"}</span>}
        </div>
        <p className="opd-line" title={op.name}><Dot tone={op.status === "active" ? "ok" : "muted"} />Operação: {opLabel}{op.status && op.status !== "active" ? ` (${op.status_label ?? op.status})` : ""}</p>
        <p className="opd-line opd-line-wrap" title={line.title} data-testid="machine-line"><Dot tone={line.tone} />{line.text}</p>
      </div>
      <div className="opd-veh-right">
        {ops.length > 1 && (
          <label className="opd-opsel">
            <span className="sr-only">Operação exibida</span>
            <select value={selectedId ?? ""} onChange={(e) => onSelect(e.target.value)} data-testid="op-select">
              {ops.map((o) => <option key={o.operation_id} value={o.operation_id}>{o.machine_id} · {o.name || o.operation_label || o.operation_id}</option>)}
            </select>
          </label>)}
        <div className="opd-conn">
          <W className={`opd-wifi tone-${ctone}`} size={30} strokeWidth={2} role="img" aria-label={`Conexão: ${conn.label || "desconhecida"}`} />
          <OperationTimer status={status} skewMs={skewMs} />
        </div>
      </div>
    </section>
  );
}

export function KpiRow({ status, tz }: { status: any; tz: string }) {
  const s = status?.snapshot ?? {}, a = status?.autonomy;
  const at = s.ts ? `Leitura de ${shortDateTime(s.ts, tz)}${status?.connection?.state !== "online" ? ` (${status?.connection?.label ?? "sem conexão"})` : ""}` : "Sem leitura";
  const miss = (v: unknown, extra = "") => (isNum(v) ? at + extra : `${NOT_REPORTED}. ${at}`);
  const speed = kmh(s.speed_ms);
  const elec = isElectric(status);
  const amount = isNum(a?.level_amount) ? a.level_amount : elec && isNum(s.battery_energy_kwh) ? s.battery_energy_kwh : null;
  const unit = a?.unit || (elec ? "kWh" : "L");
  const levelPct = elec ? s.battery_soc_pct : s.fuel_level_pct;
  const sub = (v: unknown) => (isNum(v) ? null : <span className="muted">não reportado</span>);
  return (
    <div className="opd-kpis">
      <KpiCard icon={Gauge} label="Velocidade" value={num(speed, 1)} unit="km/h" title={miss(speed)} sub={sub(speed)} testid="kpi-speed" />
      <KpiCard icon={Cog} label="Rotação (RPM)" value={int(s.engine_rpm)} title={miss(s.engine_rpm)} sub={sub(s.engine_rpm)} testid="kpi-rpm" />
      <KpiCard icon={Engine} label="Carga do motor" value={int(s.engine_load_pct)} unit="%" title={miss(s.engine_load_pct)} sub={sub(s.engine_load_pct)} testid="kpi-load" />
      <KpiCard icon={elec ? BatteryMedium : Fuel} label={elec ? "Bateria" : "Combustível"} value={int(levelPct)} unit="%" testid="kpi-energy"
        title={miss(levelPct, isNum(amount) ? ` · ${num(amount, elec ? 1 : 0)} ${unit}${a?.level_source ? ` (${a.level_source})` : ""}` : "")}
        sub={isNum(levelPct) ? (isNum(amount) ? `(${num(amount, elec ? 1 : 0)} ${unit})` : null) : sub(levelPct)} />
    </div>
  );
}

export function MissionRow({ status, tz, now }: { status: any; tz: string; now: number }) {
  const m = status?.mission ?? {};
  const eta = m.eta as string | null;
  const reason = (m.eta_reasons ?? [])[0]?.message as string | undefined;
  const rng = etaRangeLabel(m.eta_range, tz, now);
  return (
    <div className="opd-row opd-row-mission">
      <section className="card opd-card opd-mission" aria-labelledby="opd-mis-title" title={m.basis}>
        <CardHead icon={Target} title="Missão da Operação" id="opd-mis-title" />
        <div className="opd-mis-line">
          <span data-testid="mission-area">Área trabalhada: <b>{num(m.worked_ha, 1)} ha</b>{isNum(m.planned_ha) ? <> de <b>{num(m.planned_ha, 1)} ha</b></> : <span className="muted"> (área planejada desconhecida)</span>}</span>
          <b className="opd-pct" data-testid="mission-pct">{isNum(m.progress_pct) ? `${num(m.progress_pct, 0)} %` : DASH}</b>
        </div>
        <ProgressBar pct={m.progress_pct} label="Progresso da missão" />
      </section>
      <section className="card opd-card opd-eta" aria-labelledby="opd-eta-title" title={[m.basis, ...(m.eta_reasons ?? []).map((r: any) => r.message)].filter(Boolean).join("\n")}>
        <div className="opd-head opd-head-light"><Clock className="opd-ico ico-accent" size={22} strokeWidth={1.9} aria-hidden="true" /><h2 id="opd-eta-title">Previsão de conclusão</h2></div>
        {eta ? (
          <>
            <BigValue value={etaLabel(eta, tz, now)} testid="eta" />
            <span className="opd-sub">{inHM(m.remaining_h)}{rng && <span className="opd-small"> · faixa {rng}</span>}</span>
          </>
        ) : (
          <>
            <BigValue value="Indeterminada" testid="eta" />
            <span className="opd-sub opd-clamp">{reason || "Sem dados suficientes para estimar."}</span>
          </>
        )}
      </section>
    </div>
  );
}

export function AutonomyCard({ status }: { status: any }) {
  const a = status?.autonomy, m = status?.mission ?? {};
  const v = autonomyView(status);
  const elec = isElectric(status);
  const remReason = (m.eta_reasons ?? [])[0]?.message as string | undefined;
  return (
    <section className="card opd-card opd-autonomy" aria-labelledby="opd-aut-title">
      <CardHead icon={elec ? BatteryMedium : Fuel} title="Autonomia e Conclusão" id="opd-aut-title" />
      <div className="opd-aut-grid">
        <div className="opd-part">
          <span className="opd-label">Autonomia estimada</span>
          <BigValue size="lg" value={num(a?.autonomy_h, 1)} unit="h" testid="autonomy-h"
            title={a ? (isNum(a.autonomy_h) ? a.basis : (a.reasons ?? []).map((r: any) => r.message).join("\n")) : v.text} />
        </div>
        <VDivider />
        <div className="opd-part">
          <span className="opd-label">Tempo restante da missão</span>
          <BigValue size="lg" value={num(m.remaining_h, 1)} unit="h" testid="remaining-h" title={isNum(m.remaining_h) ? `${hm(m.remaining_h)} · ${m.basis ?? ""}` : remReason} />
        </div>
        <StatusBlock tone={v.tone} title={v.title} text={v.text} tooltip={v.title_attr} />
      </div>
      {v.critical && <p className="opd-critical-note" data-testid="critical-note">{CRITICAL_NOTE}</p>}
    </section>
  );
}

export function ConsumptionRow({ status }: { status: any }) {
  const c = status?.consumption, m = status?.mission ?? {};
  const elec = isElectric(status);
  const key = c?.source || (elec ? "battery_power_kw" : "fuel_rate_lph");
  const unit = c?.unit || (key === "battery_power_kw" ? "kW" : "L/h");
  const cs = lastBuckets(status?.series, key, 18);
  const ps = lastBuckets(status?.series, "ha_per_h", 10);
  const cTitle = !c ? "Este servidor não informa o consumo médio da operação." : isNum(c.avg) ? c.basis : c.reason || "Consumo não reportado.";
  return (
    <div className="opd-row opd-row-2">
      <section className="card opd-card opd-metric" aria-labelledby="opd-con-title">
        <CardHead icon={elec ? Zap : Droplet} title={elec ? "Consumo de energia" : "Consumo de combustível"} id="opd-con-title" />
        <div className="opd-metric-body">
          <div className="opd-metric-main">
            <BigValue value={num(c?.avg, 1)} unit={unit} title={cTitle} testid="consumption-avg" />
            <span className="opd-sub" title={cTitle}>{!c ? "indisponível" : isNum(c.avg) ? "Média da operação" : c.reason || "não reportado"}</span>
          </div>
          <Sparkline values={cs.values} label={`${elec ? "Potência" : "Consumo"} nos últimos ${cs.values.length} intervalos de 10 min (${unit})`} />
        </div>
      </section>
      <section className="card opd-card opd-metric" aria-labelledby="opd-prod-title">
        <CardHead icon={Wheat} title="Produtividade" id="opd-prod-title" />
        <div className="opd-metric-body">
          <div className="opd-metric-main">
            <BigValue value={num(m.rate_ha_h, 1)} unit="ha/h" title={isNum(m.rate_ha_h) ? "Área nova por hora no tempo engajado da operação (backend)." : "Ritmo ainda não calculado."} testid="rate" />
            <span className="opd-sub">{isNum(m.rate_ha_h) ? "Média da operação" : "não calculada"}</span>
          </div>
          <Bars values={ps.values} complete={ps.complete} label={`Produtividade nos últimos ${ps.values.length} intervalos de 10 min (ha/h)`} />
        </div>
      </section>
    </div>
  );
}

export function AlertsCard({ status, onOpen }: { status: any; onOpen: (a: any) => void }) {
  const list = activeAlerts(status);
  return (
    <section className="card opd-card opd-alerts" aria-labelledby="opd-al-title" data-testid="op-alerts">
      <CardHead icon={Bell} tone={list.length ? "danger" : "muted"} title={`Alertas (${list.length})`} id="opd-al-title" />
      {list.length ? <ul className="opd-alert-list">{list.map((a) => <AlertItem key={a.id ?? `${a.type}-${a.start}`} alert={a} onOpen={onOpen} />)}</ul>
        : <p className="opd-empty-line">Nenhum alerta ativo</p>}
    </section>
  );
}

function WxBlock({ icon: Icon, item, testid }: { icon: LucideIcon; item: WxItem; testid: string }) {
  return (
    <div className="opd-fb-block" title={item.title} data-testid={testid}>
      <Icon className="opd-ico ico-fg" size={28} strokeWidth={1.6} aria-hidden="true" />
      <div className="opd-fb-text">
        <span className="opd-fb-val">
          <b>{item.value}</b>{item.unit && item.value !== DASH && <span className="opd-unit">{item.unit}</span>}
          {item.tag && <span className="opd-tag">{item.tag}</span>}
        </span>
        {item.label && <span className="opd-fb-sub">{item.label}</span>}
      </div>
    </div>
  );
}

export function FieldBar({ status, weather }: { status: any; weather: WeatherView }) {
  const f = status?.field ?? {}, m = status?.mission ?? {};
  const area = isNum(f.area_ha) ? f.area_ha : m.planned_ha;
  return (
    <section className="card opd-card opd-fieldbar" aria-label="Talhão e clima" data-testid="field-bar">
      <div className="opd-fb-block opd-fb-field">
        <Wheat className="opd-ico ico-accent" size={32} strokeWidth={1.7} aria-hidden="true" />
        <div className="opd-fb-text">
          <b className="opd-fb-title">{f.name || f.field_id || "Talhão"} – {isNum(area) ? `${num(area, 1)} ha` : "área desconhecida"}</b>
          <span className="opd-fb-sub">Progresso: {isNum(m.progress_pct) ? `${num(m.progress_pct, 0)} %` : DASH}</span>
        </div>
      </div>
      <VDivider />
      {weather.available && weather.temp && weather.wind && weather.humidity ? (
        <div className="opd-fb-wx" title={weather.note || undefined}>
          <WxBlock icon={CloudSun} item={weather.temp} testid="wx-temp" />
          <VDivider />
          <WxBlock icon={Wind} item={weather.wind} testid="wx-wind" />
          <VDivider />
          <WxBlock icon={Droplets} item={weather.humidity} testid="wx-hum" />
        </div>
      ) : (
        <div className="opd-fb-wx opd-fb-none" title={[weather.reason, weather.note].filter(Boolean).join("\n")} data-testid="wx-none">
          <CloudOff className="opd-ico ico-muted" size={26} strokeWidth={1.6} aria-hidden="true" />
          <div className="opd-fb-text"><b className="opd-fb-title">Sem dados meteorológicos</b><span className="opd-fb-sub">{weather.reason}</span></div>
        </div>
      )}
      {weather.attribution && <span className="opd-attr" title={weather.note || undefined}>{weather.attribution}{weather.note ? " · local fictício" : ""}</span>}
    </section>
  );
}
