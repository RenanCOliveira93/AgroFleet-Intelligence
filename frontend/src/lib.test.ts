import { describe, expect, it } from "vitest";
import { applyTelemetry, fmt, hoursHM, positionAt, provenanceBanner, sparkPath, trailUntil, type MachineRow, type Series } from "./lib";

const S: Series = { t: [0, 5, 10, 15, 20], lon: [-47.6, -47.601, null, null, -47.605], lat: [-16.7, -16.701, null, null, -16.705],
  state: ["working", "working", "working", "working", "transport"], speed_ms: [5, 5, null, null, 6], origin: Array(5).fill("simulated") };

describe("fmt", () => {
  it("ausente não vira zero", () => { expect(fmt(null)).toBe("—"); expect(fmt(undefined)).toBe("—"); expect(fmt(NaN)).toBe("—"); expect(fmt(0, 1)).toBe("0,0"); });
  it("horas", () => { expect(hoursHM(1.5)).toBe("1 h 30 min"); expect(hoursHM(null)).toBe("—"); });
});

describe("positionAt", () => {
  it("interpola entre pontos válidos", () => {
    const p = positionAt(S, 2.5)!;
    expect(p.interpolated).toBe(true);
    expect(p.lon).toBeCloseTo(-47.6005, 6);
  });
  it("não extrapola nem inventa posição na perda de GNSS", () => {
    expect(positionAt(S, -1)).toBeNull();
    expect(positionAt(S, 21)).toBeNull();
    expect(positionAt(S, 11)).toBeNull();     // sem GNSS
  });
  it("não interpola sobre lacuna maior que o limite", () => {
    const g: Series = { ...S, t: [0, 100], lon: [1, 2], lat: [1, 2], state: ["a", "a"], speed_ms: [1, 1], origin: ["real", "real"] };
    const p = positionAt(g, 50)!;
    expect(p.lon).toBe(1);
    expect(p.interpolated).toBe(false);
  });
});

describe("trailUntil", () => {
  it("quebra a trilha onde falta posição", () => {
    const tr = trailUntil(S, 20);
    expect(tr).toHaveLength(1);                // [0,5] vira linha; o ponto 20 sozinho não forma linha
    expect(trailUntil({ ...S, lon: [1, 2, null, 3, 4], lat: [1, 2, null, 3, 4] }, 20)).toHaveLength(2);
  });
});

describe("provenanceBanner", () => {
  it("só simulado", () => expect(provenanceBanner({ simulated: { samples: 10 } }).text).toContain("SIMULADOS"));
  it("misturado", () => expect(provenanceBanner({ simulated: { samples: 1 }, real: { samples: 2 } }).level).toBe("mixed"));
  it("vazio e desconhecido", () => {
    expect(provenanceBanner({}).level).toBe("empty");
    expect(provenanceBanner({ unknown: { samples: 3 } }).level).toBe("unknown");
  });
});

describe("applyTelemetry", () => {
  const rows: MachineRow[] = [{ machine_id: "A", origin: "simulated", state: "idle", lat: 1, lon: 1, last_ts: "x", live: false, alerts: 2, speed_ms: 0, flow_lpm: 0,
    pressure_bar: 0, tank_level_l: 0, gnss_fix: "rtk_fixed", samples: 5, field_id: null, operator_id: null }];
  it("atualiza a existente e adiciona nova sem misturar origens", () => {
    const out = applyTelemetry(rows, [{ machine_id: "A", origin: "simulated", state: "working", lat: null, lon: null, ts: "y" } as never, { machine_id: "A", origin: "real", state: "idle" } as never]);
    expect(out).toHaveLength(2);
    expect(out[0].state).toBe("working");
    expect(out[0].lat).toBeNull();          // perda de GNSS aparece como ausência, não como a última posição
    expect(out[0].alerts).toBe(2);
    expect(rows[0].state).toBe("idle");      // imutável
  });
});

describe("sparkPath", () => {
  it("interrompe o traço em nulos", () => { expect(sparkPath([1, 2, null, 3, 4]).match(/M/g)).toHaveLength(2); expect(sparkPath([1])).toBe(""); });
});
