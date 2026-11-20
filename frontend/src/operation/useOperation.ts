/** Dados do painel "Operação": lista de operações, status (polling + WebSocket), geometria e clima.
 *  Respostas atrasadas de uma operação anterior são descartadas (geração por operação). */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { newerStatus } from "./format";

const STATUS_EVERY_MS = 15000;
const GEOMETRY_MIN_MS = 20000;
const WEATHER_EVERY_MS = 10 * 60 * 1000;
const OPS_EVERY_MS = 60000;

const errText = (e: unknown) =>
  e instanceof ApiError ? `${e.status}: ${typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}` : "falha de rede";

/** Lista de operações (GET /operations), recarregada a cada minuto e quando `reloadKey` muda. */
export function useOperations(reloadKey: unknown) {
  const [ops, setOps] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setOps(await api("/operations")); setError(null); } catch (e) { setError(errText(e)); }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, OPS_EVERY_MS);
    return () => clearInterval(t);
  }, [load, reloadKey]);
  return { ops, error, reload: load };
}

export interface OperationData {
  status: any | null;
  geometry: any | null;
  weather: any | null;
  /** Motivo quando não há clima (endpoint ausente, erro). */
  weatherError: string | null;
  error: string | null;
  loading: boolean;
  /** Date.parse(evaluated_at) − relógio local no recebimento: corrige o relógio do navegador no cronômetro. */
  skewMs: number;
}

const EMPTY: OperationData = { status: null, geometry: null, weather: null, weatherError: null, error: null, loading: true, skewMs: 0 };

export function useOperation(opId: string | null, wsStatus: any | null): OperationData {
  const [d, setD] = useState<OperationData>(EMPTY);
  const gen = useRef(0);
  const geoAt = useRef(0), geoTs = useRef<string | null>(null), geoTimer = useRef<number>();

  // troca de operação: zera tudo e invalida respostas pendentes
  useEffect(() => {
    gen.current += 1;
    geoAt.current = 0; geoTs.current = null; window.clearTimeout(geoTimer.current);
    setD({ ...EMPTY, loading: !!opId });
  }, [opId]);

  const acceptStatus = useCallback((s: any, g: number) => {
    if (g !== gen.current || !s) return;
    setD((prev) => {
      const best = newerStatus(prev.status, s);
      if (best === prev.status) return prev.loading ? { ...prev, loading: false, error: null } : prev;
      const ev = Date.parse(best?.evaluated_at ?? "");
      return { ...prev, status: best, loading: false, error: null, skewMs: Number.isNaN(ev) ? 0 : ev - Date.now() };
    });
  }, []);

  // status: polling a cada 15 s
  useEffect(() => {
    if (!opId) return;
    const g = gen.current;
    let stop = false;
    const tick = async () => {
      try { const s = await api(`/operations/${encodeURIComponent(opId)}/status`); if (!stop) acceptStatus(s, g); }
      catch (e) { if (!stop && g === gen.current) setD((p) => ({ ...p, loading: false, error: errText(e) })); }
    };
    tick();
    const t = setInterval(tick, STATUS_EVERY_MS);
    return () => { stop = true; clearInterval(t); };
  }, [opId, acceptStatus]);

  // status do WebSocket (operation_status), aplicado se for mais novo
  useEffect(() => {
    if (opId && wsStatus && wsStatus.operation?.operation_id === opId) acceptStatus(wsStatus, gen.current);
  }, [opId, wsStatus, acceptStatus]);

  // geometria: na troca de operação e quando mission.last_ts muda (no máximo a cada 20 s)
  const lastTs: string | null = d.status?.mission?.last_ts ?? d.status?.connection?.last_ts ?? null;
  const statusReady = !!d.status;
  useEffect(() => {
    if (!opId || !statusReady) return;
    if (geoAt.current && lastTs === geoTs.current) return;
    const g = gen.current;
    const run = async () => {
      geoAt.current = Date.now(); geoTs.current = lastTs;
      try { const geo = await api(`/operations/${encodeURIComponent(opId)}/geometry`); if (g === gen.current) setD((p) => ({ ...p, geometry: geo })); }
      catch { /* mantém a última geometria; o mapa continua com o que tem */ }
    };
    const wait = geoAt.current ? GEOMETRY_MIN_MS - (Date.now() - geoAt.current) : 0;
    window.clearTimeout(geoTimer.current);
    if (wait <= 0) run(); else geoTimer.current = window.setTimeout(run, wait);
  }, [opId, statusReady, lastTs]);
  useEffect(() => () => window.clearTimeout(geoTimer.current), []);

  // clima: a cada 10 min; 404 = servidor sem o serviço de clima
  useEffect(() => {
    if (!opId) return;
    const g = gen.current;
    let stop = false;
    const tick = async () => {
      try {
        const w = await api(`/operations/${encodeURIComponent(opId)}/weather`);
        if (!stop && g === gen.current) setD((p) => ({ ...p, weather: w, weatherError: null }));
      } catch (e) {
        if (stop || g !== gen.current) return;
        const why = e instanceof ApiError && e.status === 404 ? "Serviço de clima não disponível neste servidor." : `Falha ao consultar o clima (${errText(e)}).`;
        setD((p) => ({ ...p, weather: null, weatherError: why }));
      }
    };
    tick();
    const t = setInterval(tick, WEATHER_EVERY_MS);
    return () => { stop = true; clearInterval(t); };
  }, [opId]);

  return d;
}

/** Relógio que avança a cada `everyMs` só quando `live`; parado, devolve o instante em que parou. */
export function useNow(live: boolean, skewMs = 0, everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [live, everyMs]);
  return now + skewMs;
}
