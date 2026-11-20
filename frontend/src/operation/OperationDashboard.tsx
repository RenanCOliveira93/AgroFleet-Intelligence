/** Tela "Operação": mapa da operação + talhão/clima (esquerda) e veículo, KPIs, missão, autonomia, consumo,
 *  produtividade e alertas (direita). Todos os valores vêm da API; ausente = "—" com o motivo no title. */
import { useMemo, useState } from "react";
import { Tractor } from "lucide-react";
import type { Ctx } from "../App";
import { api, ApiError } from "../api";
import { MapView, type MapData } from "../MapView";
import { AlertsCard, AutonomyCard, ConsumptionRow, FieldBar, KpiRow, MissionRow, VehicleCard } from "./components";
import { isNum, pickOperation, weatherView } from "./format";
import { useNow, useOperation, useOperations } from "./useOperation";
import "./operation.css";

const OP_KEY = "agf-operation";
const readSaved = () => { try { return localStorage.getItem(OP_KEY); } catch { return null; } };
const writeSaved = (v: string) => { try { localStorage.setItem(OP_KEY, v); } catch { /* sem armazenamento: só não lembra a escolha */ } };

function EmptyState({ ctx, filtered, onLoaded }: { ctx: Ctx; filtered: boolean; onLoaded: () => void }) {
  const [msg, setMsg] = useState(""), [busy, setBusy] = useState(false);
  const load = async () => {
    setBusy(true); setMsg("Carregando cenários simulados…");
    try {
      const r = await api("/demo/operations/load", { method: "POST", params: { scenarios: "A,B,C", hours: 3 } });
      setMsg(`${r.operations?.length ?? 0} operações SIMULADAS carregadas (${r.stored ?? 0} amostras).`);
      ctx.refresh(); onLoaded();
    } catch (e) { setMsg(e instanceof ApiError ? `Falha: ${typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}` : "Falha de rede."); }
    finally { setBusy(false); }
  };
  return (
    <section className="card opd-empty" data-testid="op-empty">
      <Tractor size={40} strokeWidth={1.5} className="opd-ico ico-accent" aria-hidden="true" />
      <h1>Nenhuma operação {filtered ? "para os filtros atuais" : "cadastrada"}</h1>
      <p className="muted">
        {filtered ? "Troque a propriedade no cabeçalho ou o filtro de origem para ver outras operações."
          : "Cadastre uma operação em Planejamento ou envie telemetria. Para conhecer a tela, carregue os cenários simulados."}
      </p>
      {ctx.role === "admin" && <div className="opd-empty-act"><button onClick={load} disabled={busy} data-testid="load-sim-ops">Carregar operações SIMULADAS</button></div>}
      {msg && <p role="status">{msg}</p>}
    </section>
  );
}

export function OperationDashboard({ ctx }: { ctx: Ctx }) {
  const [reload, setReload] = useState(0);
  const { ops, error: opsError } = useOperations(`${ctx.propertyId}|${reload}`);
  const [saved, setSaved] = useState<string | null>(readSaved);
  const { list, selected } = useMemo(() => pickOperation(ops ?? [], { propertyId: ctx.propertyId, origin: ctx.origin, saved, requested: ctx.params?.operation_id }),
    [ops, ctx.propertyId, ctx.origin, saved, ctx.params?.operation_id]);
  const opId = selected?.operation_id ?? null;
  const d = useOperation(opId, opId ? ctx.lastStatus[opId] ?? null : null);
  const st = d.status;
  // "agora" para Hoje/Amanhã da ETA (a cada 30 s; o cronômetro tem relógio próprio, por segundo)
  const now = useNow(true, d.skewMs, 30000);
  const choose = (id: string) => { setSaved(id); writeSaved(id); };

  const g = d.geometry;
  const snap = st?.snapshot;
  const machine = useMemo(() => {
    const useSnap = snap && isNum(snap.lat) && isNum(snap.lon) && (!g?.last?.ts || Date.parse(snap.ts) >= Date.parse(g.last.ts));
    const p = useSnap ? { lon: snap.lon, lat: snap.lat, heading_deg: snap.heading_deg, state: snap.state } : g?.last && isNum(g.last.lat) && isNum(g.last.lon) ? g.last : null;
    if (!p) return null;
    return { lon: p.lon, lat: p.lat, heading_deg: p.heading_deg ?? null, state: p.state ?? "unknown", origin: st?.operation?.origin ?? g?.mode ?? "unknown",
      label: st?.machine?.name ?? st?.operation?.machine_id, id: st?.operation?.machine_id };
  }, [snap?.lat, snap?.lon, snap?.heading_deg, snap?.state, snap?.ts, g, st?.operation?.origin, st?.machine?.name]);   // eslint-disable-line react-hooks/exhaustive-deps
  const mapData = useMemo<MapData>(() => ({ fields: g?.field, worked: g?.worked, pending: g?.pending, outside: g?.outside, tracks: g?.track, machine }), [g, machine]);
  const wx = useMemo(() => weatherView(d.weather, d.weatherError, ctx.timezone), [d.weather, d.weatherError, ctx.timezone]);
  const fictitious = (st?.mode ?? g?.mode) === "simulated" || !!d.weather?.location?.fictitious;

  if (ops === null && !opsError) return <p className="opd-loading" role="status">Carregando operações…</p>;
  if (opsError && !ops) return <section className="card opd-empty"><h1>Operação</h1><p className="err" role="alert">Não foi possível listar as operações ({opsError}).</p></section>;
  if (!selected) return <EmptyState ctx={ctx} filtered={(ops ?? []).length > 0} onLoaded={() => setReload((x) => x + 1)} />;

  const openAlert = (a: any) => ctx.goto("alerts", undefined, a.machine_id ? { machine_id: a.machine_id } : undefined);

  return (
    <div className="opd" data-testid="operation-dashboard">
      <h1 className="sr-only">Operação {st?.operation?.name ?? selected.name ?? selected.operation_id}</h1>
      <div className="opd-left">
        <section className="opd-map" aria-label="Mapa da operação">
          <MapView variant="operation" fill fitKey={opId ?? undefined} fictitious={fictitious} data={mapData}
            ariaLabel={`Mapa da operação ${st?.operation?.name ?? opId}: talhão, área trabalhada e posição da máquina`} />
        </section>
        <FieldBar status={st} weather={wx} />
      </div>
      <div className="opd-right" aria-busy={d.loading}>
        {d.error && !st && <p className="err opd-error" role="alert">Não foi possível carregar o status da operação ({d.error}).</p>}
        {d.error && st && <p className="opd-small opd-error" role="status">Atualização falhou ({d.error}); mostrando o último status recebido.</p>}
        <VehicleCard status={st ?? { operation: selected }} ops={list} selectedId={opId} onSelect={choose} tz={ctx.timezone} skewMs={d.skewMs} />
        <KpiRow status={st} tz={ctx.timezone} />
        <MissionRow status={st} tz={ctx.timezone} now={now} />
        <AutonomyCard status={st} />
        <ConsumptionRow status={st} />
        <AlertsCard status={st} onOpen={openAlert} />
      </div>
    </div>
  );
}
