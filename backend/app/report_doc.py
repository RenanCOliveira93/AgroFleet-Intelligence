"""Relatório para impressão e envio (Etapa 10): ``GET /reports/document``.

Monta, no backend, o conteúdo de um relatório de período: totais, máquinas, operações e alertas. A interface só formata
e imprime (o navegador gera o PDF) ou abre o e-mail do usuário com o resumo; nenhum serviço externo é contratado.

Regras herdadas: nada é estimado aqui além do que os cálculos existentes já fazem (``report`` e ``mission``); campo sem
dado fica ``None``; a origem dos dados (simulado/real) acompanha o documento.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from agrofleet import registry as RG
from agrofleet.analytics.metrics import report
from agrofleet.analytics.mission import MissionConfig, operation_consumption

from .db import AlertRow, FieldRow, MachineProfileRow, OperationRow, PropertyRow, load_frame
from .op_alerts_service import alert_dict, priority_key
from .operations_api import operation_state

MAX_OPERATIONS = 50
MAX_ALERTS = 30
SCOPE_NOTE = ("Sistema exclusivamente de monitoramento: os alertas são informativos e não há comandos para máquinas. "
              "A severidade crítica representa risco operacional estimado, não uma decisão automática de segurança.")
ORIGIN_NOTE = {"simulated": "Dados SIMULADOS: não representam máquinas reais.",
               "real": "Dados reportados pelas máquinas (origem real).",
               "unknown": "Dados de origem não declarada."}


def _utc(x: datetime | None) -> datetime | None:
    if x is None:
        return None
    return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)


def _aware(x: datetime | None) -> datetime | None:
    return None if x is None else (x if x.tzinfo else x.replace(tzinfo=timezone.utc))


def _overlaps(op_start: datetime, op_end: datetime | None, start: datetime | None, end: datetime | None) -> bool:
    if end is not None and _aware(op_start) > end:
        return False
    return not (start is not None and op_end is not None and _aware(op_end) < start)


def build_report_router(session, viewer, live, settings, clean) -> APIRouter:
    r = APIRouter(tags=["analytics"])

    @r.get("/reports/document")
    def report_document(start: datetime | None = None, end: datetime | None = None, origin: str = "all",
                        property_id: str | None = None,
                        tz: str | None = Query(None, description="fuso para exibir; padrão: o da propriedade"),
                        user=Depends(viewer), s=Depends(session)):
        start, end = _utc(start), _utc(end)
        if start and end and end <= start:
            raise HTTPException(422, "o fim do período deve ser posterior ao início")
        prop = s.get(PropertyRow, property_id) if property_id else None
        if property_id and prop is None:
            raise HTTPException(404, "propriedade não cadastrada")

        machines = {row.id: RG.MachineProfile.model_validate(row.spec) for row in s.execute(select(MachineProfileRow)).scalars()}
        if origin != "all":
            machines = {k: m for k, m in machines.items() if m.origin == origin}
        prop_machines = {k for k, m in machines.items() if m.property_id == property_id} if property_id else None

        df = load_frame(s, None, start, end, origin)
        if property_id and not df.empty:   # telemetria marcada com a propriedade OU de máquina cadastrada nela
            df = df[(df["property_id"] == property_id) | df["machine_id"].isin(prop_machines or set())]

        totals, machine_rows = [], []
        if not df.empty:   # as uniões de geometria (shapely) liberam o GIL: os dois agregados rodam em paralelo
            with ThreadPoolExecutor(2) as ex:
                ft, fm = ex.submit(report, df, "total"), ex.submit(report, df, "machine")
                totals, machine_rows = ft.result()["rows"], fm.result()["rows"]
        by_machine = []
        for row in machine_rows:
            mid = row["machine_id"]
            m = machines.get(mid)
            by_machine.append({**row, "name": m.name if m else None,
                               "kind_label": RG.KIND_LABEL[m.kind] if m else None,
                               "energy_label": RG.ENERGY_LABEL[m.energy_source] if m else None})

        # operações que se sobrepõem ao período
        ops_rows = [RG.OperationPlan.model_validate(row.spec) for row in
                    s.execute(select(OperationRow).order_by(OperationRow.start.desc())).scalars()]
        ops_rows = [op for op in ops_rows if (origin == "all" or op.origin == origin)
                    and (not property_id or op.property_id == property_id or op.machine_id in (prop_machines or set()))
                    and _overlaps(op.start, op.end, start, end)]
        fields = {f.id: f.name for f in s.execute(select(FieldRow)).scalars()}
        operations = []
        for op in ops_rows[:MAX_OPERATIONS]:
            m = machines.get(op.machine_id)
            item = {"operation_id": op.operation_id, "name": op.name, "operation_label": RG.OPERATION_LABEL[op.operation_type],
                    "status": op.status, "status_label": RG.STATUS_LABEL[op.status], "origin": op.origin,
                    "machine_id": op.machine_id, "machine_name": m.name if m else None,
                    "field_id": op.field_id, "field_name": fields.get(op.field_id),
                    "start": op.start.isoformat(), "end": op.end.isoformat() if op.end else None,
                    "mission": None, "consumption": None, "unavailable": None}
            try:
                st = operation_state(s, op.operation_id, None, live, settings)
                md = st.mission.to_dict()
                series = md.pop("series")
                item["mission"] = {k: md.get(k) for k in ("planned_ha", "planned_source", "worked_ha", "remaining_ha", "progress_pct",
                                                           "unique_ha", "overlap_ha", "outside_field_ha", "working_h", "engaged_h",
                                                           "no_data_h", "rate_ha_h_working", "eta", "eta_range", "last_ts")}
                item["consumption"] = operation_consumption(series, m.energy_source if m else None, MissionConfig().bucket_min)
            except Exception as e:                     # uma operação com problema não derruba o relatório
                item["unavailable"] = f"progresso indisponível: {e}"
            operations.append(item)

        # alertas que tocam o período
        q = select(AlertRow).order_by(AlertRow.start.desc())
        if origin != "all":
            q = q.where(AlertRow.origin == origin)
        if start:
            q = q.where(AlertRow.end >= start)
        if end:
            q = q.where(AlertRow.start <= end)
        op_ids = {op.operation_id for op in ops_rows}
        last = {}
        if not df.empty:
            last = {(mid, o): g["ts"].max().to_pydatetime() for (mid, o), g in df.groupby(["machine_id", "origin"])}
        alerts = [alert_dict(a, last) for a in s.execute(q.limit(5000)).scalars()
                  if not property_id or a.operation_id in op_ids or a.machine_id in (prop_machines or set())]
        alerts.sort(key=priority_key)
        counts = {sev: sum(1 for a in alerts if a["severity"] == sev) for sev in ("critical", "warning", "info")}

        origins = sorted({*(o for row in totals for o in row["origins"]), *(op["origin"] for op in operations)})
        pspec = prop.spec if prop else None
        return clean({
            "title": "Relatório de operações",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generated_by": user.get("sub"),
            "period": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
            "filter": {"origin": origin, "property_id": property_id},
            "property": pspec,
            "timezone": tz or (pspec or {}).get("timezone") or "America/Sao_Paulo",
            "data_origins": origins,
            "origin_notes": [ORIGIN_NOTE.get(o, o) for o in origins],
            "totals": totals[0] if totals else None,
            "machines": by_machine,
            "operations": operations,
            "operations_truncated": max(len(ops_rows) - MAX_OPERATIONS, 0),
            "alerts": {"counts": counts, "total": len(alerts), "active": sum(1 for a in alerts if a["status"] == "active"),
                       "items": [{k: a.get(k) for k in ("machine_id", "type", "label", "severity", "status", "start", "end", "message",
                                                        "operation_id", "origin")} for a in alerts[:MAX_ALERTS]],
                       "truncated": max(len(alerts) - MAX_ALERTS, 0)},
            "notes": [SCOPE_NOTE, "Área única = união das faixas trabalhadas (sobreposição conta uma vez). "
                      "Campos sem dado aparecem como “—”: nada é estimado para preencher lacunas."],
        })

    return r
