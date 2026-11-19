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
from .operations_api import STALE_AFTER_S, load_context

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


def _snapshot(s, operation_id: str, as_of, live, settings):
    op, prof, fld, fld_geo, df, now, as_of_, age, *_ = load_context(s, operation_id, as_of, live, settings)
    ts = (as_of_ or now).isoformat()
    return op, OA.snapshot_from_data(df, op, prof, fld_geo, ts, age)


def evaluate(s, operation_id: str, live, settings, as_of=None, state: dict | None = None, use_stored: bool = True):
    """Uma avaliação completa (todas as famílias). Persiste estado e alertas; retorna (transições difundíveis, puladas)."""
    cfg = ops_config(s)
    op, snap = _snapshot(s, operation_id, as_of, live, settings)
    if state is None and use_stored:
        state = load_state(s, op.origin, op.machine_id)
    state, trs, skipped = OA.step(state, snap, cfg)
    save_state(s, op.origin, op.machine_id, state)
    return persist(s, trs), skipped, state


def replay(s, operation_id: str, live, settings, end: datetime | None = None, window_s: float | None = None,
           step_s: float | None = None):
    """Reconstrói os alertas operacionais a partir do histórico: estado novo, avaliações a cada ``step_s`` de
    ``end − window_s`` até ``end`` (padrão: a janela de confirmação, para abrir o que está sustentado agora).
    Substitui os alertas operacionais anteriores dessa operação."""
    cfg = ops_config(s)
    row = s.get(OperationRow, operation_id)
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(404, "operação não cadastrada")
    if end is None:
        mx = s.execute(select(func.max(TelemetryRow.ts)).where(TelemetryRow.machine_id == row.machine_id,
                                                                TelemetryRow.origin == row.origin)).scalar()
        if mx is None:
            return [], [], None
        end = mx if mx.tzinfo else mx.replace(tzinfo=timezone.utc)
    step_s = step_s or max(cfg.confirm_s, 1.0)
    window_s = cfg.confirm_s if window_s is None else window_s
    s.execute(delete(AlertRow).where(AlertRow.operation_id == operation_id, AlertRow.category == "operational"))
    delete_states(s, row.origin, row.machine_id)
    state, out, skipped = None, [], []
    n = max(int(window_s // step_s), 0)
    for k in range(n, -1, -1):
        t = end - timedelta(seconds=k * step_s)
        tr, skipped, state = evaluate(s, operation_id, live, settings, as_of=t, state=state or OA.new_state(), use_stored=False)
        out += tr
    s.commit()
    return out, skipped, state


def watchdog(s, live, settings) -> list[dict]:
    """Perda de conexão das operações ATIVAS (barato: só idade do dado; não recalcula missão)."""
    cfg = ops_config(s)
    now = datetime.now(timezone.utc)
    out = []
    for row in s.execute(select(OperationRow).where(OperationRow.status == "active")).scalars():
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
