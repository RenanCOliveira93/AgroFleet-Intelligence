/** Funções puras do shell da interface (cabeçalho, seletor de propriedade, contagem de alertas). Sem cálculo operacional:
 *  só escolhem, filtram e formatam o que a API já devolveu. */

export const DEFAULT_TZ = "America/Sao_Paulo";
export const PROPERTY_KEY = "agf-property";

export interface PropertyLite { property_id: string; name?: string; timezone?: string; origin?: string }

export function readStored(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
export function writeStored(key: string, v: string | null) {
  try { if (v === null) window.localStorage.removeItem(key); else window.localStorage.setItem(key, v); } catch { /* sem armazenamento */ }
}

/** Propriedade efetiva: com uma só propriedade cadastrada, é ela; senão a escolha salva, se ainda existir; senão todas (null). */
export function resolveProperty(props: PropertyLite[], saved: string | null): string | null {
  if (props.length === 1) return props[0].property_id;
  return saved && props.some((p) => p.property_id === saved) ? saved : null;
}

/** Fuso para EXIBIR horários: o da propriedade escolhida; com "todas", o comum a todas (se houver); senão o padrão. */
export function propertyTimezone(props: PropertyLite[], pid: string | null): string {
  if (pid) return props.find((p) => p.property_id === pid)?.timezone || DEFAULT_TZ;
  const tzs = [...new Set(props.map((p) => p.timezone).filter(Boolean))];
  return tzs.length === 1 ? tzs[0]! : DEFAULT_TZ;
}

/** Alertas ativos da propriedade escolhida. O endpoint /alerts não filtra por propriedade: a ligação vem do cadastro
 *  (operação da propriedade ou máquina cadastrada na propriedade). Alerta sem vínculo conhecido só conta em "todas". */
export function alertsForProperty<T extends { machine_id?: string; operation_id?: string | null; status?: string }>(
  alerts: T[], pid: string | null, machines: { machine_id: string; property_id?: string | null }[], operations: { operation_id: string; machine_id?: string; property_id?: string | null }[],
): T[] {
  const active = alerts.filter((a) => a.status === undefined || a.status === "active");
  if (!pid) return active;
  const ops = new Set(operations.filter((o) => o.property_id === pid).map((o) => o.operation_id));
  const mids = new Set(machines.filter((m) => m.property_id === pid).map((m) => m.machine_id));
  return active.filter((a) => (a.operation_id ? ops.has(a.operation_id) : !!a.machine_id && mids.has(a.machine_id)));
}

/** Texto do badge: vazio sem alertas, "99+" acima de 99. */
export const badgeText = (n: number): string => (n <= 0 ? "" : n > 99 ? "99+" : String(n));

/** Data/hora ISO (UTC) no fuso da propriedade, "dd/mm/aaaa hh:mm". Ausente = "—". */
export function fmtDateTime(iso: string | null | undefined, tz: string = DEFAULT_TZ): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat("pt-BR", { timeZone: tz, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(d).replace(",", "");
  } catch {
    return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
  }
}

/** Iniciais para o avatar ("admin" → "AD"; "joao.silva" → "JS"). */
export function initials(user: string): string {
  const parts = user.split(/[\s._-]+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : parts[0].slice(0, 2)).toUpperCase();
}
