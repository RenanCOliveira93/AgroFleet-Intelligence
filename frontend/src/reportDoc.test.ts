import { describe, expect, it } from "vitest";
import { mailtoUrl, periodRange, reportText, validEmails, zonedMidnight } from "./reportDoc";

const SP = "America/Sao_Paulo";
const NOW = new Date("2026-11-21T02:30:00Z");          // 20/11 23:30 em São Paulo

describe("períodos no fuso da propriedade", () => {
  it("hoje começa à meia-noite local, não em UTC", () => {
    expect(periodRange("today", SP, NOW)).toEqual({ start: "2026-11-20T03:00:00.000Z" });
    expect(periodRange("today", "Asia/Tokyo", NOW)).toEqual({ start: "2026-11-20T15:00:00.000Z" });
  });
  it("7 e 30 dias incluem hoje; mês começa no dia 1; todo o histórico não filtra", () => {
    expect(periodRange("7d", SP, NOW).start).toBe("2026-11-14T03:00:00.000Z");
    expect(periodRange("30d", SP, NOW).start).toBe("2026-10-22T03:00:00.000Z");
    expect(periodRange("month", SP, NOW).start).toBe("2026-11-01T03:00:00.000Z");
    expect(periodRange("all", SP, NOW)).toEqual({});
  });
  it("personalizado é inclusivo e ignora datas inválidas", () => {
    expect(periodRange("custom", SP, NOW, { from: "2026-11-01", to: "2026-11-30" }))
      .toEqual({ start: "2026-11-01T03:00:00.000Z", end: "2026-12-01T03:00:00.000Z" });
    expect(periodRange("custom", SP, NOW, { from: "", to: "x" })).toEqual({});
  });
  it("meia-noite em dia de mudança de horário", () => {
    // Nova York: horário de verão termina em 2026-11-01 (meia-noite ainda em EDT, UTC−4)
    expect(zonedMidnight(2026, 11, 1, "America/New_York").toISOString()).toBe("2026-11-01T04:00:00.000Z");
    expect(zonedMidnight(2026, 11, 2, "America/New_York").toISOString()).toBe("2026-11-02T05:00:00.000Z");
  });
});

const DOC = {
  title: "Relatório de operações", timezone: SP, generated_at: "2026-11-21T02:30:00Z", generated_by: "admin",
  period: { start: "2026-11-14T03:00:00Z", end: null }, property: { name: "Fazenda Santa Clara" }, data_origins: ["simulated"],
  totals: { coverage: { unique_ha: 41.234, overlap_pct_of_gross: 3.2 }, time_h: { working: 5.5, idle: 0.75 }, distance_km: 61.2, volume_l: null,
    unique_ha_per_working_h: 7.5, machines: ["A", "B"] },
  operations: [{ name: "Pulverização — Talhão 07", machine_name: "Pulverizador SP-210", status_label: "Em andamento",
    mission: { progress_pct: 62.4, worked_ha: 31.2, planned_ha: 50 } }],
  alerts: { total: 3, active: 1, counts: { critical: 1, warning: 2, info: 0 } },
};

describe("texto do relatório e e-mail", () => {
  it("resume com números do backend, sem inventar o que falta", () => {
    const t = reportText(DOC);
    expect(t).toContain("Fazenda Santa Clara");
    expect(t).toContain("dados SIMULADOS");
    expect(t).toContain("41,23 ha");
    expect(t).toContain("volume aplicado: —");          // ausente fica —
    expect(t).toContain("62 % (31,2 de 50,0 ha)");
    expect(t).toContain("críticos 1, avisos 2");
    expect(t).toContain("não envia comandos");
  });
  it("sem telemetria diz isso", () => {
    expect(reportText({ ...DOC, totals: null, operations: [] })).toContain("Sem telemetria no período.");
  });
  it("mailto codifica assunto e corpo e limita o tamanho", () => {
    const u = mailtoUrl("a@x.com, b@y.com.br", DOC);
    expect(u.startsWith("mailto:a%40x.com,b%40y.com.br?subject=")).toBe(true);
    expect(decodeURIComponent(u.split("&body=")[1])).toContain("anexe o PDF");
    const big = { ...DOC, operations: Array.from({ length: 60 }, (_, i) => ({ ...DOC.operations[0], name: `Operação ${i} com nome longo` })) };
    expect(decodeURIComponent(mailtoUrl("", big, 900).split("&body=")[1]).length).toBeLessThanOrEqual(900);
    expect(mailtoUrl("", DOC).startsWith("mailto:?subject=")).toBe(true);
  });
  it("valida endereços", () => {
    expect(validEmails("a@x.com; b@y.org")).toBe(true);
    expect(validEmails("a@x")).toBe(false);
  });
});
