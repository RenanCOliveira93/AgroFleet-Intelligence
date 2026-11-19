"""Rotas de cadastro: propriedades, perfis de máquina e operações.

Leitura: papel ``viewer``. Escrita: propriedades e máquinas exigem ``admin`` (afetam limites de alerta e capacidades);
operações exigem ``analyst``. O id do caminho deve coincidir com o do corpo (sem renomear em silêncio).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from agrofleet import registry as RG

from .db import FieldRow, MachineProfileRow, OperationRow, PropertyRow


def _err(e: ValidationError) -> HTTPException:
    return HTTPException(422, [{"loc": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()])


def _dump(model) -> dict:
    return model.model_dump(mode="json")


def get_machine(s, machine_id: str) -> RG.MachineProfile | None:
    r = s.get(MachineProfileRow, machine_id)
    return RG.MachineProfile.model_validate(r.spec) if r else None


def get_operation(s, operation_id: str) -> RG.OperationPlan | None:
    r = s.get(OperationRow, operation_id)
    return RG.OperationPlan.model_validate(r.spec) if r else None


def upsert_property(s, p: RG.PropertyInfo):
    row = s.get(PropertyRow, p.property_id) or PropertyRow(id=p.property_id)
    row.origin, row.spec = p.origin, _dump(p)
    s.add(row)


def upsert_machine(s, m: RG.MachineProfile):
    row = s.get(MachineProfileRow, m.machine_id) or MachineProfileRow(id=m.machine_id)
    row.origin, row.property_id, row.spec = m.origin, m.property_id, _dump(m)
    s.add(row)


def upsert_operation(s, op: RG.OperationPlan):
    fld = s.get(FieldRow, op.field_id)
    RG.check_operation_links(op, get_machine(s, op.machine_id), fld.origin if fld else None)
    row = s.get(OperationRow, op.operation_id) or OperationRow(id=op.operation_id)
    row.origin, row.machine_id, row.field_id, row.status, row.start, row.spec = op.origin, op.machine_id, op.field_id, op.status, op.start, _dump(op)
    s.add(row)


def labels_for_machine(m: RG.MachineProfile) -> dict:
    return {"kind_label": RG.KIND_LABEL[m.kind], "energy_label": RG.ENERGY_LABEL[m.energy_source]}


def build_router(session, viewer, analyst, admin) -> APIRouter:
    r = APIRouter(tags=["registry"])

    # ---------- propriedades ----------
    @r.get("/registry/properties")
    def list_properties(origin: str = "all", _=Depends(viewer), s=Depends(session)):
        q = select(PropertyRow).order_by(PropertyRow.id)
        if origin != "all":
            q = q.where(PropertyRow.origin == origin)
        return [row.spec for row in s.execute(q).scalars()]

    @r.put("/registry/properties/{property_id}")
    def put_property(property_id: str, body: dict, _=Depends(admin), s=Depends(session)):
        try:
            p = RG.PropertyInfo.model_validate(body)
        except ValidationError as e:
            raise _err(e)
        if p.property_id != property_id:
            raise HTTPException(422, "property_id do corpo difere do caminho")
        upsert_property(s, p)
        s.commit()
        return _dump(p)

    # ---------- máquinas ----------
    @r.get("/registry/machines")
    def list_machines(origin: str = "all", _=Depends(viewer), s=Depends(session)):
        q = select(MachineProfileRow).order_by(MachineProfileRow.id)
        if origin != "all":
            q = q.where(MachineProfileRow.origin == origin)
        out = []
        for row in s.execute(q).scalars():
            m = RG.MachineProfile.model_validate(row.spec)
            out.append({**row.spec, **labels_for_machine(m)})
        return out

    @r.get("/registry/machines/{machine_id}")
    def get_machine_ep(machine_id: str, _=Depends(viewer), s=Depends(session)):
        m = get_machine(s, machine_id)
        if not m:
            raise HTTPException(404, "máquina não cadastrada")
        return {**_dump(m), **labels_for_machine(m)}

    @r.put("/registry/machines/{machine_id}")
    def put_machine(machine_id: str, body: dict, _=Depends(admin), s=Depends(session)):
        try:
            m = RG.MachineProfile.model_validate(body)
        except ValidationError as e:
            raise _err(e)
        if m.machine_id != machine_id:
            raise HTTPException(422, "machine_id do corpo difere do caminho")
        if m.property_id and (pr := s.get(PropertyRow, m.property_id)) is not None and pr.origin != m.origin:
            raise HTTPException(422, "máquina e propriedade com origens diferentes")
        upsert_machine(s, m)
        s.commit()
        return {**_dump(m), **labels_for_machine(m)}

    # ---------- operações ----------
    @r.get("/operations")
    def list_operations(status: str | None = None, machine_id: str | None = None, origin: str = "all",
                        _=Depends(viewer), s=Depends(session)):
        q = select(OperationRow).order_by(OperationRow.start.desc())
        if status:
            q = q.where(OperationRow.status == status)
        if machine_id:
            q = q.where(OperationRow.machine_id == machine_id)
        if origin != "all":
            q = q.where(OperationRow.origin == origin)
        return [{**row.spec, "operation_label": RG.OPERATION_LABEL[row.spec["operation_type"]],
                 "status_label": RG.STATUS_LABEL[row.spec["status"]]} for row in s.execute(q).scalars()]

    @r.get("/operations/{operation_id}")
    def get_operation_ep(operation_id: str, _=Depends(viewer), s=Depends(session)):
        op = get_operation(s, operation_id)
        if not op:
            raise HTTPException(404, "operação não cadastrada")
        return {**_dump(op), "operation_label": RG.OPERATION_LABEL[op.operation_type], "status_label": RG.STATUS_LABEL[op.status]}

    @r.put("/operations/{operation_id}")
    def put_operation(operation_id: str, body: dict, _=Depends(analyst), s=Depends(session)):
        try:
            op = RG.OperationPlan.model_validate(body)
        except ValidationError as e:
            raise _err(e)
        if op.operation_id != operation_id:
            raise HTTPException(422, "operation_id do corpo difere do caminho")
        try:
            upsert_operation(s, op)
        except ValueError as e:
            raise HTTPException(422, str(e))
        s.commit()
        return _dump(op)

    return r
