/** Formatação do painel "Operação" (funções puras). Nenhum cálculo operacional aqui: autonomia, ETA, médias e áreas vêm
 *  do backend (/operations/{id}/status). Este módulo só escolhe, formata e explica o que já veio. Ausente = "—", nunca 0. */

export const DASH = "—";
export const DEFAULT_TZ = "America/Sao_Paulo";
export const CRITICAL_NOTE = "A severidade crítica representa risco operacional estimado, não uma decisão automática de segurança. "
  + "O sistema permanece exclusivamente de monitoramento, sem comandos para máquinas.";
export const NOT_REPORTED = "não reportado pela máquina";

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

const NF = new Map<number, Intl.NumberFormat>();
function nf(d: number) {
  let f = NF.get(d);
  if (!f) NF.set(d, (f = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d })));
  return f;
}

/** Número pt-BR com `d` casas ("25,4", "1.950"). Ausente/NaN = "—". Nunca mostra "-0". */
export function num(v: number | null | undefined, d = 1): string {
  if (!isNum(v)) return DASH;
  const r = Number(v.toFixed(d));
  return nf(d).format(r === 0 ? 0 : r);
}
/** Inteiro pt-BR com separador de milhar ("1.950"). */
export const int = (v: number | null | undefined) => num(v, 0);
/** m/s → km/h (null continua null). */
export const kmh = (ms: number | null | undefined): number | null => (isNum(ms) ? ms * 3.6 : null);

/** Segundos → "HH:MM:SS" (horas podem passar de 99). Ausente = "—". */
export function hms(sec: number | null | undefined): string {
  if (!isNum(sec)) return DASH;
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

/** Horas → "03h 05min" (arredonda ao minuto). */
export function hm(h: number | null | undefined): string {
  if (!isNum(h)) return DASH;
  const m = Math.max(0, Math.round(h * 60));
  return `${String(Math.floor(m / 60)).padStart(2, "0")}h ${String(m % 60).padStart(2, "0")}min`;
}
/** "(em 03h 05min)" ou "" se ausente. */
export const inHM = (h: number | null | undefined) => (isNum(h) ? `(em ${hm(h)})` : "");

/** Horas com uma casa: "4,9 h". */
export const hoursText = (h: number | null | undefined, d = 1) => (isNum(h) ? `${num(h, d)} h` : DASH);

/** Idade em segundos → "45 s", "49 min", "2 h 05 min", "3 d". */
export function age(sec: number | null | undefined): string {
  if (!isNum(sec)) return DASH;
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) { const m = Math.floor(s / 60); return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`; }
  return `${Math.floor(s / 86400)} d`;
}

const parse = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
};

function safeTz(tz: string | null | undefined): string {
  try { new Intl.DateTimeFormat("pt-BR", { timeZone: tz || DEFAULT_TZ }); return tz || DEFAULT_TZ; } catch { return DEFAULT_TZ; }
}
/** "aaaa-mm-dd" do instante no fuso. */
export function dayKey(ms: number, tz: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: safeTz(tz), year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
}
function nextDay(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
}
/** "15:42" no fuso. */
export function clock(iso: string | null | undefined, tz: string): string {
  const t = parse(iso);
  if (t === null) return DASH;
  return new Intl.DateTimeFormat("pt-BR", { timeZone: safeTz(tz), hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(t));
}
/** Prefixo do dia relativo ao agora no fuso: "Hoje", "Amanhã" ou "dd/mm". */
export function dayLabel(iso: string | null | undefined, tz: string, nowMs: number): string {
  const t = parse(iso);
  if (t === null) return DASH;
  const k = dayKey(t, tz), today = dayKey(nowMs, tz);
  if (k === today) return "Hoje";
  if (k === nextDay(today)) return "Amanhã";
  return `${k.slice(8, 10)}/${k.slice(5, 7)}`;
}
/** ETA: "Hoje, 15:42" / "Amanhã, 08:10" / "22/11, 09:00". Ausente = "—". */
export function etaLabel(iso: string | null | undefined, tz: string, nowMs: number): string {
  if (parse(iso) === null) return DASH;
  return `${dayLabel(iso, tz, nowMs)}, ${clock(iso, tz)}`;
}
/** Faixa da ETA, compacta: "15:20–16:05" (mesmo dia que hoje), "Amanhã 08:10–09:30" ou "Hoje, 22:10 – Amanhã, 01:30". */
export function etaRangeLabel(range: (string | null)[] | null | undefined, tz: string, nowMs: number): string {
  if (!range || range.length < 2 || parse(range[0]) === null || parse(range[1]) === null) return "";
  const [a, b] = range as string[];
  const da = dayLabel(a, tz, nowMs), db = dayLabel(b, tz, nowMs);
  if (da === db) return `${da === "Hoje" ? "" : da + " "}${clock(a, tz)}–${clock(b, tz)}`;
  return `${etaLabel(a, tz, nowMs)} – ${etaLabel(b, tz, nowMs)}`;
}
/** "20/11 15:00" no fuso. */
export function shortDateTime(iso: string | null | undefined, tz: string): string {
  const t = parse(iso);
  if (t === null) return DASH;
  const k = dayKey(t, tz);
  return `${k.slice(8, 10)}/${k.slice(5, 7)} ${clock(iso, tz)}`;
}

/** Faixa "21–29" (ou valor único). Ausente = "—". */
export function range(lo: number | null | undefined, hi: number | null | undefined, d = 0): string {
  const a = isNum(lo), b = isNum(hi);
  if (a && b) return num(lo, d) === num(hi, d) ? num(lo, d) : `${num(lo, d)}–${num(hi, d)}`;
  if (a) return num(lo, d);
  if (b) return num(hi, d);
  return DASH;
}

// ---------------------------------------------------------------- máquina / conexão / tempo de operação
export const STATE_TEXT: Record<string, string> = {
  working: "Em operação", transport: "Em deslocamento", idle: "Parada (ligada)", refilling: "Reabastecendo",
  fault: "Em falha", off: "Desligada", unknown: "Estado desconhecido",
};
export const stateText = (s: string | null | undefined) => (s ? STATE_TEXT[s] ?? s : "Estado não reportado");

export type Tone = "ok" | "warn" | "danger" | "info" | "muted";
export const CONNECTION_TONE: Record<string, Tone> = { online: "ok", delayed: "warn", offline: "danger", unknown: "muted" };

/** Segunda linha do card do veículo: estado da máquina e da conexão, com a idade do dado quando não está online. */
export function machineLine(status: any, tz: string = DEFAULT_TZ): { text: string; tone: Tone; title: string } {
  const conn = status?.connection ?? {}, st = status?.snapshot?.state as string | undefined;
  const ageS = conn.age_s as number | undefined, label = (conn.label as string) || "Conexão desconhecida";
  const lastTs = conn.last_ts ?? status?.snapshot?.ts;
  const title = lastTs ? `Último dado: ${shortDateTime(lastTs, tz)}${isNum(ageS) ? ` (há ${age(ageS)})` : ""}` : "Sem dados da máquina";
  if (!status?.snapshot) return { text: `Sem dados da máquina · ${label}`, tone: "muted", title };
  if (conn.state === "online") return { text: stateText(st), tone: st === "fault" ? "danger" : st === "working" ? "ok" : st === "idle" ? "warn" : "info", title };
  if (conn.state === "delayed") return { text: `${stateText(st)} · ${label}${isNum(ageS) ? ` (dado de há ${age(ageS)})` : ""}`, tone: "warn", title };
  return { text: `Último estado: ${stateText(st)} · ${label}${isNum(ageS) ? ` há ${age(ageS)}` : ""}`, tone: conn.state === "offline" ? "danger" : "muted", title };
}

export interface OpClock { seconds: number | null; live: boolean; note: string }
/** Tempo de operação desde `operation.start`. Conta ao vivo só com conexão "online"; senão congela no último dado. */
export function operationClock(op: { start?: string | null; end?: string | null } | null | undefined,
  conn: { state?: string; last_ts?: string | null } | null | undefined, nowMs: number): OpClock {
  const s = parse(op?.start);
  if (s === null) return { seconds: null, live: false, note: "início da operação desconhecido" };
  const e = parse(op?.end);
  if (e !== null) return { seconds: (e - s) / 1000, live: false, note: "operação encerrada" };
  if (conn?.state === "online") return { seconds: (nowMs - s) / 1000, live: true, note: "" };
  const last = parse(conn?.last_ts);
  if (last === null) return { seconds: null, live: false, note: "sem dados da máquina" };
  return { seconds: (last - s) / 1000, live: false, note: "até o último dado" };
}

// ---------------------------------------------------------------- operações
export interface OpLite { operation_id: string; property_id?: string | null; origin?: string; status?: string; start?: string | null; machine_id?: string; name?: string; operation_label?: string }
/** Operações candidatas (da propriedade e da origem escolhidas; ativas primeiro, mais recente primeiro) e a escolhida:
 *  pedida (goto) > salva > a ativa mais recente. Sem ativas, mostra as demais da propriedade. */
export function pickOperation<T extends OpLite>(ops: T[], o: { propertyId?: string | null; origin?: string; saved?: string | null; requested?: string | null }):
  { list: T[]; selected: T | null } {
  const mine = ops.filter((x) => (!o.propertyId || x.property_id === o.propertyId) && (!o.origin || o.origin === "all" || x.origin === o.origin));
  const active = mine.filter((x) => x.status === "active");
  const list = [...(active.length ? active : mine)].sort((a, b) => (parse(b.start) ?? 0) - (parse(a.start) ?? 0) || a.operation_id.localeCompare(b.operation_id));
  const find = (id?: string | null) => (id ? list.find((x) => x.operation_id === id) ?? null : null);
  return { list, selected: find(o.requested) ?? find(o.saved) ?? list[0] ?? null };
}

/** Status mais novo (por `evaluated_at`) entre o do polling e o do WebSocket. */
export function newerStatus(a: any, b: any): any {
  if (!a) return b ?? null;
  if (!b) return a;
  const ta = parse(a.evaluated_at) ?? -Infinity, tb = parse(b.evaluated_at) ?? -Infinity;
  return tb > ta ? b : a;
}

/** Últimos `n` intervalos da série da missão para um campo (lacuna = null, não 0). */
export function lastBuckets(series: any[] | null | undefined, key: string, n: number): { values: (number | null)[]; complete: boolean[]; t: string[] } {
  const s = (series ?? []).slice(-n);
  return { values: s.map((b) => (isNum(b?.[key]) ? b[key] : null)), complete: s.map((b) => b?.complete !== false), t: s.map((b) => b?.t_start ?? "") };
}

const SEV_RANK: Record<string, number> = { critical: 0, warning: 1, info: 2 };
/** Alertas ATIVOS desta operação (ou da máquina, quando o alerta não traz a operação). Críticos primeiro, depois os mais novos. */
export function activeAlerts(status: any): any[] {
  const opId = status?.operation?.operation_id, mid = status?.operation?.machine_id;
  return (status?.alerts ?? [])
    .filter((a: any) => (a.status ?? "active") === "active" && (a.operation_id ? a.operation_id === opId : !mid || a.machine_id === mid))
    .sort((a: any, b: any) => (SEV_RANK[a.severity] ?? 3) - (SEV_RANK[b.severity] ?? 3) || (parse(b.start) ?? 0) - (parse(a.start) ?? 0));
}

export const isElectric = (status: any) =>
  status?.machine?.energy_source === "electric" || status?.autonomy?.energy_source === "electric" || status?.consumption?.source === "battery_power_kw";

// ---------------------------------------------------------------- autonomia (só formatação do que o backend decidiu)
export interface AutonomyView { tone: "green" | "yellow" | "red" | "gray"; title: string; text: string; critical: boolean; title_attr: string }
const AUTONOMY_TITLE: Record<string, string> = { green: "Autonomia suficiente", yellow: "Margem apertada", red: "Autonomia insuficiente", gray: "Indeterminado" };
export function autonomyView(status: any): AutonomyView {
  const a = status?.autonomy;
  if (!a) {
    const why = typeof status?.autonomy_unavailable === "string" ? status.autonomy_unavailable : status?.autonomy_unavailable?.message;
    return { tone: "gray", title: "Indeterminado", text: why || "Autonomia não calculada.", critical: false, title_attr: why || "" };
  }
  const tone = (["green", "yellow", "red", "gray"].includes(a.status) ? a.status : "gray") as AutonomyView["tone"];
  const reasons: string[] = (a.reasons ?? []).map((r: any) => r?.message).filter(Boolean);
  const margin = isNum(a.margin_pct) ? `margem de ${num(a.margin_pct, 0)} %` : "";
  const req = isNum(a.required_margin_pct) ? `${num(a.required_margin_pct, 0)} %` : "";
  let text: string;
  const done = (a.reasons ?? []).find((r: any) => r?.code === "mission_done")?.message as string | undefined;
  if (tone === "green") text = done ?? `Operação deve ser concluída conforme planejado${margin ? ` (${margin})` : ""}.`;
  else if (tone === "yellow") text = reasons[0] ?? `Concluir é possível, mas com ${margin || "margem pequena"}${req ? `, abaixo dos ${req} exigidos` : ""}.`;
  else if (tone === "red") text = `Autonomia estimada menor que o tempo restante da missão${margin ? ` (${margin})` : ""}.`;
  else text = reasons[0] ?? "Sem dados suficientes para estimar.";
  const title_attr = [`Status do backend: ${a.status_label ?? a.status}`, ...reasons, a.basis].filter(Boolean).join("\n");
  return { tone, title: AUTONOMY_TITLE[tone], text, critical: tone === "red", title_attr };
}

// ---------------------------------------------------------------- clima (contrato A)
export interface WxItem { value: string; unit: string; label: string; title: string; source: "observation" | "forecast" | "none"; tag?: string }
export interface WeatherView { available: boolean; reason: string; temp?: WxItem; wind?: WxItem; humidity?: WxItem; attribution: string; note: string }

/** Monta os blocos de clima: observação primeiro, senão previsão (marcada "previsão"); condição do céu só de forecast.summary. */
export function weatherView(w: any, unavailableReason: string | null, tz: string): WeatherView {
  const attribution = (w?.attribution as string) || "";
  const note = (w?.location?.note as string) || (w?.location?.fictitious ? "Talhão em local fictício: o clima é o do ponto mostrado, não de um talhão real." : "");
  const reasons = (w?.reasons ?? []).map((r: any) => r?.message).filter(Boolean).join(" · ");
  if (!w || w.status === "unavailable" || (!w.observation && !w.forecast))
    return { available: false, reason: unavailableReason || reasons || w?.message || "Sem dados meteorológicos", attribution, note };
  const o = w.observation, f = w.forecast;
  const model = o?.kind === "model";
  const oTitle = o ? [
    `${model ? "Estimativa de modelo" : "Observação"} ${o.provider_label ?? o.provider ?? ""}`.trim(),
    o.station_name ? `estação ${o.station_name}${o.station_uf ? "/" + o.station_uf : ""}${o.station_code ? ` (${o.station_code})` : ""}` : "",
    isNum(o.distance_km) ? `a ${num(o.distance_km, 1)} km` : "",
    o.observed_at ? `em ${shortDateTime(o.observed_at, tz)}` : "",
    o.stale ? "dado antigo" : "",
  ].filter(Boolean).join(" · ") : "";
  const fTitle = f ? [`Previsão ${f.provider_label ?? f.provider ?? ""}`.trim(), f.municipality ? `${f.municipality}${f.uf ? "/" + f.uf : ""}` : "",
    f.period_label ?? "", f.date ? `${String(f.date).slice(8, 10)}/${String(f.date).slice(5, 7)}` : ""].filter(Boolean).join(" · ") : "";
  const tag = model ? "modelo" : o?.stale ? "antigo" : undefined;
  const condition = (f?.summary as string) || (o?.station_name ? `Estação ${o.station_name}` : "");

  let temp: WxItem;
  if (o && isNum(o.temperature_c)) temp = { value: num(o.temperature_c, 0), unit: "°C", label: condition, title: oTitle, source: "observation", tag };
  else if (f && (isNum(f.temp_min_c) || isNum(f.temp_max_c))) temp = { value: range(f.temp_min_c, f.temp_max_c), unit: "°C", label: condition, title: fTitle, source: "forecast", tag: "previsão" };
  else temp = { value: DASH, unit: "", label: condition || "temperatura não informada", title: "Temperatura não informada pelo provedor", source: "none" };

  let wind: WxItem;
  if (o && isNum(o.wind_speed_kmh)) wind = { value: num(o.wind_speed_kmh, 0), unit: "km/h", label: "Vento", title: oTitle + (isNum(o.wind_gust_kmh) ? ` · rajada ${num(o.wind_gust_kmh, 0)} km/h` : ""), source: "observation", tag };
  else if (f && (f.wind_intensity || f.wind_dir)) wind = { value: [f.wind_intensity, f.wind_dir].filter(Boolean).join(" "), unit: "", label: "Vento", title: fTitle, source: "forecast", tag: "previsão" };
  else wind = { value: DASH, unit: "", label: "Vento", title: "Vento não informado pelo provedor", source: "none" };

  let humidity: WxItem;
  if (o && isNum(o.humidity_pct)) humidity = { value: num(o.humidity_pct, 0), unit: "%", label: "Umidade", title: oTitle, source: "observation", tag };
  else if (f && (isNum(f.humidity_min_pct) || isNum(f.humidity_max_pct))) humidity = { value: range(f.humidity_min_pct, f.humidity_max_pct), unit: "%", label: "Umidade", title: fTitle, source: "forecast", tag: "previsão" };
  else humidity = { value: DASH, unit: "", label: "Umidade", title: "Umidade não informada pelo provedor", source: "none" };

  return { available: true, reason: reasons, temp, wind, humidity, attribution, note };
}
