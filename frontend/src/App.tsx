import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Bell, CalendarCheck, ChartSpline, Check, ChevronDown, Info, LogOut, Monitor, Moon, Sun, Tractor, Truck, User, type LucideIcon } from "lucide-react";
import { api, ApiError, openWs, setToken, type Role } from "./api";
import { AlertsTab } from "./Alerts";
import { CoverageTab } from "./Coverage";
import { FleetTab } from "./Fleet";
import { applyTelemetry, ORIGIN_LABEL, provenanceBanner, type MachineRow, type OriginFilter } from "./lib";
import { PlaybackTab } from "./Playback";
import { ReportsTab } from "./Reports";
import { RoiTab } from "./Roi";
import { AboutTab } from "./About";
import { UploadTab } from "./Upload";
import { PlanningTab } from "./Planning";
import { OperationDashboard } from "./operation/OperationDashboard";
import { alertsForProperty, badgeText, propertyTimezone, PROPERTY_KEY, readStored, resolveProperty, writeStored } from "./shell";
import { THEME_LABEL, useTheme, type ThemePref } from "./theme";

export type SectionId = "operation" | "fleet" | "planning" | "analysis" | "alerts";
type View = SectionId | "about";
export interface Ctx {
  origin: OriginFilter; role: Role; machines: MachineRow[]; refresh: () => void; summary: any; fields: any[];
  /** Propriedade escolhida no cabeçalho (null = todas). */
  propertyId: string | null; properties: any[];
  /** Fuso IANA para EXIBIR horários (o da propriedade; padrão America/Sao_Paulo). */
  timezone: string;
  /** operation_id → corpo do último `operation_status` recebido pelo WebSocket (mesmo formato de GET /operations/{id}/status). */
  lastStatus: Record<string, any>;
  goto: (section: SectionId, sub?: string, params?: Record<string, string>) => void;
  /** Parâmetros passados no último goto() para a seção atual (ex.: { operation_id }, { machine_id }). Aditivo ao contrato. */
  params?: Record<string, string>;
}

const SECTIONS: [SectionId, string, LucideIcon][] = [
  ["operation", "Operação", Tractor], ["fleet", "Frota", Truck], ["planning", "Planejamento", CalendarCheck],
  ["analysis", "Análises", ChartSpline], ["alerts", "Alertas", Bell],
];
const SUBTABS: Partial<Record<SectionId, [string, string][]>> = {
  planning: [["operations", "Operações"], ["upload", "Upload"]],
  analysis: [["playback", "Reprodução"], ["coverage", "Cobertura"], ["reports", "Relatórios"], ["roi", "ROI"]],
};
const DEFAULT_SUB: Record<string, string> = { planning: "operations", analysis: "playback" };
const ROLE_LABEL: Record<string, string> = { admin: "Administrador", analyst: "Analista", viewer: "Visualizador" };
const THEME_ICON: Record<ThemePref, LucideIcon> = { light: Sun, dark: Moon, auto: Monitor };
const ALERTS_EVERY_MS = 30000;

/** Logotipo próprio: folha estilizada (sem marca de terceiros). */
export function LeafLogo({ size = 40 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 40 40" aria-hidden="true" focusable="false">
      <path d="M5 35c-2.5-7.5-.5-15.5 6-19.5 1.2 7.6-.6 14-6 19.5z" fill="var(--accent-2)" />
      <path d="M9 34C9 17 19.5 6 36 5c.6 17.5-10.4 28.6-27 29z" fill="var(--accent)" />
      <path d="M10.5 32.5C16 24 22.5 16.5 31 10" fill="none" stroke="var(--bg)" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

function Brand() {
  return <div className="brand"><LeafLogo /><div className="brand-text"><b>AgroFleet</b><span>Intelligence</span></div></div>;
}

function Login({ onOk }: { onOk: (role: Role, user: string) => void }) {
  const [u, setU] = useState(""), [p, setP] = useState(""), [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const go = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(""); setBusy(true);
    try { const r = await api("/auth/login", { method: "POST", body: { username: u, password: p } }); setToken(r.access_token); onOk(r.role, u); }
    catch (x) { setErr(x instanceof ApiError ? String(x.detail) : "falha de rede"); }
    finally { setBusy(false); }
  };
  return (
    <div className="login-page">
      <form className="login card" onSubmit={go} aria-label="login">
        <Brand />
        <h1>Entrar no AgroFleet Intelligence</h1>
        <p className="muted">Telemetria e análise de operações agrícolas. Somente monitoramento: nenhum comando é enviado às máquinas.</p>
        <label>Usuário<input value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" name="username" required /></label>
        <label>Senha<input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" name="password" required /></label>
        <button type="submit" disabled={busy}>{busy ? "Entrando…" : "Entrar"}</button>
        {err && <p className="err" role="alert">{err}</p>}
      </form>
    </div>
  );
}

function SubTabs({ section, value, onChange }: { section: SectionId; value: string; onChange: (id: string) => void }) {
  const items = SUBTABS[section]!;
  const ref = useRef<HTMLDivElement>(null);
  const onKey = (e: React.KeyboardEvent) => {
    const i = items.findIndex(([id]) => id === value);
    const n = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : null;
    if (n === null) return;
    e.preventDefault();
    const j = (n + items.length) % items.length;
    onChange(items[j][0]);
    ref.current?.querySelectorAll<HTMLButtonElement>("[role=tab]")[j]?.focus();
  };
  return (
    <div className="subtabs" role="tablist" aria-label="Subseções" ref={ref} onKeyDown={onKey}>
      {items.map(([id, label]) => (
        <button key={id} id={`tab-${id}`} role="tab" aria-selected={value === id} aria-controls={`panel-${section}`} tabIndex={value === id ? 0 : -1}
          className={value === id ? "on" : ""} onClick={() => onChange(id)}>{label}</button>))}
    </div>
  );
}

function AccountMenu({ user, role, onAbout, onLogout }: { user: string; role: Role; onAbout: () => void; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const { pref, setPref } = useTheme();
  const box = useRef<HTMLDivElement>(null), btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    box.current?.querySelector<HTMLElement>("[role^=menuitem]")?.focus();
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { setOpen(false); btn.current?.focus(); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = [...(box.current?.querySelectorAll<HTMLElement>("[role^=menuitem]") ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  };
  const act = (f: () => void) => () => { setOpen(false); f(); };
  return (
    <div className="account" ref={box} onKeyDown={onKey}>
      <button ref={btn} className="avatar" aria-haspopup="menu" aria-expanded={open} aria-label={`Conta de ${user}`} title={`${user} (${ROLE_LABEL[role] ?? role})`} onClick={() => setOpen((o) => !o)}>
        <User size={20} strokeWidth={1.75} aria-hidden="true" />
      </button>
      {open && (
        <div className="menu" role="menu" aria-label="Conta">
          <div className="menu-user"><b>{user}</b><span className="muted">{ROLE_LABEL[role] ?? role}</span></div>
          <div className="menu-sep" role="separator" />
          <div className="menu-label" id="menu-theme">Tema</div>
          <div role="group" aria-labelledby="menu-theme">
            {(["light", "dark", "auto"] as ThemePref[]).map((t) => {
              const I = THEME_ICON[t];
              return (
                <button key={t} role="menuitemradio" aria-checked={pref === t} tabIndex={-1} className="menu-item" onClick={() => setPref(t)}>
                  <I size={16} aria-hidden="true" /><span>{THEME_LABEL[t]}</span>{pref === t && <Check size={16} className="menu-check" aria-hidden="true" />}
                </button>);
            })}
          </div>
          <div className="menu-sep" role="separator" />
          <button role="menuitem" tabIndex={-1} className="menu-item" onClick={act(onAbout)}><Info size={16} aria-hidden="true" /><span>Sobre / limites</span></button>
          <button role="menuitem" tabIndex={-1} className="menu-item" onClick={act(onLogout)}><LogOut size={16} aria-hidden="true" /><span>Sair</span></button>
        </div>)}
    </div>
  );
}

function PropertySelect({ properties, value, onChange }: { properties: any[]; value: string | null; onChange: (v: string | null) => void }) {
  const single = properties.length === 1;
  return (
    <label className="farm" title="Propriedade">
      <span className="sr-only">Propriedade</span>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} disabled={single} data-testid="property-select">
        {!single && <option value="">Todas as propriedades</option>}
        {properties.map((p) => <option key={p.property_id} value={p.property_id}>{p.name ?? p.property_id}</option>)}
      </select>
      <ChevronDown size={18} className="farm-chev" aria-hidden="true" />
    </label>
  );
}

export function App() {
  const [session, setSession] = useState<{ role: Role; user: string } | null>(null);
  const [view, setView] = useState<View>("operation");
  const [subs, setSubs] = useState<Record<string, string>>(DEFAULT_SUB);
  const [params, setParams] = useState<Record<string, string>>({});
  const [origin, setOrigin] = useState<OriginFilter>("all");
  const [machines, setMachines] = useState<MachineRow[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [fields, setFields] = useState<any[]>([]);
  const [properties, setProperties] = useState<any[]>([]);
  const [savedProp, setSavedProp] = useState<string | null>(() => readStored(PROPERTY_KEY));
  const [lastStatus, setLastStatus] = useState<Record<string, any>>({});
  const [activeAlerts, setActiveAlerts] = useState<number | null>(null);
  const [ws, setWs] = useState<"connecting" | "open" | "closed">("closed");
  const [lastAlert, setLastAlert] = useState<any>(null);
  const originRef = useRef(origin);
  originRef.current = origin;
  const propertyId = resolveProperty(properties, savedProp);

  const logout = useCallback(() => { setToken(null); setSession(null); setLastStatus({}); setActiveAlerts(null); }, []);

  const refresh = useCallback(async () => {
    try {
      const [m, s, f, p] = await Promise.all([api("/machines", { params: { origin } }), api("/summary"), api("/fields"),
        api("/registry/properties").catch((e) => { if (e instanceof ApiError && e.status === 401) throw e; return null; })]);
      setMachines(m); setSummary(s); setFields(f); if (p) setProperties(p);
    } catch (e) { if (e instanceof ApiError && e.status === 401) logout(); }
  }, [origin, logout]);

  // Badge de Alertas: ativos da origem e da propriedade escolhidas (o vínculo com a propriedade vem do cadastro).
  const loadAlerts = useCallback(async () => {
    try {
      const [al, rm, ops] = await Promise.all([api("/alerts", { params: { status: "active", origin, limit: 5000 } }),
        propertyId ? api("/registry/machines") : Promise.resolve([]), propertyId ? api("/operations") : Promise.resolve([])]);
      setActiveAlerts(alertsForProperty(al, propertyId, rm, ops).length);
    } catch (e) { if (e instanceof ApiError && e.status === 401) logout(); else setActiveAlerts(null); }
  }, [origin, propertyId, logout]);
  const alertsTimer = useRef<number>();
  const loadAlertsSoon = useCallback(() => { window.clearTimeout(alertsTimer.current); alertsTimer.current = window.setTimeout(loadAlerts, 1000); }, [loadAlerts]);

  useEffect(() => { if (session) refresh(); }, [session, refresh]);
  useEffect(() => {
    if (!session) return;
    loadAlerts();
    const t = setInterval(loadAlerts, ALERTS_EVERY_MS);
    return () => { clearInterval(t); window.clearTimeout(alertsTimer.current); };
  }, [session, loadAlerts]);
  const onAlertMsg = useRef(loadAlertsSoon);
  onAlertMsg.current = loadAlertsSoon;
  useEffect(() => {
    if (!session) return;
    const t = setInterval(refresh, 10000);
    const close = openWs((m) => {
      if (m.type === "telemetry") setMachines((rows) => applyTelemetry(rows, m.points.filter((p: any) => originRef.current === "all" || p.origin === originRef.current)));
      if (m.type === "alert") { setLastAlert(m.alert); onAlertMsg.current(); }
      if (m.type === "operation_status" && m.operation_id && m.status) setLastStatus((prev) => ({ ...prev, [m.operation_id]: m.status }));
    }, setWs);
    return () => { clearInterval(t); close(); };
  }, [session, refresh]);

  const goto = useCallback((section: SectionId | "about", sub?: string, p?: Record<string, string>) => {
    setView(section);
    if (sub && SUBTABS[section as SectionId]?.some(([id]) => id === sub)) setSubs((s) => ({ ...s, [section]: sub }));
    setParams(p ?? {});
  }, []);

  if (!session) return <Login onOk={(role, user) => { setView("operation"); setParams({}); setSession({ role, user }); }} />;
  const banner = provenanceBanner(summary?.by_origin);
  const timezone = propertyTimezone(properties, propertyId);
  const ctx: Ctx = { origin, role: session.role, machines, refresh, summary, fields, propertyId, properties, timezone, lastStatus, goto, params };
  const chooseProperty = (v: string | null) => { setSavedProp(v); writeStored(PROPERTY_KEY, v); };
  const badge = badgeText(activeAlerts ?? 0);
  const sub = subs[view] ?? DEFAULT_SUB[view];

  let content: ReactNode = null;
  if (view === "operation") content = <OperationDashboard ctx={ctx} />;
  else if (view === "fleet") content = <FleetTab ctx={ctx} />;
  else if (view === "alerts") content = <AlertsTab ctx={ctx} params={params} onClearParams={() => setParams({})} />;
  else if (view === "about") content = <AboutTab ctx={ctx} />;
  else if (view === "planning") content = sub === "upload" ? <UploadTab ctx={ctx} /> : <PlanningTab ctx={ctx} />;
  else if (view === "analysis") content = sub === "coverage" ? <CoverageTab ctx={ctx} /> : sub === "reports" ? <ReportsTab ctx={ctx} /> : sub === "roi" ? <RoiTab ctx={ctx} /> : <PlaybackTab ctx={ctx} />;
  const hasSubs = view !== "about" && !!SUBTABS[view];

  return (
    <div className={`app view-${view}`}>
      <a className="skip" href="#conteudo">Pular para o conteúdo</a>
      <header className="topbar">
        <Brand />
        <nav className="mainnav" aria-label="Seções">
          {SECTIONS.map(([id, label, Icon]) => (
            <button key={id} className={view === id ? "on" : ""} aria-current={view === id ? "page" : undefined} onClick={() => goto(id)} data-testid={`nav-${id}`}>
              <Icon size={20} strokeWidth={1.75} aria-hidden="true" />
              <span className="nav-label">{label}</span>
              {id === "alerts" && badge && <><span className="nav-badge" aria-hidden="true" data-testid="alerts-badge">{badge}</span><span className="sr-only"> ({activeAlerts} alertas ativos)</span></>}
            </button>))}
        </nav>
        <div className="topbar-right">
          <PropertySelect properties={properties} value={propertyId} onChange={chooseProperty} />
          <AccountMenu user={session.user} role={session.role} onAbout={() => goto("about")} onLogout={logout} />
        </div>
      </header>
      <div className="ctxbar">
        {banner.level === "sim" && <span className="sim-pill" data-testid="sim-mode">MODO SIMULAÇÃO</span>}
        <div className={`banner banner-${banner.level}`} role="status" data-testid="provenance-banner">{banner.text}</div>
        <div className="ctxbar-right">
          <div className="seg" role="group" aria-label="filtro de origem">
            {(["all", "simulated", "real", "unknown"] as const).map((o) => (
              <button key={o} className={origin === o ? "on" : ""} onClick={() => setOrigin(o)} aria-pressed={origin === o}>{o === "all" ? "Todas as origens" : ORIGIN_LABEL[o]}</button>))}
          </div>
          <span className={`ws ws-${ws}`} title="Conexão ao vivo (WebSocket)" data-testid="ws-state"><i aria-hidden="true" />{ws === "open" ? "ao vivo" : ws === "connecting" ? "conectando…" : "sem conexão ao vivo"}</span>
        </div>
      </div>
      {lastAlert && (
        <div className="toast" role="status">
          <Bell size={16} aria-hidden="true" /> <span>Novo alerta informativo · {lastAlert.machine_id} · {lastAlert.label ?? lastAlert.type}</span>
          <button className="link" onClick={() => { goto("alerts", undefined, lastAlert.machine_id ? { machine_id: lastAlert.machine_id } : {}); setLastAlert(null); }}>ver</button>
          <button className="link" onClick={() => setLastAlert(null)}>fechar</button>
        </div>)}
      <main id="conteudo" className={`main main-${view}`} tabIndex={-1}>
        {view === "about" && <h1 className="sr-only">Sobre / limites</h1>}
        {hasSubs && <SubTabs section={view as SectionId} value={sub} onChange={(id) => { setSubs((s) => ({ ...s, [view]: id })); setParams({}); }} />}
        <div id={`panel-${view}`} role={hasSubs ? "tabpanel" : undefined} aria-labelledby={hasSubs ? `tab-${sub}` : undefined}>{content}</div>
      </main>
    </div>
  );
}
