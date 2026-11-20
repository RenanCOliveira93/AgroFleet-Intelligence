"""Alertas operacionais no backend (Etapa 5): estado persistido, avaliação, reprodução e vigia de conexão.

A lógica está em ``agrofleet.analytics.op_alerts`` (pura). Aqui:
- o estado de cada máquina (pendências, alerta ativo por família) fica na tabela ``settings`` e sobrevive a reinícios;
- cada transição vira uma linha em ``alerts`` (``category = operational``), atualizada no lugar enquanto ativa;
- ``evaluate`` roda ao vivo (com intervalo mínimo por máquina), ``replay`` reconstrói a partir do histórico e
  ``watchdog`` verifica perda de conexão de operações ativas mesmo quando nenhum dado chega.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, fields as dc_fields
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from agrofleet.analytics import op_alerts as OA

from .db import AlertRow, OperationRow, SettingRow, TelemetryRow, get_setting
from .operations_api import operation_state

CFG_KEY = "ops_alert_config"


def ops_config(s) -> OA.OpsAlertConfig:
    saved = get_setting(s, CFG_KEY, {}) or {}
    ok = {f.name for f in dc_fields(OA.OpsAlertConfig)}
    return OA.OpsAlertConfig(**{k: v for k, v in saved.items() if k in ok})


def _state_key(origin: str, machine_id: str) -> str:
    return "opsst:" + hashlib.sha1(f"{origin}|{machine_id}".encode()).hexdigest()[:24]


def load_state(s, origin: str, machine_id: str) -> dict | None:
    r = s.get(SettingRow, _state_key(origin, machine_id))
    return (r.value or {}).get("state") if r else None


def save_state(s, origin: str, machine_id: str, state: dict):
    k = _state_key(origin, machine_id)
    v = {"origin": origin, "machine_id": machine_id, "state": state}
    r = s.get(SettingRow, k)
    if r:
        r.value = v
    else:
        s.add(SettingRow(key=k, value=v))


def delete_states(s, origin: str | None = None, machine_id: str | None = None) -> int:
    n = 0
    for r in s.execute(select(SettingRow).where(SettingRow.key.like("opsst:%"))).scalars():
        v = r.value or {}
        if (origin is None or v.get("origin") == origin) and (machine_id is None or v.get("machine_id") == machine_id):
            s.delete(r); n += 1
    return n


def _dt(iso: str | None):
    return datetime.fromisoformat(iso) if iso else None


def persist(s, transitions: list[dict]) -> list[dict]:
    """Grava/atualiza as linhas de alerta. Retorna as transições a difundir (sem os ``update`` silenciosos)."""
    out = []
    for tr in transitions:
        a = tr["alert"]
        row = s.get(AlertRow, a["id"])
        if row is None:
            row = AlertRow(id=a["id"], machine_id=a["machine_id"], type=a["type"], origin=a["origin"], experimental=False,
                           category="operational")
            s.add(row)
        row.severity = a.get("severity") or row.severity or "info"
        row.start, row.end = _dt(a["start"]), _dt(a["end"])
        row.message, row.rule = a["message"], a.get("rule")
        row.evidence = {**(a.get("evidence") or {}), **({"resolution": a["resolution"]} if a.get("resolution") else {})}
        row.status, row.operation_id = a.get("status", "active"), a.get("operation_id")
        row.resolved_at = _dt(a.get("resolved_at"))
        row.history = a.get("history", [])
        if tr["action"] != "update":
            out.append({"transition": tr["action"], **alert_dict(row)})
    return out


def alert_dict(r: AlertRow, last_ts: dict | None = None) -> dict:
    """Formato único de alerta na API (eventos antigos e operacionais). ``status`` dos eventos é derivado: ativo se o
    evento chega até a última amostra da máquina (±60 s)."""
    from agrofleet.analytics import anomalies as A
    cat = r.category or "anomaly"
    status = r.status
    if status is None:
        lt = (last_ts or {}).get((r.machine_id, r.origin))
        status = "active" if lt is not None and r.end >= lt - timedelta(seconds=60) else "resolved"
    d = {"id": r.id, "machine_id": r.machine_id, "type": r.type, "severity": r.severity, "start": r.start.isoformat(),
         "end": r.end.isoformat(), "duration_s": (r.end - r.start).total_seconds(), "message": r.message, "evidence": r.evidence,
         "rule": r.rule, "origin": r.origin, "experimental": r.experimental, "informational": True, "note": A.INFO_NOTE,
         "category": cat, "status": status, "operation_id": r.operation_id,
         "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None, "history": r.history or []}
    if cat == "operational":
        d["label"] = OA.LABEL.get(r.type, r.type)
        if r.severity == "critical" or any(h.get("severity") == "critical" for h in (r.history or [])):
            d["note"] = OA.CRITICAL_NOTE
    return d


PRIORITY = {"critical": 0, "warning": 1, "info": 2}


def priority_key(d: dict):
    """Ativos antes de resolvidos; depois severidade; depois o mais recente."""
    return (d["status"] != "active", PRIORITY.get(d["severity"], 3), -datetime.fromisoformat(d["end"]).timestamp())


def _snapshot(s, operation_id: str, as_of, live, settings, opstate=None):
    """Entradas das regras. ``opstate``: cálculo já feito (``operations_api.operation_state``), reaproveitado."""
    st = opstate or operation_state(s, operation_id, as_of, live, settings)
    ts = (st.as_of or st.now).isoformat()
    return st.op, OA.snapshot_from_data(st.df, st.op, st.prof, st.fld_geo, ts, st.age, mission_result=st.mission)


def evaluate(s, operation_id: str, live, settings, as_of=None, state: dict | None = None, use_stored: bool = True,
             opstate=None):
    """Uma avaliação completa (todas as famílias). Persiste estado e alertas; retorna (transições difundíveis, puladas)."""
    cfg = ops_config(s)
    op, snap = _snapshot(s, operation_id, as_of, live, settings, opstate)
    if state is None and use_stored:
        state = load_state(s, op.origin, op.machine_id)
    state, trs, skipped = OA.step(state, snap, cfg)
    save_state(s, op.origin, op.machine_id, state)
    return persist(s, trs), skipped, state


def current_operations(s, origin: str | None = None, machine_id: str | None = None) -> dict:
    """Operação corrente de cada máquina: a ativa de início mais recente, por (origem, máquina). Só ela é dona do
    estado ao vivo; outra operação ativa da mesma máquina (cadastro sobreposto) não é avaliada ao vivo."""
    q = select(OperationRow).where(OperationRow.status == "active")
    if origin is not None:
        q = q.where(OperationRow.origin == origin)
    if machine_id is not None:
        q = q.where(OperationRow.machine_id == machine_id)
    cur: dict = {}
    for r in s.execute(q.order_by(OperationRow.start, OperationRow.id)).scalars():
        cur[(r.origin, r.machine_id)] = r                 # ordem crescente: fica a de início mais recente
    return cur


def _last_ts(s, row):
    mx = s.execute(select(func.max(TelemetryRow.ts)).where(TelemetryRow.machine_id == row.machine_id,
                                                            TelemetryRow.origin == row.origin)).scalar()
    return None if mx is None else (mx if mx.tzinfo else mx.replace(tzinfo=timezone.utc))


def _public_transitions(trs: list[dict]) -> list[dict]:
    return [{"transition": tr["action"], **tr["alert"]} for tr in trs if tr["action"] != "update"]


def replay(s, operation_id: str, live, settings, end: datetime | None = None, window_s: float | None = None,
           step_s: float | None = None):
    """Reconstrói os alertas operacionais a partir do histórico: estado novo, avaliações a cada ``step_s`` de
    ``end − window_s`` até ``end`` (padrão: a janela de confirmação, para abrir o que está sustentado agora).

    O estado ao vivo da máquina nunca é usado como ponto de partida, e só é trocado quando a reconstrução representa
    o presente daquela máquina:
    - **reconstrução** (``end`` omitido ou no último dado): substitui os alertas operacionais desta operação. Se ela é a
      operação corrente da máquina (``current_operations``), o estado reconstruído vira o estado ao vivo e alertas
      ativos de outras operações que estavam nele são encerrados. Se não é, o estado ao vivo só perde as referências
      a esta operação, e o que ficou ativo no fim da reconstrução é encerrado (a operação não está em andamento).
    - **auditoria** (``end`` anterior ao último dado): nada é gravado, nem alertas nem estado (``persisted = False``).

    Retorna (transições, regras puladas, estado final, persisted)."""
    cfg = ops_config(s)
    row = s.get(OperationRow, operation_id)
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(404, "operação não cadastrada")
    mx = _last_ts(s, row)
    if mx is None:
        return [], [], None, False
    end = mx if end is None else end
    rebuild = end >= mx
    step_s = step_s or max(cfg.confirm_s, 1.0)
    window_s = cfg.confirm_s if window_s is None else window_s
    state, trs, skipped, snap = OA.new_state(), [], [], None
    n = max(int(window_s // step_s), 0)
    for k in range(n, -1, -1):
        t = end - timedelta(seconds=k * step_s)
        _, snap = _snapshot(s, operation_id, t, live, settings)
        state, tr, skipped = OA.step(state, snap, cfg)
        trs += tr
    store = getattr(live, "ops_cache", None)
    if store is not None:                                 # cache de instantes passados: só serve a esta reprodução
        store.entry(operation_id).past.release()
    if not rebuild:
        return _public_transitions(trs), skipped, state, False

    current = current_operations(s, row.origin, row.machine_id).get((row.origin, row.machine_id))
    is_current = current is not None and current.id == operation_id
    live_state = load_state(s, row.origin, row.machine_id)
    if is_current:
        for fam, st in (live_state or {}).items():        # alertas de outras operações no estado ao vivo: encerra
            act = st.get("active")
            if act and act.get("operation_id") != operation_id:
                trs.append(OA._close(st, act, snap, "resolve", f"Operação {operation_id} reconstruída como corrente."))
    else:
        for fam, st in state.items():                     # operação fora de andamento: nada fica aberto
            if st.get("active"):
                trs.append(OA._close(st, st["active"], snap, "resolve", "Operação não está em andamento; encerrado ao fim dos dados."))
    s.execute(delete(AlertRow).where(AlertRow.operation_id == operation_id, AlertRow.category == "operational"))
    s.flush()
    out = persist(s, trs)
    if is_current:
        save_state(s, row.origin, row.machine_id, state)
    elif live_state:
        for st in live_state.values():                    # as linhas desta operação agora são da reconstrução
            if (st.get("active") or {}).get("operation_id") == operation_id:
                st["active"], st["pending"], st["clearing"] = None, {}, None
        save_state(s, row.origin, row.machine_id, live_state)
    s.commit()
    return out, skipped, state, True


def watchdog(s, live, settings) -> list[dict]:
    """Perda de conexão da operação corrente de cada máquina (barato: só idade do dado; não recalcula missão)."""
    cfg = ops_config(s)
    now = datetime.now(timezone.utc)
    out = []
    for row in current_operations(s).values():
        q = select(func.max(TelemetryRow.ingested_at), func.max(TelemetryRow.ts)).where(
            TelemetryRow.machine_id == row.machine_id, TelemetryRow.origin == row.origin)
        ing, ts = s.execute(q).one()
        ages = [(now - (x if x.tzinfo else x.replace(tzinfo=timezone.utc))).total_seconds() for x in (ing, ts) if x is not None]
        age = max(max(ages), 0.0) if ages else None     # mesma regra de idade do /status: max(enlace, dado)
        snap = OA.Snapshot(machine_id=row.machine_id, origin=row.origin, ts=now.isoformat(), profile=None,
                           operation_id=row.id, operation_active=True, data_age_s=age)
        state = load_state(s, row.origin, row.machine_id)
        state, trs, _ = OA.step(state, snap, cfg, families=("connection_lost",))
        save_state(s, row.origin, row.machine_id, state)
        out += persist(s, trs)
    s.commit()
    return out


def config_dict(s) -> dict:
    return asdict(ops_config(s))
