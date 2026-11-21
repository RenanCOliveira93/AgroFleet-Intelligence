/** Formulário de cadastro de máquina (Etapa 10): estado do formulário ⇄ corpo de PUT /registry/machines/{id}.
 * Campo em branco vira null (nunca um valor padrão escondido). As regras espelham agrofleet/registry.py para avisar
 * antes de enviar; o backend continua sendo quem decide (e as mensagens dele são mostradas). */

export type Kind = "tractor" | "sprayer" | "harvester" | "other";
export type Energy = "diesel" | "electric" | "unknown";

export interface MachineFormState {
  machine_id: string; name: string; kind: Kind; energy_source: Energy; autonomous: boolean;
  property_id: string; origin: "real" | "simulated" | "unknown";
  capacity: string; energy_reserve_pct: string; energy_warning_pct: string; energy_critical_pct: string;
  autonomy_margin_pct: string; maintenance_interval_h: string; last_maintenance_engine_h: string;
}

export const KIND_OPTIONS: [Kind, string][] = [["tractor", "Trator"], ["sprayer", "Pulverizador"], ["harvester", "Colheitadeira"], ["other", "Outro"]];
export const ENERGY_OPTIONS: [Energy, string][] = [["diesel", "Diesel"], ["electric", "Elétrico (bateria)"], ["unknown", "Não informado"]];
export const ORIGIN_OPTIONS: [MachineFormState["origin"], string][] = [["real", "Real (máquina física)"], ["simulated", "Simulada"], ["unknown", "Não declarada"]];
export const ID_RE = /^[A-Za-z0-9_.-]{1,64}$/;

export function emptyMachineForm(property_id = "", origin: MachineFormState["origin"] = "real"): MachineFormState {
  return { machine_id: "", name: "", kind: "tractor", energy_source: "diesel", autonomous: false, property_id, origin,
    capacity: "", energy_reserve_pct: "", energy_warning_pct: "", energy_critical_pct: "", autonomy_margin_pct: "",
    maintenance_interval_h: "", last_maintenance_engine_h: "" };
}

const s = (v: unknown) => (v === null || v === undefined ? "" : String(v));

export function formFromMachine(m: any): MachineFormState {
  return { machine_id: s(m.machine_id), name: s(m.name), kind: m.kind ?? "other", energy_source: m.energy_source ?? "unknown",
    autonomous: !!m.autonomous, property_id: s(m.property_id), origin: m.origin ?? "unknown",
    capacity: s(m.energy_source === "electric" ? m.battery_capacity_kwh : m.fuel_tank_capacity_l),
    energy_reserve_pct: s(m.energy_reserve_pct), energy_warning_pct: s(m.energy_warning_pct), energy_critical_pct: s(m.energy_critical_pct),
    autonomy_margin_pct: s(m.autonomy_margin_pct), maintenance_interval_h: s(m.maintenance_interval_h),
    last_maintenance_engine_h: s(m.last_maintenance_engine_h) };
}

/** Número digitado (aceita vírgula decimal); "" → null; texto inválido → NaN (a validação aponta). */
export function num(v: string): number | null {
  const t = v.trim().replace(/\s/g, "").replace(",", ".");
  if (!t) return null;
  return /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : NaN;
}

export function capacityUnit(e: Energy): string | null {
  return e === "diesel" ? "L" : e === "electric" ? "kWh" : null;
}

export function machineBody(f: MachineFormState): Record<string, unknown> {
  const cap = f.energy_source === "unknown" ? null : num(f.capacity);
  const diesel = f.energy_source === "diesel";
  return {
    machine_id: f.machine_id.trim(), name: f.name.trim(), kind: f.kind, energy_source: f.energy_source, autonomous: f.autonomous,
    property_id: f.property_id || null, origin: f.origin,
    fuel_tank_capacity_l: diesel ? cap : null,
    battery_capacity_kwh: f.energy_source === "electric" ? cap : null,
    energy_reserve_pct: num(f.energy_reserve_pct), energy_warning_pct: num(f.energy_warning_pct),
    energy_critical_pct: num(f.energy_critical_pct), autonomy_margin_pct: num(f.autonomy_margin_pct),
    // horímetro de motor não se aplica a elétrica
    maintenance_interval_h: diesel || f.energy_source === "unknown" ? num(f.maintenance_interval_h) : null,
    last_maintenance_engine_h: diesel || f.energy_source === "unknown" ? num(f.last_maintenance_engine_h) : null,
  };
}

type Errors = Partial<Record<keyof MachineFormState | "form", string>>;

export function validateMachine(f: MachineFormState, opts: { existingIds?: string[]; editing?: boolean; propertyOrigin?: string | null } = {}): Errors {
  const e: Errors = {};
  const id = f.machine_id.trim();
  if (!id) e.machine_id = "Informe o identificador.";
  else if (!ID_RE.test(id)) e.machine_id = "Use só letras, números, ponto, hífen ou sublinhado (até 64), sem espaços.";
  else if (!opts.editing && opts.existingIds?.includes(id)) e.machine_id = "Já existe uma máquina com este identificador. Use Editar na lista.";
  if (!f.name.trim()) e.name = "Informe o nome.";
  if (opts.propertyOrigin && f.property_id && opts.propertyOrigin !== f.origin)
    e.property_id = "A propriedade tem outra origem: dado real e simulado não se misturam.";
  const b = machineBody(f);
  const range = (k: keyof MachineFormState, v: unknown, lo: number, hi: number, gt = false) => {
    if (v === null) return;
    if (typeof v !== "number" || Number.isNaN(v)) { e[k] = "Número inválido."; return; }
    if (gt ? v <= lo : v < lo) e[k] = gt ? `Deve ser maior que ${lo}.` : `Mínimo ${lo}.`;
    else if (v > hi) e[k] = `Máximo ${hi}.`;
  };
  const cap = f.energy_source === "diesel" ? b.fuel_tank_capacity_l : b.battery_capacity_kwh;
  range("capacity", cap, 0, f.energy_source === "diesel" ? 5000 : 2000, true);
  range("energy_reserve_pct", b.energy_reserve_pct, 0, 50);
  range("energy_warning_pct", b.energy_warning_pct, 0, 100);
  range("energy_critical_pct", b.energy_critical_pct, 0, 100);
  range("autonomy_margin_pct", b.autonomy_margin_pct, 0, 200);
  range("maintenance_interval_h", b.maintenance_interval_h, 0, 10000, true);
  range("last_maintenance_engine_h", b.last_maintenance_engine_h, 0, 1e9);
  const w = b.energy_warning_pct as number | null, c = b.energy_critical_pct as number | null, r = b.energy_reserve_pct as number | null;
  if (!e.energy_critical_pct && w !== null && c !== null && !(c < w)) e.energy_critical_pct = "O nível crítico deve ser menor que o de aviso.";
  if (!e.energy_critical_pct && r !== null && c !== null && c < r) e.energy_critical_pct = "O nível crítico não pode ficar abaixo da reserva.";
  const mi = b.maintenance_interval_h, ml = b.last_maintenance_engine_h;
  if (!e.maintenance_interval_h && !e.last_maintenance_engine_h && (mi === null) !== (ml === null))
    e[mi === null ? "maintenance_interval_h" : "last_maintenance_engine_h"] = "Informe o intervalo e o horímetro da última manutenção (ou nenhum dos dois).";
  return e;
}

/** Erro 422 do backend → texto legível (lista de {loc,msg} ou string). */
export function apiErrorText(detail: unknown): string {
  if (Array.isArray(detail)) return detail.map((d: any) => (d?.loc ? `${String(d.loc).replace(/^body\.?/, "")}: ${d.msg}` : d?.msg ?? String(d))).join(" · ");
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}

export const telemetryTopic = (id: string) => `agrofleet/v1/${id.trim() || "<identificador>"}/telemetry`;
