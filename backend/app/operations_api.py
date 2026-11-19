"""Estado de uma operação (Etapa 4): missão, autonomia, conexão e geometria para o painel.

Todos os cálculos ficam em ``agrofleet.analytics.mission`` e ``autonomy`` (puros e testados); aqui só se carrega o
dado, se decide o instante de avaliação e se monta a resposta.

Instante de avaliação (``as_of``):
- ausente: agora. A idade do dado vem de ``ingested_at`` (chegada ao servidor), não do relógio do dispositivo, que
  pode estar adiantado (publicador com ``--speedup``) ou atrasado.
- informado: avalia a operação como estava naquele instante (reprodução, auditoria). Só entram amostras com
  ``ts <= as_of`` e a idade é ``as_of − última amostra``.

Geometria: simplificada SÓ para exibição (``display``); as áreas da resposta de status vêm da geometria exata.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from shapely.geometry import LineString, MultiLineString, mapping, shape
from sqlalchemy import func, select

from agrofleet import registry as RG
from agrofleet.analytics.autonomy import autonomy
from agrofleet.analytics.mission import mission
from agrofleet.geo import ha, transform_geom
from agrofleet.schema import ENGINE_ENERGY_FIELDS

from .db import FieldRow, TelemetryRow, load_frame
from .registry_api import get_machine, get_operation, labels_for_machine

CONNECTION_LABEL = {"online": "Conectado", "delayed": "Atrasado", "offline": "Sem conexão", "unknown": "Desconhecido"}
MODE_LABEL = {"simulated": "MODO SIMULAÇÃO", "real": "Operação real", "unknown": "Origem desconhecida"}
SNAPSHOT = ["ts", "state", "lat", "lon", "speed_ms", "gnss_fix", "accuracy_m", "working_width_m", "flow_lpm", "pressure_bar",
            "tank_level_l", *ENGINE_ENERGY_FIELDS]


def _utc(x: datetime | None) -> datetime | None:
    if x is None:
        return None
    return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)


def connection_state(age_s: float | None, live_now: bool, live_window_s: float, stale_after_s: float) -> str:
    if live_now:
        return "online"
    if age_s is None:
        return "unknown"
    if age_s <= live_window_s:
        return "online"
    if age_s <= stale_after_s:
        return "delayed"
    return "offline"


def _val(v):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    if isinstance(v, (np.floating,)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


STALE_AFTER_S = 300.0


def load_context(s, operation_id: str, as_of: datetime | None, live, settings):
    """Carrega operação, cadastro, talhão e telemetria e decide o instante e a idade do dado (ver docstring do módulo)."""
    op = get_operation(s, operation_id)
    if not op:
        raise HTTPException(404, "operação não cadastrada")
    prof = get_machine(s, op.machine_id)
    if prof is not None and prof.origin != op.origin:
        prof = None                                   # cadastro de outra origem nunca é usado
    fld = s.get(FieldRow, op.field_id)
    fld_geo = fld.geojson if fld is not None and fld.origin == op.origin else None
    as_of = _utc(as_of)
    end = op.end if op.end is not None else None
    if as_of is not None:
        end = min(end, as_of) if end is not None else as_of
    df = load_frame(s, [op.machine_id], op.start, end, op.origin)
    now = datetime.now(timezone.utc)
    if as_of is None:
        q = select(func.max(TelemetryRow.ingested_at)).where(TelemetryRow.machine_id == op.machine_id,
                                                             TelemetryRow.origin == op.origin)
        last_ing = s.execute(q).scalar()
        last_ing = _utc(last_ing) if last_ing is not None else None
        # idade = max(atraso do enlace, idade do dado): carga histórica recém-importada não parece "ao vivo", e
        # relógio do dispositivo adiantado (ts no futuro) não esconde um enlace parado
        link_age = (now - last_ing).total_seconds() if last_ing is not None else None
        data_age = max((now - df["ts"].iloc[-1].to_pydatetime()).total_seconds(), 0.0) if len(df) else None
        ages = [a for a in (link_age, data_age) if a is not None]
        age = max(max(ages), 0.0) if ages else None
        live_now = live.is_live(op.machine_id, settings.live_window_s) and (data_age is None or data_age <= STALE_AFTER_S)
        basis = "max(ingested_at, ts)" if last_ing is not None else "ts"
    else:
        last_ing = None
        age = max((as_of - df["ts"].iloc[-1].to_pydatetime()).total_seconds(), 0.0) if len(df) else None
        live_now, basis = False, "as_of"
    return op, prof, fld, fld_geo, df, now, as_of, age, last_ing, live_now, basis


def _active_alerts(s, op, df) -> list[dict]:
    """Alertas ativos da máquina (operacionais e de evento), por prioridade: crítico, aviso, informativo; recentes antes."""
    from .db import AlertRow
    from .op_alerts_service import alert_dict, priority_key
    last = {(op.machine_id, op.origin): df["ts"].iloc[-1].to_pydatetime()} if len(df) else {}
    rows = s.execute(select(AlertRow).where(AlertRow.machine_id == op.machine_id, AlertRow.origin == op.origin)
                     .order_by(AlertRow.end.desc()).limit(500)).scalars()
    out = [a for a in (alert_dict(r, last) for r in rows) if a["status"] == "active"]
    return sorted(out, key=priority_key)


def build_operations_router(session, viewer, live, settings, clean) -> APIRouter:
    r = APIRouter(tags=["operations"])
    stale_after_s = STALE_AFTER_S

    def _load(s, operation_id, as_of):
        return load_context(s, operation_id, as_of, live, settings)


    @r.get("/operations/{operation_id}/status")
    def operation_status(operation_id: str, as_of: datetime | None = Query(None, description="avaliar como neste instante (UTC)"),
                         _=Depends(viewer), s=Depends(session)):
        op, prof, fld, fld_geo, df, now, as_of, age, last_ing, live_now, basis = _load(s, operation_id, as_of)
        m = mission(df, op, fld_geo, data_age_s=age)
        a = autonomy(df, prof, m.remaining(), data_age_s=age) if prof is not None else None
        conn = connection_state(age, live_now, settings.live_window_s, stale_after_s)
        snap = None
        if len(df):
            last = df.iloc[-1]
            snap = {c: _val(last[c]) for c in SNAPSHOT if c in last}
            snap["ts"] = last["ts"].isoformat()
        md = m.to_dict()
        series = md.pop("series")
        return clean({
            "operation": {**op.model_dump(mode="json"), "operation_label": RG.OPERATION_LABEL[op.operation_type],
                          "status_label": RG.STATUS_LABEL[op.status]},
            "mode": op.origin, "mode_label": MODE_LABEL.get(op.origin, op.origin),
            "machine": {**prof.model_dump(mode="json"), **labels_for_machine(prof)} if prof else None,
            "field": {"field_id": op.field_id, "name": fld.name if fld else None, "has_geometry": fld_geo is not None,
                      "area_ha": md["planned_ha"] if md["planned_source"] == "field_geometry" else None},
            "evaluated_at": now.isoformat(), "as_of": as_of.isoformat() if as_of else None,
            "connection": {"state": conn, "label": CONNECTION_LABEL[conn], "age_s": age, "basis": basis,
                           "last_ingested_at": last_ing.isoformat() if last_ing else None,
                           "last_ts": snap["ts"] if snap else None},
            "snapshot": snap,
            "mission": md,
            "autonomy": a.to_dict() if a else None,
            "autonomy_unavailable": None if a else "Máquina sem cadastro da mesma origem: autonomia não calculada.",
            "series": series,
            "n_samples": int(len(df)),
            "alerts": _active_alerts(s, op, df),
        })

    @r.get("/operations/{operation_id}/geometry")
    def operation_geometry(operation_id: str, as_of: datetime | None = None,
                           simplify_m: float = Query(0.5, ge=0, le=20), track_points: int = Query(4000, ge=100, le=50000),
                           _=Depends(viewer), s=Depends(session)):
        op, prof, fld, fld_geo, df, now, as_of, age, *_ = _load(s, operation_id, as_of)
        m = mission(df, op, fld_geo, data_age_s=age)
        cov = m.coverage
        out = {"field": fld_geo, "worked": None, "pending": None, "outside": None, "track": None, "last": None,
               "display": {"simplify_m": simplify_m, "closing_m": 0.05,
                           "note": "Geometria simplificada só para exibição; as áreas vêm de /status (cálculo exato)."},
               "mode": op.origin, "mode_label": MODE_LABEL.get(op.origin, op.origin)}
        if cov is not None and cov.union_geom is not None and cov.proj is not None:
            proj = cov.proj
            # fechamento de 5 cm: remove frestas de ~1 cm entre passadas vizinhas (ruído RTK) só no desenho
            disp = cov.union_geom.buffer(0.05, quad_segs=2).buffer(-0.05, quad_segs=2)
            fg = cov.field_geom
            worked = disp.intersection(fg) if fg is not None else disp
            ll = lambda g: mapping(transform_geom(g.simplify(simplify_m, preserve_topology=True), proj.inv)) if not g.is_empty else None
            out["worked"] = ll(worked)
            if fg is not None:
                out["pending"] = ll(fg.difference(disp))
                out["outside"] = ll(disp.difference(fg))
        if len(df):
            pos = df.dropna(subset=["lat", "lon"])
            if len(pos):
                step = max(int(np.ceil(len(pos) / track_points)), 1)
                # quebra a linha em lacunas (> 10 s): não se desenha trajeto onde não houve dado
                gap = pos["ts"].diff().dt.total_seconds().fillna(0).to_numpy() > 10
                parts, cur = [], []
                for i, (lo, la, g) in enumerate(zip(pos["lon"].to_numpy(), pos["lat"].to_numpy(), gap)):
                    if g and len(cur) > 1:
                        parts.append(cur); cur = []
                    elif g:
                        cur = []
                    if i % step == 0 or g or i == len(pos) - 1:
                        cur.append((float(lo), float(la)))
                if len(cur) > 1:
                    parts.append(cur)
                out["track"] = mapping(MultiLineString(parts)) if parts else None
                last = pos.iloc[-1]
                out["last"] = {"ts": last["ts"].isoformat(), "lat": float(last["lat"]), "lon": float(last["lon"]),
                               "heading_deg": _val(last.get("heading_deg")), "state": last["state"]}
        return clean(out)

    return r
