import { describe, expect, it } from "vitest";
import {
  activeAlerts, age, autonomyView, clock, CRITICAL_NOTE, DASH, dayLabel, etaLabel, etaRangeLabel, hm, hms, hoursText, inHM, int, isElectric, kmh,
  lastBuckets, machineLine, newerStatus, num, operationClock, pickOperation, range, shortDateTime, weatherView,
} from "./format";

const TZ = "America/Sao_Paulo";
const NOW = Date.parse("2026-11-20T15:00:00Z");   // 12:00 em São Paulo

describe("números pt-BR", () => {
  it("formata casas e milhar; ausente vira travessão, nunca 0", () => {
    expect(num(25.4)).toBe("25,4");
    expect(num(25.44, 1)).toBe("25,4");
    expect(num(4.95, 1)).toBe("5,0");
    expect(int(1950)).toBe("1.950");
    expect(int(1933.4)).toBe("1.933");
    expect(int(12345678)).toBe("12.345.678");
    expect(num(0)).toBe("0,0");
    expect(num(null)).toBe(DASH);
    expect(num(undefined)).toBe(DASH);
    expect(num(Number.NaN)).toBe(DASH);
    expect(num(Number.POSITIVE_INFINITY)).toBe(DASH);
  });
  it("não mostra -0", () => {
    expect(num(-0.04, 1)).toBe("0,0");
    expect(num(-0.4, 0)).toBe("0");
    expect(num(-1.25, 1)).toBe("-1,3");
  });
  it("m/s → km/h", () => {
    expect(num(kmh(2.0833333), 1)).toBe("7,5");
    expect(kmh(null)).toBeNull();
    expect(kmh(0)).toBe(0);
  });
  it("faixas", () => {
    expect(range(21, 29)).toBe("21–29");
    expect(range(21, 21)).toBe("21");
    expect(range(null, 29)).toBe("29");
    expect(range(40, null)).toBe("40");
    expect(range(null, undefined)).toBe(DASH);
  });
});

describe("durações", () => {
  it("HH:MM:SS", () => {
    expect(hms(2 * 3600 + 37 * 60 + 15)).toBe("02:37:15");
    expect(hms(9435.9)).toBe("02:37:15");
    expect(hms(0)).toBe("00:00:00");
    expect(hms(-5)).toBe("00:00:00");
    expect(hms(100 * 3600)).toBe("100:00:00");
    expect(hms(null)).toBe(DASH);
  });
  it("03h 05min e (em …)", () => {
    expect(hm(3 + 5 / 60)).toBe("03h 05min");
    expect(hm(3.0999)).toBe("03h 06min");
    expect(hm(0.5)).toBe("00h 30min");
    expect(hm(27.25)).toBe("27h 15min");
    expect(hm(null)).toBe(DASH);
    expect(inHM(3 + 5 / 60)).toBe("(em 03h 05min)");
    expect(inHM(null)).toBe("");
  });
  it("horas com vírgula", () => {
    expect(hoursText(4.9)).toBe("4,9 h");
    expect(hoursText(12.46993)).toBe("12,5 h");
    expect(hoursText(null)).toBe(DASH);
  });
  it("idade do dado", () => {
    expect(age(45)).toBe("45 s");
    expect(age(2965.1)).toBe("49 min");
    expect(age(2 * 3600 + 5 * 60)).toBe("2 h 05 min");
    expect(age(3 * 86400 + 10)).toBe("3 d");
    expect(age(null)).toBe(DASH);
  });
});

describe("ETA no fuso da propriedade", () => {
  it("Hoje / Amanhã / dd/mm", () => {
    expect(etaLabel("2026-11-20T18:42:00Z", TZ, NOW)).toBe("Hoje, 15:42");
    expect(etaLabel("2026-11-21T11:10:00Z", TZ, NOW)).toBe("Amanhã, 08:10");
    expect(etaLabel("2026-11-22T12:00:00Z", TZ, NOW)).toBe("22/11, 09:00");
    expect(etaLabel("2026-11-19T12:00:00Z", TZ, NOW)).toBe("19/11, 09:00");
  });
  it("usa o dia do fuso, não o UTC", () => {
    const late = Date.parse("2026-11-21T02:30:00Z");                         // 23:30 do dia 20 em São Paulo
    expect(etaLabel("2026-11-21T02:50:00Z", TZ, late)).toBe("Hoje, 23:50");
    expect(etaLabel("2026-11-21T03:10:00Z", TZ, late)).toBe("Amanhã, 00:10");
    expect(etaLabel("2026-11-21T03:10:00Z", "UTC", late)).toBe("Hoje, 03:10");
    expect(etaLabel("2026-11-20T23:30:00Z", "Asia/Tokyo", NOW)).toBe("Hoje, 08:30");           // 15:00Z já é dia 21 em Tóquio
    expect(etaLabel("2026-11-21T23:30:00Z", "Asia/Tokyo", NOW)).toBe("Amanhã, 08:30");
  });
  it("virada de ano e mês", () => {
    const nye = Date.parse("2026-12-31T20:00:00Z");
    expect(etaLabel("2027-01-01T12:00:00Z", TZ, nye)).toBe("Amanhã, 09:00");
    expect(dayLabel("2027-01-02T12:00:00Z", TZ, nye)).toBe("02/01");
    expect(etaLabel("2026-12-01T12:00:00Z", TZ, Date.parse("2026-11-30T15:00:00Z"))).toBe("Amanhã, 09:00");
  });
  it("ausente, inválida e fuso inválido", () => {
    expect(etaLabel(null, TZ, NOW)).toBe(DASH);
    expect(etaLabel("não é data", TZ, NOW)).toBe(DASH);
    expect(etaLabel("2026-11-20T18:42:00Z", "Fuso/Inexistente", NOW)).toBe("Hoje, 15:42");
    expect(clock("2026-11-20T03:05:00Z", TZ)).toBe("00:05");
    expect(shortDateTime("2026-11-20T15:00:00Z", TZ)).toBe("20/11 12:00");
  });
  it("faixa compacta", () => {
    expect(etaRangeLabel(["2026-11-20T18:20:00Z", "2026-11-20T19:05:00Z"], TZ, NOW)).toBe("15:20–16:05");
    expect(etaRangeLabel(["2026-11-21T11:10:00Z", "2026-11-21T12:30:00Z"], TZ, NOW)).toBe("Amanhã 08:10–09:30");
    expect(etaRangeLabel(["2026-11-21T01:10:00Z", "2026-11-21T04:30:00Z"], TZ, NOW)).toBe("Hoje, 22:10 – Amanhã, 01:30");
    expect(etaRangeLabel(null, TZ, NOW)).toBe("");
    expect(etaRangeLabel([null, "2026-11-21T04:30:00Z"], TZ, NOW)).toBe("");
  });
});

describe("tempo de operação", () => {
  const op = { start: "2026-11-20T10:29:00Z", end: null };
  it("online: conta até agora", () => {
    const c = operationClock(op, { state: "online", last_ts: "2026-11-20T13:29:00Z" }, Date.parse("2026-11-20T13:06:15Z"));
    expect(hms(c.seconds)).toBe("02:37:15");
    expect(c.live).toBe(true);
    expect(c.note).toBe("");
  });
  it("sem conexão: congela no último dado", () => {
    const c = operationClock(op, { state: "offline", last_ts: "2026-11-20T13:29:00+00:00" }, NOW);
    expect(hms(c.seconds)).toBe("03:00:00");
    expect(c.live).toBe(false);
    expect(c.note).toBe("até o último dado");
    expect(operationClock(op, { state: "delayed", last_ts: "2026-11-20T13:29:00Z" }, NOW).live).toBe(false);
  });
  it("encerrada, sem início, sem dados", () => {
    expect(hms(operationClock({ start: op.start, end: "2026-11-20T12:00:00Z" }, { state: "online" }, NOW).seconds)).toBe("01:31:00");
    expect(operationClock({ start: null }, { state: "online" }, NOW).seconds).toBeNull();
    expect(operationClock(op, { state: "unknown", last_ts: null }, NOW)).toEqual({ seconds: null, live: false, note: "sem dados da máquina" });
  });
});

describe("linha de estado da máquina", () => {
  const base = { snapshot: { state: "working", ts: "2026-11-20T13:29:00Z" } };
  it("online + trabalhando = Em operação", () => {
    expect(machineLine({ ...base, connection: { state: "online", label: "Online", age_s: 3 } }, TZ)).toMatchObject({ text: "Em operação", tone: "ok" });
  });
  it("sem conexão mostra último estado, rótulo e idade", () => {
    const l = machineLine({ ...base, connection: { state: "offline", label: "Sem conexão", age_s: 2965, last_ts: "2026-11-20T13:29:00Z" } }, TZ);
    expect(l.text).toBe("Último estado: Em operação · Sem conexão há 49 min");
    expect(l.tone).toBe("danger");
    expect(l.title).toBe("Último dado: 20/11 10:29 (há 49 min)");
  });
  it("atrasado e sem snapshot", () => {
    expect(machineLine({ ...base, connection: { state: "delayed", label: "Atrasada", age_s: 75 } }).text).toBe("Em operação · Atrasada (dado de há 1 min)");
    expect(machineLine({ connection: { state: "unknown", label: "Desconhecida" } }).text).toBe("Sem dados da máquina · Desconhecida");
  });
});

describe("escolha da operação", () => {
  const ops = [
    { operation_id: "A", property_id: "P1", origin: "simulated", status: "active", start: "2026-11-20T10:00:00Z" },
    { operation_id: "B", property_id: "P1", origin: "real", status: "active", start: "2026-11-20T11:00:00Z" },
    { operation_id: "C", property_id: "P2", origin: "simulated", status: "active", start: "2026-11-20T12:00:00Z" },
    { operation_id: "D", property_id: "P1", origin: "simulated", status: "finished", start: "2026-11-20T13:00:00Z" },
  ];
  it("ativa mais recente da propriedade", () => {
    const r = pickOperation(ops, { propertyId: "P1" });
    expect(r.list.map((o) => o.operation_id)).toEqual(["B", "A"]);
    expect(r.selected?.operation_id).toBe("B");
  });
  it("salva > padrão; pedida > salva; salva inexistente é ignorada", () => {
    expect(pickOperation(ops, { propertyId: "P1", saved: "A" }).selected?.operation_id).toBe("A");
    expect(pickOperation(ops, { propertyId: "P1", saved: "A", requested: "B" }).selected?.operation_id).toBe("B");
    expect(pickOperation(ops, { propertyId: "P1", saved: "C" }).selected?.operation_id).toBe("B");
  });
  it("filtro de origem e todas as propriedades", () => {
    expect(pickOperation(ops, { propertyId: "P1", origin: "simulated" }).list.map((o) => o.operation_id)).toEqual(["A"]);
    expect(pickOperation(ops, { propertyId: null, origin: "all" }).list.map((o) => o.operation_id)).toEqual(["C", "B", "A"]);
  });
  it("sem ativas mostra as demais; sem nenhuma, null", () => {
    expect(pickOperation(ops.filter((o) => o.operation_id === "D"), { propertyId: "P1" }).selected?.operation_id).toBe("D");
    expect(pickOperation(ops, { propertyId: "P9" })).toEqual({ list: [], selected: null });
  });
});

describe("status mais novo, série e alertas", () => {
  it("compara evaluated_at", () => {
    const a = { evaluated_at: "2026-11-20T14:00:00Z" }, b = { evaluated_at: "2026-11-20T14:00:05Z" };
    expect(newerStatus(a, b)).toBe(b);
    expect(newerStatus(b, a)).toBe(b);
    expect(newerStatus(null, a)).toBe(a);
    expect(newerStatus(a, null)).toBe(a);
    expect(newerStatus(a, { evaluated_at: "2026-11-20T14:00:00Z" })).toBe(a);
  });
  it("últimos intervalos mantêm lacunas", () => {
    const s = [{ fuel_rate_lph: 30, complete: true }, { fuel_rate_lph: null, complete: true }, { fuel_rate_lph: 32 }, { fuel_rate_lph: 31, complete: false }];
    expect(lastBuckets(s, "fuel_rate_lph", 3)).toEqual({ values: [null, 32, 31], complete: [true, true, false], t: ["", "", ""] });
    expect(lastBuckets(undefined, "x", 5).values).toEqual([]);
  });
  it("só ativos desta operação/máquina; críticos primeiro", () => {
    const st = { operation: { operation_id: "OP", machine_id: "M" }, alerts: [
      { id: "1", severity: "info", status: "active", operation_id: "OP", start: "2026-11-20T10:00:00Z" },
      { id: "2", severity: "warning", status: "active", operation_id: "OP", start: "2026-11-20T11:00:00Z" },
      { id: "3", severity: "critical", status: "active", operation_id: "OP", start: "2026-11-20T09:00:00Z" },
      { id: "4", severity: "warning", status: "resolved", operation_id: "OP" },
      { id: "5", severity: "warning", status: "active", operation_id: "OUTRA" },
      { id: "6", severity: "warning", status: "active", machine_id: "M", start: "2026-11-20T08:00:00Z" },
      { id: "7", severity: "warning", status: "active", machine_id: "X" },
    ] };
    expect(activeAlerts(st).map((a) => a.id)).toEqual(["3", "2", "6", "1"]);
    expect(activeAlerts(null)).toEqual([]);
  });
  it("detecta elétrico", () => {
    expect(isElectric({ machine: { energy_source: "electric" } })).toBe(true);
    expect(isElectric({ consumption: { source: "battery_power_kw" } })).toBe(true);
    expect(isElectric({ machine: { energy_source: "diesel" } })).toBe(false);
  });
});

describe("autonomia (só formata o status do backend)", () => {
  it("verde", () => {
    const v = autonomyView({ autonomy: { status: "green", status_label: "Viável", reasons: [], margin_pct: 32.4 } });
    expect(v).toMatchObject({ tone: "green", title: "Autonomia suficiente", critical: false });
    expect(v.text).toBe("Operação deve ser concluída conforme planejado (margem de 32 %).");
  });
  it("verde por missão concluída usa o motivo do backend", () => {
    const v = autonomyView({ autonomy: { status: "green", reasons: [{ code: "mission_done", message: "Área planejada coberta; não há necessidade adicional de energia." }] } });
    expect(v.text).toMatch(/^Área planejada coberta/);
  });
  it("amarelo e vermelho (crítico)", () => {
    expect(autonomyView({ autonomy: { status: "yellow", reasons: [], margin_pct: 8, required_margin_pct: 15 } }).text).toBe("Concluir é possível, mas com margem de 8 %, abaixo dos 15 % exigidos.");
    const r = autonomyView({ autonomy: { status: "red", status_label: "Insuficiente", reasons: [], margin_pct: -71.7 } });
    expect(r).toMatchObject({ tone: "red", title: "Autonomia insuficiente", critical: true });
    expect(r.text).toContain("margem de -72 %");
    expect(CRITICAL_NOTE).toContain("não uma decisão automática de segurança");
  });
  it("cinza com o primeiro motivo; sem autonomia usa autonomy_unavailable", () => {
    expect(autonomyView({ autonomy: { status: "gray", reasons: [{ code: "remaining_unknown", message: "Tempo restante da missão indeterminado." }] } }))
      .toMatchObject({ tone: "gray", title: "Indeterminado", text: "Tempo restante da missão indeterminado." });
    expect(autonomyView({ autonomy: null, autonomy_unavailable: "Máquina sem cadastro da mesma origem: autonomia não calculada." }).text).toMatch(/^Máquina sem cadastro/);
    expect(autonomyView({ autonomy: { status: "???" } }).tone).toBe("gray");
  });
});

describe("clima (contrato A)", () => {
  const obs = { provider: "inmet", provider_label: "INMET", kind: "observation", station_code: "A001", station_name: "BRASILIA", station_uf: "DF", distance_km: 12.3,
    observed_at: "2026-11-20T15:00:00Z", age_s: 1800, stale: false, temperature_c: 27.9, humidity_pct: 58, wind_speed_kmh: 12.2, wind_gust_kmh: 20.1 };
  const fc = { provider: "inmet", provider_label: "INMET", kind: "forecast", municipality: "Cristalina", uf: "GO", date: "2026-11-20", period_label: "Tarde",
    summary: "Muitas nuvens com pancadas de chuva isoladas", temp_min_c: 21, temp_max_c: 29, humidity_min_pct: 40, humidity_max_pct: 90, wind_dir: "N-NW", wind_intensity: "Fracos" };
  const loc = { lat: -16.7, lon: -47.6, source: "field_centroid", fictitious: true, note: "Talhão simulado em local fictício: o clima mostrado é o real deste ponto, não do talhão." };

  it("observação primeiro; condição só da previsão", () => {
    const v = weatherView({ status: "ok", observation: obs, forecast: fc, attribution: "Fonte: INMET", location: loc }, null, TZ);
    expect(v.available).toBe(true);
    expect(v.temp).toMatchObject({ value: "28", unit: "°C", label: "Muitas nuvens com pancadas de chuva isoladas", source: "observation" });
    expect(v.temp!.title).toBe("Observação INMET · estação BRASILIA/DF (A001) · a 12,3 km · em 20/11 12:00");
    expect(v.wind).toMatchObject({ value: "12", unit: "km/h", label: "Vento" });
    expect(v.humidity).toMatchObject({ value: "58", unit: "%", label: "Umidade" });
    expect(v.attribution).toBe("Fonte: INMET");
    expect(v.note).toMatch(/local fictício/);
  });
  it("sem previsão: não inventa céu (mostra a estação)", () => {
    const v = weatherView({ status: "partial", observation: obs, forecast: null, attribution: "Fonte: INMET" }, null, TZ);
    expect(v.temp!.label).toBe("Estação BRASILIA");
    expect(v.temp!.label).not.toMatch(/nublado|sol/i);
  });
  it("só previsão: faixas marcadas como previsão", () => {
    const v = weatherView({ status: "partial", observation: null, forecast: fc, attribution: "Fonte: INMET" }, null, TZ);
    expect(v.temp).toMatchObject({ value: "21–29", unit: "°C", tag: "previsão", source: "forecast" });
    expect(v.wind).toMatchObject({ value: "Fracos N-NW", unit: "", tag: "previsão" });
    expect(v.humidity).toMatchObject({ value: "40–90", unit: "%", tag: "previsão" });
    expect(v.temp!.title).toBe("Previsão INMET · Cristalina/GO · Tarde · 20/11");
  });
  it("campo ausente na observação cai para a previsão; modelo é marcado", () => {
    const v = weatherView({ status: "ok", observation: { ...obs, kind: "model", humidity_pct: null }, forecast: fc, attribution: "Fonte: X" }, null, TZ);
    expect(v.temp!.tag).toBe("modelo");
    expect(v.temp!.title).toMatch(/^Estimativa de modelo/);
    expect(v.humidity).toMatchObject({ value: "40–90", tag: "previsão" });
  });
  it("tudo ausente vira travessão", () => {
    const v = weatherView({ status: "partial", observation: { ...obs, temperature_c: null, wind_speed_kmh: null, humidity_pct: null }, forecast: null }, null, TZ);
    expect([v.temp!.value, v.wind!.value, v.humidity!.value]).toEqual([DASH, DASH, DASH]);
  });
  it("indisponível: motivo do provedor ou do endpoint", () => {
    const u = weatherView({ status: "unavailable", message: "Sem dados meteorológicos", reasons: [{ provider: "inmet", code: "no_token", message: "Token INMET não configurado." }],
      observation: null, forecast: null, attribution: "Fonte: INMET" }, null, TZ);
    expect(u).toMatchObject({ available: false, reason: "Token INMET não configurado.", attribution: "Fonte: INMET" });
    expect(weatherView(null, "Serviço de clima não disponível neste servidor.", TZ)).toMatchObject({ available: false, reason: "Serviço de clima não disponível neste servidor." });
    expect(weatherView(null, null, TZ).reason).toBe("Sem dados meteorológicos");
  });
});
