import { useCallback, useEffect, useRef, useState } from "react";
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

const TABS = [["fleet", "Frota"], ["playback", "Reprodução"], ["coverage", "Cobertura"], ["alerts", "Alertas"], ["reports", "Relatórios"], ["upload", "Upload"], ["roi", "ROI"], ["about", "Sobre / limites"]] as const;
type TabId = (typeof TABS)[number][0];
export interface Ctx { origin: OriginFilter; role: Role; machines: MachineRow[]; refresh: () => void; summary: any; fields: any[] }

function Login({ onOk }: { onOk: (role: Role, user: string) => void }) {
  const [u, setU] = useState(""), [p, setP] = useState(""), [err, setErr] = useState("");
  const go = async (e: React.FormEvent) => {
    e.preventDefault(); setErr("");
    try { const r = await api("/auth/login", { method: "POST", body: { username: u, password: p } }); setToken(r.access_token); onOk(r.role, u); }
    catch (x) { setErr(x instanceof ApiError ? String(x.detail) : "falha de rede"); }
  };
  return (
    <form className="login card" onSubmit={go} aria-label="login">
      <h1>AgroFleet Intelligence</h1>
      <p className="muted">Telemetria e analytics de pulverizadores. Somente leitura/análise — nenhum comando é enviado às máquinas.</p>
      <label>Usuário<input value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" name="username" /></label>
      <label>Senha<input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" name="password" /></label>
      <button type="submit">Entrar</button>
      {err && <p className="err" role="alert">{err}</p>}
    </form>
  );
}

export function App() {
  const [session, setSession] = useState<{ role: Role; user: string } | null>(null);
  const [tab, setTab] = useState<TabId>("fleet");
  const [origin, setOrigin] = useState<OriginFilter>("all");
  const [machines, setMachines] = useState<MachineRow[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [fields, setFields] = useState<any[]>([]);
  const [ws, setWs] = useState<"connecting" | "open" | "closed">("closed");
  const [lastAlert, setLastAlert] = useState<any>(null);
  const originRef = useRef(origin);
  originRef.current = origin;

  const refresh = useCallback(async () => {
    try {
      const [m, s, f] = await Promise.all([api("/machines", { params: { origin } }), api("/summary"), api("/fields")]);
      setMachines(m); setSummary(s); setFields(f);
    } catch (e) { if (e instanceof ApiError && e.status === 401) setSession(null); }
  }, [origin]);

  useEffect(() => { if (session) refresh(); }, [session, refresh]);
  useEffect(() => {
    if (!session) return;
    const t = setInterval(refresh, 10000);
    const close = openWs((m) => {
      if (m.type === "telemetry") setMachines((rows) => applyTelemetry(rows, m.points.filter((p: any) => originRef.current === "all" || p.origin === originRef.current)));
      if (m.type === "alert") setLastAlert(m.alert);
    }, setWs);
    return () => { clearInterval(t); close(); };
  }, [session, refresh]);

  if (!session) return <Login onOk={(role, user) => setSession({ role, user })} />;
  const banner = provenanceBanner(summary?.by_origin);
  const ctx: Ctx = { origin, role: session.role, machines, refresh, summary, fields };
  return (
    <div className="app">
      <header>
        <div className="brand"><b>AgroFleet Intelligence</b><span className="muted"> · {session.user} ({session.role})</span></div>
        <div className="seg" role="group" aria-label="filtro de origem">
          {(["all", "simulated", "real", "unknown"] as const).map((o) => (
            <button key={o} className={origin === o ? "on" : ""} onClick={() => setOrigin(o)} aria-pressed={origin === o}>{o === "all" ? "Todas as origens" : ORIGIN_LABEL[o]}</button>))}
        </div>
        <span className={`ws ws-${ws}`} title="WebSocket" data-testid="ws-state">{ws === "open" ? "ao vivo" : ws === "connecting" ? "conectando…" : "sem conexão ao vivo"}</span>
        <button className="link" onClick={() => { setToken(null); setSession(null); }}>Sair</button>
      </header>
      <div className={`banner banner-${banner.level}`} role="status" data-testid="provenance-banner">{banner.text}</div>
      {lastAlert && <div className="toast" role="status">Novo alerta informativo · {lastAlert.machine_id} · {lastAlert.type} <button className="link" onClick={() => setLastAlert(null)}>fechar</button></div>}
      <nav role="tablist">{TABS.map(([id, label]) => (<button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>{label}</button>))}</nav>
      <main>
        {tab === "fleet" && <FleetTab ctx={ctx} />}
        {tab === "playback" && <PlaybackTab ctx={ctx} />}
        {tab === "coverage" && <CoverageTab ctx={ctx} />}
        {tab === "alerts" && <AlertsTab ctx={ctx} />}
        {tab === "reports" && <ReportsTab ctx={ctx} />}
        {tab === "upload" && <UploadTab ctx={ctx} />}
        {tab === "roi" && <RoiTab ctx={ctx} />}
        {tab === "about" && <AboutTab ctx={ctx} />}
      </main>
    </div>
  );
}
