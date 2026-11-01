export type Origin = "simulated" | "real" | "unknown";
export type OriginFilter = Origin | "all";

export const STATE_COLOR: Record<string, string> = {
  working: "#2e7d32", transport: "#1565c0", idle: "#f9a825", refilling: "#00838f", fault: "#c62828", off: "#616161", unknown: "#9e9e9e",
};
export const STATE_LABEL: Record<string, string> = {
  working: "aplicando", transport: "deslocando", idle: "parada (ligada)", refilling: "reabastecendo", fault: "falha", off: "desligada", unknown: "desconhecido",
};
export const ORIGIN_LABEL: Record<string, string> = { simulated: "SIMULADO", real: "REAL (declarado)", unknown: "ORIGEM NÃO DECLARADA" };
export const ORIGIN_COLOR: Record<string, string> = { simulated: "#b26a00", real: "#1b6e2d", unknown: "#6b6b6b" };

export const stateColor = (s: string) => STATE_COLOR[s] ?? STATE_COLOR.unknown;

export function fmt(v: number | null | undefined, digits = 1, unit = ""): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";   // ausente nunca vira 0
  return `${v.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}${unit ? " " + unit : ""}`;
}
export const fmtMoney = (v: number | null | undefined, cur = "BRL") =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toLocaleString("pt-BR", { style: "currency", currency: cur, maximumFractionDigits: 0 });
export const fmtPct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${fmt(v, d)} %`);
export const msToKmh = (v: number | null | undefined) => (v === null || v === undefined ? null : v * 3.6);

export function hoursHM(h: number | null | undefined): string {
  if (h === null || h === undefined || !Number.isFinite(h)) return "—";
  const m = Math.round(h * 60);
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

/** Mensagem do banner de procedência dos dados, a partir de /summary.by_origin. */
export function provenanceBanner(by: Record<string, { samples: number }> | undefined): { level: "sim" | "mixed" | "real" | "unknown" | "empty"; text: string } {
  const keys = Object.keys(by ?? {}).filter((k) => (by![k].samples ?? 0) > 0);
  if (!keys.length) return { level: "empty", text: "Sem dados. Carregue a demonstração simulada (admin) ou envie um arquivo." };
  if (keys.length === 1 && keys[0] === "simulated") return { level: "sim", text: "TODOS OS DADOS EXIBIDOS SÃO SIMULADOS. Nenhuma máquina, talhão ou resultado aqui é real." };
  if (keys.length === 1 && keys[0] === "real") return { level: "real", text: "Dados marcados como REAIS por quem os enviou (o sistema não consegue comprovar a procedência)." };
  if (keys.length === 1 && keys[0] === "unknown") return { level: "unknown", text: "Origem dos dados NÃO declarada (simulado/real). Trate com cautela." };
  return { level: "mixed", text: `Há dados de origens misturadas (${keys.join(", ")}). Use o filtro de origem; cada item mostra a sua.` };
}

export interface Series { t: number[]; lon: (number | null)[]; lat: (number | null)[]; state: string[]; speed_ms: (number | null)[]; origin: string[] }
export interface Pos { lon: number; lat: number; state: string; speed_ms: number | null; origin: string; interpolated: boolean }

/** Posição no instante t. Nunca extrapola: antes do 1º ou depois do último ponto devolve null; sem posição (perda de GNSS) devolve null;
 *  interpola linearmente só entre dois pontos válidos separados por no máximo maxGap segundos. */
export function positionAt(s: Series, t: number, maxGap = 15): Pos | null {
  const n = s.t.length;
  if (!n || t < s.t[0] || t > s.t[n - 1]) return null;
  let lo = 0, hi = n - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (s.t[mid] <= t) lo = mid; else hi = mid - 1; }
  const i = lo;
  const a = { lon: s.lon[i], lat: s.lat[i] };
  if (a.lon === null || a.lat === null) return null;
  const base = { state: s.state[i], speed_ms: s.speed_ms[i], origin: s.origin[i] };
  if (i + 1 < n && s.lon[i + 1] !== null && s.lat[i + 1] !== null && s.t[i + 1] - s.t[i] <= maxGap && s.t[i + 1] > s.t[i]) {
    const f = (t - s.t[i]) / (s.t[i + 1] - s.t[i]);
    return { lon: a.lon + (s.lon[i + 1]! - a.lon) * f, lat: a.lat + (s.lat[i + 1]! - a.lat) * f, ...base, interpolated: f > 0 };
  }
  return { lon: a.lon, lat: a.lat, ...base, interpolated: false };
}

/** Trilha até t, quebrada onde falta posição (cada trecho vira uma LineString). */
export function trailUntil(s: Series, t: number): [number, number][][] {
  const out: [number, number][][] = [];
  let cur: [number, number][] = [];
  for (let i = 0; i < s.t.length && s.t[i] <= t; i++) {
    if (s.lon[i] === null || s.lat[i] === null) { if (cur.length > 1) out.push(cur); cur = []; continue; }
    cur.push([s.lon[i]!, s.lat[i]!]);
  }
  if (cur.length > 1) out.push(cur);
  return out;
}

export type WsMsg = { type: "hello" | "telemetry" | "alert"; [k: string]: unknown };
export interface MachineRow { machine_id: string; origin: string; state: string; lat: number | null; lon: number | null; last_ts: string; live: boolean; alerts: number;
  speed_ms: number | null; flow_lpm: number | null; pressure_bar: number | null; tank_level_l: number | null; gnss_fix: string; samples: number; field_id: string | null; operator_id: string | null }

/** Aplica uma mensagem 'telemetry' do WebSocket à lista de máquinas (sem inventar campos ausentes). */
export function applyTelemetry(rows: MachineRow[], points: Partial<MachineRow & { ts: string }>[]): MachineRow[] {
  const out = rows.map((r) => ({ ...r }));
  for (const p of points) {
    const i = out.findIndex((r) => r.machine_id === p.machine_id && r.origin === p.origin);
    const patch = { state: p.state, lat: p.lat, lon: p.lon, speed_ms: p.speed_ms, flow_lpm: p.flow_lpm, pressure_bar: p.pressure_bar,
      tank_level_l: p.tank_level_l, gnss_fix: p.gnss_fix, last_ts: p.ts, live: true } as Partial<MachineRow>;
    if (i >= 0) Object.assign(out[i], patch);
    else out.push({ machine_id: p.machine_id!, origin: p.origin!, samples: 1, alerts: 0, field_id: null, operator_id: null, ...patch } as MachineRow);
  }
  return out;
}

export const sparkPath = (xs: (number | null)[], w = 120, h = 28): string => {
  const v = xs.filter((x): x is number => x !== null && Number.isFinite(x));
  if (v.length < 2) return "";
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  let d = "", pen = false;
  xs.forEach((x, i) => {
    if (x === null || !Number.isFinite(x)) { pen = false; return; }
    const px = (i / (xs.length - 1)) * w, py = h - ((x - lo) / span) * h;
    d += `${pen ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`;
    pen = true;
  });
  return d;
};
