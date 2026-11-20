import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { AlertsCard, AutonomyCard, ConsumptionRow, FieldBar, KpiRow, MissionRow, smoothPaths, VehicleCard } from "./components";
import { CRITICAL_NOTE, weatherView } from "./format";

const TZ = "America/Sao_Paulo";
const NOW = Date.parse("2026-11-20T15:00:00Z");   // 12:00 em São Paulo

/** Texto visível do HTML renderizado (sem os comentários que o React põe entre nós de texto). */
function text(el: ReactElement): string {
  const div = document.createElement("div");
  div.innerHTML = renderToString(el);
  return (div.textContent ?? "").replace(/\s+/g, " ");
}
const html = (el: ReactElement) => renderToString(el);

const series = Array.from({ length: 20 }, (_, i) => ({
  t_start: new Date(Date.parse("2026-11-20T09:20:00Z") + i * 600000).toISOString(), complete: i < 19, observed_h: 1 / 6,
  fuel_rate_lph: i === 15 ? null : 24 + (i % 5), battery_power_kw: null, ha_per_h: i === 12 ? null : 20 + i * 0.3,
}));

/** Corpo de /status no formato do contrato, com os valores da imagem de referência (só para o teste). */
function fixture(over: Record<string, any> = {}): any {
  return {
    operation: { operation_id: "OP-1", name: "Preparo de solo — Talhão 03", operation_type: "soil_preparation", operation_label: "Preparo de solo", machine_id: "TR-320",
      field_id: "T03", property_id: "P1", start: "2026-11-20T09:20:00Z", end: null, status: "active", status_label: "Em andamento", origin: "real" },
    mode: "real", mode_label: "DADOS REAIS",
    machine: { machine_id: "TR-320", name: "Trator TR-320", kind: "tractor", kind_label: "Trator", energy_source: "diesel", energy_label: "Diesel", fuel_tank_capacity_l: 200 },
    field: { field_id: "T03", name: "Talhão 03", area_ha: 120, has_geometry: true },
    connection: { state: "online", label: "Online", age_s: 4, last_ts: "2026-11-20T14:59:56Z" },
    snapshot: { ts: "2026-11-20T14:59:56Z", state: "working", lat: -16.7, lon: -47.6, speed_ms: 7.5 / 3.6, engine_rpm: 1950, engine_load_pct: 78, fuel_level_pct: 62,
      fuel_rate_lph: 25, battery_soc_pct: null, battery_energy_kwh: null, heading_deg: 55 },
    mission: { planned_ha: 120, worked_ha: 76.4, remaining_ha: 43.6, progress_pct: 63.67, elapsed_h: 2.6, working_h: 2.5, rate_ha_h: 24.3, remaining_h: 3 + 5 / 60,
      remaining_h_range: [2.8, 3.4], eta: "2026-11-20T18:42:00Z", eta_range: ["2026-11-20T18:20:00Z", "2026-11-20T19:05:00Z"], eta_reasons: [], basis: "Extrapolação." },
    autonomy: { status: "green", status_label: "Viável", reasons: [], autonomy_h: 4.9, remaining_h: 3.1, level_pct: 62, level_amount: 124, unit: "L", margin_pct: 40,
      energy_source: "diesel", basis: "Extrapolação da taxa recente." },
    autonomy_unavailable: null,
    consumption: { source: "fuel_rate_lph", unit: "L/h", avg: 25.4, observed_h: 2.8, basis: "Média da operação, ponderada.", reason: null },
    series,
    alerts: [
      { id: "a1", machine_id: "TR-320", operation_id: "OP-1", type: "maintenance_due", severity: "warning", status: "active", message: "Manutenção preventiva em 12 h de operação.", start: "2026-11-20T12:00:00Z" },
      { id: "a2", machine_id: "TR-320", operation_id: "OP-1", type: "energy_trend", severity: "info", status: "active", message: "Nível de combustível em queda. Acompanhar.", start: "2026-11-20T11:00:00Z" },
      { id: "a3", machine_id: "TR-320", operation_id: "OP-1", type: "x", severity: "warning", status: "resolved", message: "Resolvido.", start: "2026-11-20T10:00:00Z" },
    ],
    evaluated_at: "2026-11-20T15:00:00Z",
    ...over,
  };
}

afterEach(() => { vi.useRealTimers(); });

describe("cartões com o corpo completo de /status", () => {
  it("veículo: nome, operação, estado e cronômetro ao vivo", () => {
    vi.useFakeTimers(); vi.setSystemTime(Date.parse("2026-11-20T11:57:15Z"));
    const t = text(<VehicleCard status={fixture()} ops={[]} selectedId="OP-1" onSelect={() => {}} tz={TZ} />);
    expect(t).toContain("Trator TR-320");
    expect(t).toContain("Operação: Preparo de solo");
    expect(t).toContain("Em operação");
    expect(t).toContain("Tempo de operação");
    expect(t).toContain("02:37:15");
    expect(t).not.toContain("MODO SIMULAÇÃO");
    expect(html(<VehicleCard status={fixture()} ops={[]} selectedId="OP-1" onSelect={() => {}} tz={TZ} />)).toContain('aria-label="Conexão: Online"');
  });
  it("veículo simulado sem conexão: pílula, cronômetro congelado e seletor de operação", () => {
    const st = fixture({ mode: "simulated", mode_label: "MODO SIMULAÇÃO", connection: { state: "offline", label: "Sem conexão", age_s: 2965, last_ts: "2026-11-20T12:20:00Z" } });
    const ops = [{ operation_id: "OP-1", machine_id: "TR-320", name: "Preparo" }, { operation_id: "OP-2", machine_id: "PE-07", name: "Pulverização" }];
    const t = text(<VehicleCard status={st} ops={ops} selectedId="OP-1" onSelect={() => {}} tz={TZ} />);
    expect(t).toContain("MODO SIMULAÇÃO");
    expect(t).toContain("Último estado: Em operação · Sem conexão há 49 min");
    expect(t).toContain("03:00:00");
    expect(t).toContain("até o último dado");
    expect(t).toContain("Pulverização");   // rótulo curto (nome da operação); máquina fica no title
  });
  it("KPIs da referência", () => {
    const t = text(<KpiRow status={fixture()} tz={TZ} />);
    expect(t).toContain("Velocidade7,5km/h");
    expect(t).toContain("Rotação (RPM)1.950");
    expect(t).toContain("Carga do motor78%");
    expect(t).toContain("Combustível62%(124 L)");
  });
  it("missão e previsão de conclusão", () => {
    const t = text(<MissionRow status={fixture()} tz={TZ} now={NOW} />);
    expect(t).toContain("Área trabalhada: 76,4 ha de 120,0 ha");
    expect(t).toContain("64 %");
    expect(t).toContain("Hoje, 15:42");
    expect(t).toContain("(em 03h 05min)");
    expect(t).toContain("faixa 15:20–16:05");
    expect(html(<MissionRow status={fixture()} tz={TZ} now={NOW} />)).toContain('aria-valuenow="63.67"');
  });
  it("autonomia suficiente", () => {
    const t = text(<AutonomyCard status={fixture()} />);
    expect(t).toContain("Autonomia estimada4,9h");
    expect(t).toContain("Tempo restante da missão3,1h");
    expect(t).toContain("Autonomia suficiente");
    expect(t).toContain("Operação deve ser concluída conforme planejado");
    expect(t).not.toContain(CRITICAL_NOTE);
  });
  it("consumo e produtividade com gráficos", () => {
    const h = html(<ConsumptionRow status={fixture()} />), t = text(<ConsumptionRow status={fixture()} />);
    expect(t).toContain("Consumo de combustível25,4L/h");
    expect(t).toContain("Produtividade24,3ha/h");
    expect(t.match(/Média da operação/g)).toHaveLength(2);
    expect(h).toContain("opd-spark");
    expect((h.match(/opd-bar-full/g) ?? []).length).toBe(8);      // 10 barras: 1 lacuna, 1 incompleta
    expect((h.match(/opd-bar-partial/g) ?? []).length).toBe(1);
    expect((h.match(/opd-bar-gap/g) ?? []).length).toBe(1);
    expect(h).toContain("últimos 18 intervalos");
  });
  it("alertas ativos, com chevron e sem o resolvido", () => {
    const t = text(<AlertsCard status={fixture()} onOpen={() => {}} />);
    expect(t).toContain("Alertas (2)");
    expect(t).toContain("Manutenção preventiva em 12 h de operação.");
    expect(t).toContain("Nível de combustível em queda. Acompanhar.");
    expect(t).not.toContain("Resolvido.");
    expect((html(<AlertsCard status={fixture()} onOpen={() => {}} />).match(/<button/g) ?? []).length).toBe(2);
  });
  it("barra do talhão com clima observado", () => {
    const wx = weatherView({ status: "ok", attribution: "Fonte: INMET",
      observation: { provider_label: "INMET", kind: "observation", station_name: "BRASILIA", temperature_c: 28.2, wind_speed_kmh: 12, humidity_pct: 58 },
      forecast: { provider_label: "INMET", summary: "Parcialmente nublado", temp_min_c: 21, temp_max_c: 29 } }, null, TZ);
    const t = text(<FieldBar status={fixture()} weather={wx} />);
    expect(t).toContain("Talhão 03 – 120,0 ha");
    expect(t).toContain("Progresso: 64 %");
    expect(t).toContain("28°CParcialmente nublado");
    expect(t).toContain("12km/hVento");
    expect(t).toContain("58%Umidade");
    expect(t).toContain("Fonte: INMET");
  });
});

describe("ausências viram travessão com motivo, nunca 0", () => {
  it("KPIs sem leitura", () => {
    const st = fixture({ snapshot: { ts: "2026-11-20T14:59:56Z", state: "working", speed_ms: null, engine_rpm: null, engine_load_pct: null, fuel_level_pct: null } });
    const t = text(<KpiRow status={st} tz={TZ} />);
    expect(t).toContain("Velocidade—não reportado");
    expect(t).toContain("Rotação (RPM)—não reportado");
    expect(t).toContain("Carga do motor—não reportado");
    expect(t).toContain("Combustível—não reportado");
    expect(t).not.toMatch(/\b0 ?(km\/h|%)/);
    expect(html(<KpiRow status={st} tz={TZ} />)).toContain("não reportado pela máquina");
  });
  it("máquina elétrica: Bateria com kWh; RPM e carga não reportados", () => {
    const st = fixture({ machine: { name: "Pulverizador PE-07", energy_source: "electric" },
      snapshot: { ts: "2026-11-20T14:59:56Z", state: "working", speed_ms: 2, engine_rpm: null, engine_load_pct: null, battery_soc_pct: 19.5, battery_energy_kwh: 11.7 },
      autonomy: { status: "red", status_label: "Insuficiente", reasons: [], autonomy_h: 0.84, unit: "kWh", level_amount: 11.7, margin_pct: -71.7, energy_source: "electric" },
      consumption: { source: "battery_power_kw", unit: "kW", avg: 6.8, basis: "b", reason: null },
      series: series.map((b) => ({ ...b, fuel_rate_lph: null, battery_power_kw: 6 + (b.complete ? 1 : 0) })) });
    const k = text(<KpiRow status={st} tz={TZ} />);
    expect(k).toContain("Bateria20%(11,7 kWh)");
    expect(k).toContain("Rotação (RPM)—não reportado");
    const a = text(<AutonomyCard status={st} />);
    expect(a).toContain("Autonomia insuficiente");
    expect(a).toContain(CRITICAL_NOTE);
    expect(text(<ConsumptionRow status={st} />)).toContain("Consumo de energia6,8kW");
  });
  it("ETA indeterminada e autonomia cinza mostram o primeiro motivo", () => {
    const st = fixture({ mission: { ...fixture().mission, eta: null, eta_range: null, remaining_h: null,
      eta_reasons: [{ code: "stale_data", message: "Último dado há 49 min; ritmo atual desconhecido." }] },
    autonomy: { status: "gray", status_label: "Indeterminado", autonomy_h: 12.47, reasons: [{ code: "remaining_unknown", message: "Tempo restante da missão indeterminado." }] } });
    const m = text(<MissionRow status={st} tz={TZ} now={NOW} />);
    expect(m).toContain("Indeterminada");
    expect(m).toContain("Último dado há 49 min; ritmo atual desconhecido.");
    const a = text(<AutonomyCard status={st} />);
    expect(a).toContain("Autonomia estimada12,5h");
    expect(a).toContain("Tempo restante da missão—");
    expect(a).toContain("IndeterminadoTempo restante da missão indeterminado.");
  });
  it("autonomia não calculada", () => {
    const t = text(<AutonomyCard status={fixture({ autonomy: null, autonomy_unavailable: "Máquina sem cadastro da mesma origem: autonomia não calculada." })} />);
    expect(t).toContain("Autonomia estimada—");
    expect(t).toContain("Máquina sem cadastro da mesma origem");
  });
  it("backend antigo sem consumption: indisponível", () => {
    const st = fixture({ consumption: undefined });
    const t = text(<ConsumptionRow status={st} />);
    expect(t).toContain("Consumo de combustível—");
    expect(t).toContain("indisponível");
    expect(text(<ConsumptionRow status={fixture({ consumption: { source: null, unit: null, avg: null, reason: "Máquina não reporta consumo." } })} />)).toContain("Máquina não reporta consumo.");
  });
  it("sem série: gráfico vazio explícito", () => {
    expect(text(<ConsumptionRow status={fixture({ series: [] })} />)).toContain("sem série");
  });
  it("sem alertas e sem clima", () => {
    expect(text(<AlertsCard status={fixture({ alerts: [] })} onOpen={() => {}} />)).toContain("Alertas (0)Nenhum alerta ativo");
    const wx = weatherView(null, "Serviço de clima não disponível neste servidor.", TZ);
    const t = text(<FieldBar status={fixture()} weather={wx} />);
    expect(t).toContain("Sem dados meteorológicos");
    expect(t).toContain("Serviço de clima não disponível neste servidor.");
    expect(t).not.toContain("°C");
  });
  it("alerta crítico traz a nota", () => {
    const st = fixture({ alerts: [{ id: "c", operation_id: "OP-1", severity: "critical", status: "active", message: "Risco de não concluir." }] });
    expect(text(<AlertsCard status={st} onOpen={() => {}} />)).toContain(CRITICAL_NOTE);
  });
  it("status ainda não carregado não quebra", () => {
    expect(text(<KpiRow status={null} tz={TZ} />)).toContain("Velocidade—");
    expect(text(<FieldBar status={null} weather={weatherView(null, null, TZ)} />)).toContain("Progresso: —");
  });
});

describe("curva suave", () => {
  it("lacunas quebram o traço; ponto isolado vira círculo", () => {
    const r = smoothPaths([1, 2, null, 3, null, 2, 4], 100, 40);
    expect((r.line.match(/M/g) ?? []).length).toBe(2);
    expect(r.dots).toHaveLength(1);
    expect(smoothPaths([], 100, 40)).toEqual({ line: "", area: "", dots: [] });
  });
  it("não ultrapassa o intervalo dos dados (monotônica)", () => {
    const r = smoothPaths([10, 10, 30, 30, 10], 100, 40, 3);
    const ys = [...r.line.matchAll(/-?\d+\.\d/g)].map((m) => Number(m[0])).filter((_, i) => i % 2 === 1);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(3 - 0.05);
    expect(Math.max(...ys)).toBeLessThanOrEqual(40);
  });
});
