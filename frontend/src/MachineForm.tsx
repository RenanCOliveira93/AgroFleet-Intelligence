import { useState } from "react";
import { Modal } from "./Modal";
import { api, ApiError } from "./api";
import type { PropertyLite } from "./shell";
import {
  apiErrorText, capacityUnit, emptyMachineForm, ENERGY_OPTIONS, formFromMachine, KIND_OPTIONS, machineBody, MachineFormState,
  ORIGIN_OPTIONS, telemetryTopic, validateMachine,
} from "./machineModel";

function Field({ label, hint, error, children, id }: { label: string; hint?: string; error?: string; children: React.ReactNode; id: string }) {
  return (
    <div className={`ff ${error ? "has-err" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {children}
      {error ? <small className="ff-err" id={`${id}-err`} role="alert">{error}</small> : hint ? <small className="muted" id={`${id}-hint`}>{hint}</small> : null}
    </div>
  );
}

export function MachineFormDialog({ machine, properties, existingIds, defaultProperty, onClose, onSaved }: {
  machine?: any; properties: PropertyLite[]; existingIds: string[]; defaultProperty: string | null;
  onClose: () => void; onSaved: (m: any) => void;
}) {
  const editing = !!machine;
  const [f, setF] = useState<MachineFormState>(() => machine ? formFromMachine(machine)
    : emptyMachineForm(defaultProperty ?? "", (properties.find((p) => p.property_id === defaultProperty)?.origin as any) ?? "real"));
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [apiErr, setApiErr] = useState("");
  const [saved, setSaved] = useState<any>(null);
  const propOrigin = properties.find((p) => p.property_id === f.property_id)?.origin ?? null;
  const errs = validateMachine(f, { existingIds, editing, propertyOrigin: propOrigin });
  const show = (k: keyof MachineFormState) => (touched ? errs[k] : undefined);
  const set = <K extends keyof MachineFormState>(k: K, v: MachineFormState[K]) => setF((x) => ({ ...x, [k]: v }));
  const unit = capacityUnit(f.energy_source);
  const props = properties.filter((p) => !p.origin || p.origin === f.origin || p.property_id === f.property_id);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true); setApiErr("");
    if (Object.keys(errs).length) return;
    const body = machineBody(f);
    setBusy(true);
    try {
      const r = await api(`/registry/machines/${encodeURIComponent(String(body.machine_id))}`, { method: "PUT", body });
      setSaved(r); onSaved(r);
    } catch (x) {
      setApiErr(x instanceof ApiError ? (x.status === 403 ? "Somente o administrador cadastra máquinas." : apiErrorText(x.detail)) : "Falha ao salvar.");
    } finally { setBusy(false); }
  };

  if (saved) return (
    <Modal title="Máquina salva" onClose={onClose}>
      <div className="saved-box" data-testid="machine-saved">
        <p><b>{saved.name}</b> ({saved.machine_id}) foi {editing ? "atualizada" : "cadastrada"}.</p>
        {saved.origin === "real" ? (<>
          <h3>Próximo passo: ligar a máquina</h3>
          <p>Configure o dispositivo de telemetria (gateway ou computador de bordo) para publicar por MQTT:</p>
          <dl className="kvlist">
            <dt>Tópico</dt><dd><code>{telemetryTopic(saved.machine_id)}</code></dd>
            <dt>Usuário</dt><dd><code>device</code> (senha definida pelo administrador no broker)</dd>
            <dt>Formato</dt><dd>JSON <code>agrofleet/v1</code>, QoS 1, com <code>"machine_id": "{saved.machine_id}"</code> e <code>"origin": "real"</code></dd>
          </dl>
          <p className="muted small">Sem dispositivo? Envie um arquivo CSV/JSON em Planejamento › Upload. Detalhes no Guia do usuário, seção 7.</p>
        </>) : <p className="muted">Origem {saved.origin === "simulated" ? "simulada" : "não declarada"}: os dados desta máquina não se misturam aos de máquinas reais.</p>}
        <div className="modal-actions"><button onClick={onClose}>Concluir</button></div>
      </div>
    </Modal>
  );

  return (
    <Modal title={editing ? `Editar máquina ${machine.machine_id}` : "Cadastrar máquina"} onClose={onClose} wide>
      <form onSubmit={submit} noValidate data-testid="machine-form">
        <fieldset>
          <legend>Identificação</legend>
          <div className="form-grid">
            <Field id="mf-id" label="Identificador *" hint="O mesmo machine_id que o dispositivo envia. Ex.: TR-0451" error={show("machine_id")}>
              <input id="mf-id" value={f.machine_id} onChange={(e) => set("machine_id", e.target.value)} disabled={editing} autoComplete="off" spellCheck={false} aria-invalid={!!show("machine_id")} />
            </Field>
            <Field id="mf-name" label="Nome *" hint="Como aparece nas telas e relatórios" error={show("name")}>
              <input id="mf-name" value={f.name} onChange={(e) => set("name", e.target.value)} aria-invalid={!!show("name")} />
            </Field>
            <Field id="mf-kind" label="Tipo">
              <select id="mf-kind" value={f.kind} onChange={(e) => set("kind", e.target.value as any)}>{KIND_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            </Field>
            <Field id="mf-origin" label="Origem dos dados" hint="Real = máquina física. Não muda depois de ter dados.">
              <select id="mf-origin" value={f.origin} onChange={(e) => { const o = e.target.value as MachineFormState["origin"];
                setF((x) => ({ ...x, origin: o, property_id: properties.find((p) => p.property_id === x.property_id)?.origin === o ? x.property_id : "" })); }} disabled={editing}>{ORIGIN_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            </Field>
            <Field id="mf-prop" label="Propriedade" error={show("property_id")} hint={props.length ? undefined : "Nenhuma propriedade desta origem cadastrada."}>
              <select id="mf-prop" value={f.property_id} onChange={(e) => set("property_id", e.target.value)} aria-invalid={!!show("property_id")}>
                <option value="">— sem propriedade —</option>
                {props.map((p) => <option key={p.property_id} value={p.property_id}>{p.name ?? p.property_id}</option>)}
              </select>
            </Field>
            <label className="check"><input type="checkbox" checked={f.autonomous} onChange={(e) => set("autonomous", e.target.checked)} /> Máquina autônoma</label>
          </div>
        </fieldset>

        <fieldset>
          <legend>Energia e autonomia</legend>
          <div className="form-grid">
            <Field id="mf-energy" label="Fonte de energia">
              <select id="mf-energy" value={f.energy_source} onChange={(e) => set("energy_source", e.target.value as any)}>{ENERGY_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            </Field>
            <Field id="mf-cap" label={unit ? `Capacidade (${unit})` : "Capacidade"} hint={unit ? (unit === "L" ? "Tanque de combustível" : "Bateria utilizável") : "Escolha a fonte de energia para informar"} error={show("capacity")}>
              <input id="mf-cap" inputMode="decimal" value={f.capacity} onChange={(e) => set("capacity", e.target.value)} disabled={!unit} aria-invalid={!!show("capacity")} />
            </Field>
            <Field id="mf-res" label="Reserva operacional (%)" hint="Parte da capacidade que não conta para a missão (0–50)" error={show("energy_reserve_pct")}>
              <input id="mf-res" inputMode="decimal" value={f.energy_reserve_pct} onChange={(e) => set("energy_reserve_pct", e.target.value)} />
            </Field>
            <Field id="mf-warn" label="Alerta de nível baixo (%)" hint="Aviso quando o nível ficar abaixo" error={show("energy_warning_pct")}>
              <input id="mf-warn" inputMode="decimal" value={f.energy_warning_pct} onChange={(e) => set("energy_warning_pct", e.target.value)} />
            </Field>
            <Field id="mf-crit" label="Nível crítico (%)" hint="Menor que o aviso e não abaixo da reserva" error={show("energy_critical_pct")}>
              <input id="mf-crit" inputMode="decimal" value={f.energy_critical_pct} onChange={(e) => set("energy_critical_pct", e.target.value)} />
            </Field>
            <Field id="mf-margin" label="Margem de autonomia (%)" hint="Folga exigida para considerar a autonomia suficiente" error={show("autonomy_margin_pct")}>
              <input id="mf-margin" inputMode="decimal" value={f.autonomy_margin_pct} onChange={(e) => set("autonomy_margin_pct", e.target.value)} />
            </Field>
          </div>
        </fieldset>

        {f.energy_source !== "electric" && (
          <fieldset>
            <legend>Manutenção preventiva (opcional)</legend>
            <div className="form-grid">
              <Field id="mf-mi" label="Intervalo (horas de motor)" error={show("maintenance_interval_h")}>
                <input id="mf-mi" inputMode="decimal" value={f.maintenance_interval_h} onChange={(e) => set("maintenance_interval_h", e.target.value)} />
              </Field>
              <Field id="mf-ml" label="Horímetro na última manutenção (h)" error={show("last_maintenance_engine_h")}>
                <input id="mf-ml" inputMode="decimal" value={f.last_maintenance_engine_h} onChange={(e) => set("last_maintenance_engine_h", e.target.value)} />
              </Field>
            </div>
          </fieldset>)}

        <p className="muted small">Só o identificador e o nome são obrigatórios. Campo em branco fica “não informado” e o cálculo que depende dele aparece como indisponível: nada é preenchido por estimativa.</p>
        {apiErr && <p className="err" role="alert">{apiErr}</p>}
        {touched && Object.keys(errs).length > 0 && <p className="err" role="alert">Corrija os campos destacados.</p>}
        <div className="modal-actions">
          <button type="button" className="ghost" onClick={onClose}>Cancelar</button>
          <button type="submit" disabled={busy}>{busy ? "Salvando…" : editing ? "Salvar alterações" : "Cadastrar máquina"}</button>
        </div>
      </form>
    </Modal>
  );
}
