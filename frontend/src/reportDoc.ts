/** Relatório para impressão/envio (Etapa 10): períodos no fuso da propriedade e texto do e-mail.
 * Os números vêm prontos de GET /reports/document; aqui só se formata. */
import { fmt, fmtPct, hoursHM } from "./lib";
import { fmtDateTime } from "./shell";

export type Preset = "today" | "7d" | "30d" | "month" | "all" | "custom";
export const PRESETS: [Preset, string][] = [["today", "Hoje"], ["7d", "Últimos 7 dias"], ["30d", "Últimos 30 dias"], ["month", "Este mês"], ["all", "Todo o histórico"], ["custom", "Personalizado"]];

/** Diferença (ms) entre o relógio local de ``tz`` e UTC no instante ``t``. */
export function tzOffsetMs(t: number, tz: string): number {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
    .formatToParts(new Date(t)).filter((x) => x.type !== "literal").map((x) => [x.type, Number(x.value)]));
  return Date.UTC(p.year, p.month - 1, p.day, p.hour % 24, p.minute, p.second) - Math.floor(t / 1000) * 1000;
}

/** Instante UTC da meia-noite de y-m-d no fuso ``tz`` (correto também em dias de mudança de horário). */
export function zonedMidnight(y: number, m: number, d: number, tz: string): Date {
  const guess = Date.UTC(y, m - 1, d);
  let t = guess - tzOffsetMs(guess, tz);
  t = guess - tzOffsetMs(t, tz);
  return new Date(t);
}

/** Data (y, m, d) de ``t`` no fuso ``tz``. */
export function zonedYMD(t: Date, tz: string): [number, number, number] {
  const s = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(t);
  const [y, m, d] = s.split("-").map(Number);
  return [y, m, d];
}

const addDays = ([y, m, d]: [number, number, number], n: number): [number, number, number] => {
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return [t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate()];
};

/** Período do filtro em ISO UTC. ``custom`` usa datas "AAAA-MM-DD" inclusivas (fim = meia-noite do dia seguinte). */
export function periodRange(p: Preset, tz: string, now: Date, custom?: { from: string; to: string }): { start?: string; end?: string } {
  const today = zonedYMD(now, tz);
  const mid = (ymd: [number, number, number]) => zonedMidnight(...ymd, tz).toISOString();
  switch (p) {
    case "today": return { start: mid(today) };
    case "7d": return { start: mid(addDays(today, -6)) };
    case "30d": return { start: mid(addDays(today, -29)) };
    case "month": return { start: mid([today[0], today[1], 1]) };
    case "all": return {};
    case "custom": {
      const parse = (s?: string) => (s && /^\d{4}-\d{2}-\d{2}$/.test(s) ? (s.split("-").map(Number) as [number, number, number]) : null);
      const a = parse(custom?.from), b = parse(custom?.to);
      return { ...(a ? { start: mid(a) } : {}), ...(b ? { end: mid(addDays(b, 1)) } : {}) };
    }
  }
}

export function periodLabel(doc: any): string {
  const tz = doc.timezone;
  const a = doc.period?.start ? fmtDateTime(doc.period.start, tz) : "início dos registros";
  const b = doc.period?.end ? fmtDateTime(doc.period.end, tz) : fmtDateTime(doc.generated_at, tz);
  return `${a} até ${b}`;
}

export const SEVERITY_LABEL: Record<string, string> = { critical: "Crítico", warning: "Aviso", info: "Informativo" };

/** Texto simples do relatório para o corpo do e-mail ou para compartilhar (WhatsApp etc.). */
export function reportText(doc: any, maxOps = 10): string {
  const t = doc.totals, L: string[] = [];
  const prop = doc.property?.name ?? "Todas as propriedades";
  L.push(`AgroFleet Intelligence · ${doc.title}`, `${prop}`, `Período: ${periodLabel(doc)} (${doc.timezone})`);
  if (doc.data_origins?.includes("simulated")) L.push("ATENÇÃO: dados SIMULADOS, não representam máquinas reais.");
  L.push("");
  if (t) {
    L.push("RESUMO",
      `Área trabalhada (única): ${fmt(t.coverage?.unique_ha, 2, "ha")} · sobreposição ${fmtPct(t.coverage?.overlap_pct_of_gross)}`,
      `Tempo em trabalho: ${hoursHM(t.time_h?.working)} · parada ligada: ${hoursHM(t.time_h?.idle)}`,
      `Distância: ${fmt(t.distance_km, 1, "km")} · volume aplicado: ${fmt(t.volume_l, 0, "L")} · ritmo: ${fmt(t.unique_ha_per_working_h, 1, "ha/h")}`,
      `Máquinas com dados: ${t.machines?.length ?? 0}`, "");
  } else L.push("Sem telemetria no período.", "");
  if (doc.operations?.length) {
    L.push("OPERAÇÕES");
    for (const o of doc.operations.slice(0, maxOps)) {
      const m = o.mission;
      const prog = m ? `${fmtPct(m.progress_pct, 0)} (${fmt(m.worked_ha, 1)} de ${fmt(m.planned_ha, 1, "ha")})` : "progresso indisponível";
      L.push(`- ${o.name} · ${o.machine_name ?? o.machine_id} · ${o.status_label} · ${prog}`);
    }
    if (doc.operations.length > maxOps) L.push(`  … e mais ${doc.operations.length - maxOps} no relatório completo`);
    L.push("");
  }
  const c = doc.alerts?.counts ?? {};
  L.push(`ALERTAS no período: ${doc.alerts?.total ?? 0} (críticos ${c.critical ?? 0}, avisos ${c.warning ?? 0}, informativos ${c.info ?? 0}; ativos agora ${doc.alerts?.active ?? 0})`, "");
  L.push(`Gerado em ${fmtDateTime(doc.generated_at, doc.timezone)}${doc.generated_by ? ` por ${doc.generated_by}` : ""}.`,
    "Alertas são informativos; o sistema só monitora e não envia comandos às máquinas.");
  return L.join("\n");
}

/** Link mailto: abre o programa de e-mail do usuário; nenhum servidor de e-mail é usado. O corpo é limitado para caber
 * no limite de tamanho de URL dos clientes de e-mail. */
export function mailtoUrl(to: string, doc: any, maxBody = 1800): string {
  const subject = `Relatório AgroFleet · ${doc.property?.name ?? "todas as propriedades"} · ${periodLabel(doc)}`;
  let body = reportText(doc);
  body += "\n\nRelatório completo: anexe o PDF salvo pelo botão Imprimir / salvar PDF.";
  if (body.length > maxBody) body = body.slice(0, maxBody - 40) + "\n… (resumo cortado; veja o PDF)";
  const rcpt = to.split(/[;,\s]+/).filter(Boolean).map(encodeURIComponent).join(",");
  return `mailto:${rcpt}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

export const validEmails = (to: string) => to.split(/[;,\s]+/).filter(Boolean).every((e) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e));
