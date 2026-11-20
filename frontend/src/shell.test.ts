import { describe, expect, it } from "vitest";
import { alertsForProperty, badgeText, DEFAULT_TZ, fmtDateTime, initials, propertyTimezone, resolveProperty } from "./shell";

const P = [{ property_id: "A", name: "Fazenda A", timezone: "America/Cuiaba" }, { property_id: "B", name: "Fazenda B", timezone: "America/Sao_Paulo" }];

describe("propriedade", () => {
  it("uma só propriedade é escolhida sozinha; várias respeitam a escolha salva", () => {
    expect(resolveProperty([P[0]], null)).toBe("A");
    expect(resolveProperty(P, "B")).toBe("B");
    expect(resolveProperty(P, "X")).toBeNull();
    expect(resolveProperty(P, null)).toBeNull();
    expect(resolveProperty([], "A")).toBeNull();
  });
  it("fuso da propriedade, comum ou padrão", () => {
    expect(propertyTimezone(P, "A")).toBe("America/Cuiaba");
    expect(propertyTimezone(P, null)).toBe(DEFAULT_TZ);
    expect(propertyTimezone([P[0]], null)).toBe("America/Cuiaba");
    expect(propertyTimezone([], null)).toBe(DEFAULT_TZ);
    expect(propertyTimezone([{ property_id: "C" }], "C")).toBe(DEFAULT_TZ);
  });
});

describe("alertas por propriedade", () => {
  const machines = [{ machine_id: "M1", property_id: "A" }, { machine_id: "M2", property_id: "B" }];
  const ops = [{ operation_id: "O1", machine_id: "M1", property_id: "A" }, { operation_id: "O2", machine_id: "M2", property_id: "B" }];
  const alerts = [
    { id: 1, machine_id: "M1", operation_id: "O1", status: "active" },
    { id: 2, machine_id: "M2", operation_id: "O2", status: "active" },
    { id: 3, machine_id: "M1", operation_id: null, status: "active" },
    { id: 4, machine_id: "SIM-01", operation_id: null, status: "active" },
    { id: 5, machine_id: "M1", operation_id: "O1", status: "resolved" },
  ];
  it("todas as propriedades: conta todos os ativos", () => expect(alertsForProperty(alerts, null, machines, ops).map((a) => a.id)).toEqual([1, 2, 3, 4]));
  it("propriedade A: pela operação ou pela máquina cadastrada", () => expect(alertsForProperty(alerts, "A", machines, ops).map((a) => a.id)).toEqual([1, 3]));
  it("badge", () => { expect(badgeText(0)).toBe(""); expect(badgeText(2)).toBe("2"); expect(badgeText(140)).toBe("99+"); });
});

describe("formatação", () => {
  it("data no fuso da propriedade; ausente vira travessão", () => {
    expect(fmtDateTime("2026-11-20T10:29:00Z", "America/Sao_Paulo")).toBe("20/11/2026 07:29");
    expect(fmtDateTime(null)).toBe("—");
    expect(fmtDateTime("lixo")).toBe("—");
  });
  it("iniciais", () => { expect(initials("admin")).toBe("AD"); expect(initials("joao.silva")).toBe("JS"); expect(initials("")).toBe("?"); });
});
