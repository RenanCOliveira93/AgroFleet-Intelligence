"""Rotas de clima (Etapa 7). Papel mínimo: viewer. Nunca devolvem 5xx por falha de provedor (ver agrofleet.weather).

Ponto consultado para uma operação:
1. centroide da geometria do talhão da operação (mesma origem da operação);
2. senão, última posição de telemetria da máquina dentro da janela da operação;
3. sem nenhum dos dois, só o código IBGE (rotas INMET por município) ou "Sem dados meteorológicos".
Município: ``ibge_municipality`` da propriedade (operação → propriedade; senão a da máquina), senão
``AGROFLEET_WEATHER_IBGE``. Fuso: o da propriedade (escolhe o período da previsão: manhã/tarde/noite).
Operação simulada: ``location.fictitious=true`` com a nota de que o clima é o real do ponto, não do talhão.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from shapely.geometry import shape
from sqlalchemy import select

from agrofleet import registry as RG
from agrofleet.weather import SIMULATED_NOTE

from .db import FieldRow, PropertyRow, TelemetryRow
from .registry_api import get_machine, get_operation


def _geometry(g):
    if not isinstance(g, dict):
        return None
    if g.get("type") == "Feature":
        return g.get("geometry")
    if g.get("type") == "FeatureCollection":
        fs = [f.get("geometry") for f in g.get("features") or [] if isinstance(f, dict) and f.get("geometry")]
        return {"type": "GeometryCollection", "geometries": fs} if fs else None
    return g


def field_centroid(geojson) -> tuple[float, float] | None:
    """(lat, lon) do centroide do talhão (em graus; talhões têm poucos km, a distorção é desprezível para clima)."""
    try:
        g = shape(_geometry(geojson))
    except Exception:
        return None
    if g.is_empty:
        return None
    c = g.centroid
    return float(c.y), float(c.x)


def _property(s, property_id: str | None) -> RG.PropertyInfo | None:
    if not property_id:
        return None
    r = s.get(PropertyRow, property_id)
    if r is None:
        return None
    try:
        return RG.PropertyInfo.model_validate(r.spec)
    except Exception:
        return None


def operation_location(s, operation_id: str) -> dict:
    op = get_operation(s, operation_id)
    if not op:
        raise HTTPException(404, "operação não cadastrada")
    lat = lon = None
    source = None
    fld = s.get(FieldRow, op.field_id)
    if fld is not None and fld.origin == op.origin:
        c = field_centroid(fld.geojson)
        if c is not None:
            (lat, lon), source = c, "field_centroid"
    if lat is None:
        q = (select(TelemetryRow.lat, TelemetryRow.lon).where(TelemetryRow.machine_id == op.machine_id, TelemetryRow.origin == op.origin,
                                                             TelemetryRow.lat.is_not(None), TelemetryRow.lon.is_not(None),
                                                             TelemetryRow.ts >= op.start))
        if op.end is not None:
            q = q.where(TelemetryRow.ts <= op.end)
        row = s.execute(q.order_by(TelemetryRow.ts.desc()).limit(1)).first()
        if row is not None:
            lat, lon, source = float(row[0]), float(row[1]), "last_position"
    prop = _property(s, op.property_id)
    if prop is None or prop.origin != op.origin:
        m = get_machine(s, op.machine_id)
        prop = _property(s, m.property_id) if m is not None and m.origin == op.origin else None
        if prop is not None and prop.origin != op.origin:
            prop = None
    fictitious = op.origin == "simulated" or (fld is not None and fld.origin == "simulated")
    return {"lat": lat, "lon": lon, "source": source, "ibge": prop.ibge_municipality if prop else None,
            "tz": prop.timezone if prop else None, "fictitious": fictitious, "note": SIMULATED_NOTE if fictitious else None,
            "operation_id": op.operation_id, "origin": op.origin, "property_id": prop.property_id if prop else None}


def build_weather_router(session, viewer) -> APIRouter:
    r = APIRouter(tags=["weather"])

    @r.get("/operations/{operation_id}/weather")
    def operation_weather(operation_id: str, request: Request, _=Depends(viewer), s=Depends(session)):
        """Clima do ponto da operação: observação (estação INMET ou modelo) e previsão do período atual."""
        loc = operation_location(s, operation_id)
        out = request.app.state.weather.lookup(loc["lat"], loc["lon"], ibge=loc["ibge"], tz=loc["tz"], source=loc["source"],
                                               fictitious=loc["fictitious"], note=loc["note"])
        out["operation_id"], out["origin"] = loc["operation_id"], loc["origin"]
        return out

    @r.get("/weather")
    def weather(request: Request, lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
                ibge: str | None = Query(None, pattern=r"^\d{7}$"), tz: str | None = None, _=Depends(viewer)):
        """Clima de um ponto arbitrário (``lat``/``lon``) e/ou de um município (``ibge``)."""
        if (lat is None) != (lon is None):
            raise HTTPException(422, "informe lat e lon juntos")
        return request.app.state.weather.lookup(lat, lon, ibge=ibge, tz=tz, source="query")

    return r
