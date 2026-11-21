import { describe, expect, it } from "vitest";
import { apiErrorText, emptyMachineForm, formFromMachine, machineBody, num, telemetryTopic, validateMachine } from "./machineModel";

const base = () => ({ ...emptyMachineForm("PROP-1", "real"), machine_id: "TR-0451", name: "Trator 0451" });

describe("cadastro de máquina", () => {
  it("campo em branco vira null, nunca um padrão", () => {
    const b = machineBody(base());
    expect(b).toMatchObject({ machine_id: "TR-0451", property_id: "PROP-1", origin: "real", fuel_tank_capacity_l: null, battery_capacity_kwh: null,
      energy_warning_pct: null, maintenance_interval_h: null });
    expect(validateMachine(base())).toEqual({});
  });
  it("capacidade vai para o campo da fonte declarada e aceita vírgula", () => {
    expect(machineBody({ ...base(), capacity: "380,5" }).fuel_tank_capacity_l).toBe(380.5);
    const e = machineBody({ ...base(), energy_source: "electric", capacity: "120", maintenance_interval_h: "250", last_maintenance_engine_h: "10" });
    expect(e).toMatchObject({ battery_capacity_kwh: 120, fuel_tank_capacity_l: null, maintenance_interval_h: null, last_maintenance_engine_h: null });
    expect(machineBody({ ...base(), energy_source: "unknown", capacity: "300" }).fuel_tank_capacity_l).toBeNull();
    expect(num("")).toBeNull(); expect(num("abc")).toBeNaN(); expect(num(" 1 000 ")).toBe(1000);
  });
  it("identificador, nome e duplicado", () => {
    expect(validateMachine({ ...base(), machine_id: "" }).machine_id).toBeTruthy();
    expect(validateMachine({ ...base(), machine_id: "TR 1" }).machine_id).toMatch(/sem espaços/);
    expect(validateMachine({ ...base(), name: " " }).name).toBeTruthy();
    expect(validateMachine(base(), { existingIds: ["TR-0451"] }).machine_id).toMatch(/Já existe/);
    expect(validateMachine(base(), { existingIds: ["TR-0451"], editing: true }).machine_id).toBeUndefined();
  });
  it("mesmas regras do backend para limites e manutenção", () => {
    expect(validateMachine({ ...base(), energy_warning_pct: "20", energy_critical_pct: "25" }).energy_critical_pct).toMatch(/menor/);
    expect(validateMachine({ ...base(), energy_reserve_pct: "15", energy_critical_pct: "10" }).energy_critical_pct).toMatch(/reserva/);
    expect(validateMachine({ ...base(), energy_reserve_pct: "60" }).energy_reserve_pct).toMatch(/Máximo 50/);
    expect(validateMachine({ ...base(), capacity: "0" }).capacity).toMatch(/maior que 0/);
    expect(validateMachine({ ...base(), capacity: "x" }).capacity).toMatch(/inválido/);
    expect(validateMachine({ ...base(), maintenance_interval_h: "250" }).last_maintenance_engine_h).toMatch(/intervalo e o horímetro/);
    expect(validateMachine({ ...base(), maintenance_interval_h: "250", last_maintenance_engine_h: "1200" })).toEqual({});
  });
  it("origem da propriedade precisa bater", () => {
    expect(validateMachine(base(), { propertyOrigin: "simulated" }).property_id).toMatch(/não se misturam/);
    expect(validateMachine({ ...base(), property_id: "" }, { propertyOrigin: "simulated" }).property_id).toBeUndefined();
  });
  it("edição preenche a partir do cadastro e volta ao mesmo corpo", () => {
    const m = { machine_id: "SP-1", name: "Pulv", kind: "sprayer", energy_source: "electric", autonomous: true, property_id: null, origin: "real",
      battery_capacity_kwh: 150, energy_reserve_pct: 10, energy_warning_pct: 25, energy_critical_pct: 12, autonomy_margin_pct: null };
    const f = formFromMachine(m);
    expect(f.capacity).toBe("150");
    expect(machineBody(f)).toMatchObject({ battery_capacity_kwh: 150, energy_critical_pct: 12, autonomy_margin_pct: null, property_id: null, autonomous: true });
  });
  it("mensagens da API e tópico MQTT", () => {
    expect(apiErrorText([{ loc: "body.energy_critical_pct", msg: "x" }])).toBe("energy_critical_pct: x");
    expect(apiErrorText("máquina e propriedade com origens diferentes")).toMatch(/origens/);
    expect(telemetryTopic("TR-0451")).toBe("agrofleet/v1/TR-0451/telemetry");
  });
});
