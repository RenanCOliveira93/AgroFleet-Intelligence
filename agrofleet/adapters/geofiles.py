"""Adaptadores GeoJSON e GPX (trilhas e limites de talhão).

Trilha sem timestamp por ponto NÃO é aceita como telemetria: não inventamos tempo (nem velocidade) para uma geometria.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .base import AdapterError, AdapterResult
from .tabular import read_rows


def _iso(t):
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
    return str(t)


def read_geojson(source: str | bytes, *, machine_id: str, origin="unknown", assume_tz=None, **kw) -> AdapterResult:
    raw = source.decode("utf-8-sig") if isinstance(source, bytes) else source
    try:
        gj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AdapterError(f"GeoJSON ilegível: {e}")
    feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj] if gj.get("type") == "Feature" else None
    if feats is None:
        raise AdapterError("GeoJSON deve ser Feature ou FeatureCollection")
    rows, rej = [], []
    for n, f in enumerate(feats, 1):
        geom, props = f.get("geometry") or {}, dict(f.get("properties") or {})
        t = geom.get("type")
        if t == "Point":
            lon, lat = geom["coordinates"][:2]
            rows.append({**props, "lat": lat, "lon": lon})
        elif t in ("LineString", "MultiLineString"):
            lines = [geom["coordinates"]] if t == "LineString" else geom["coordinates"]
            times = props.get("times") or (props.get("coordinateProperties") or {}).get("times") \
                or (f.get("coordinateProperties") or {}).get("times")
            if t == "MultiLineString" and times and isinstance(times[0], list):
                times = [x for sub in times for x in sub]
            flat = [c for ln in lines for c in ln]
            if not times or len(times) != len(flat):
                rej.append((n, f"{t} sem tempo por vértice (properties.times / coordinateProperties.times): não inventamos timestamps"))
                continue
            for c, tt in zip(flat, times):
                rows.append({"lat": c[1], "lon": c[0], "time": _iso(tt)})
        else:
            rej.append((n, f"geometria {t!r} não é trilha (Point/LineString)"))
    cols = sorted({k for r in rows for k in r})
    if rows:
        res = read_rows(rows, cols, origin=origin, assume_tz=assume_tz, machine_id=machine_id, source="geojson", **kw)
    else:
        res = AdapterResult()
    res.n_input = len(feats)
    for n, why in rej:
        res.reject(n, why)
    res.warnings.append("GeoJSON só traz posição/tempo (e propriedades que você incluir): o resto fica None.")
    return res


def read_gpx(source: str | bytes, *, machine_id: str, origin="unknown", **kw) -> AdapterResult:
    import gpxpy
    raw = source.decode("utf-8-sig") if isinstance(source, bytes) else source
    try:
        gpx = gpxpy.parse(raw)
    except Exception as e:
        raise AdapterError(f"GPX ilegível: {e}")
    rows, n_no_time, n = [], 0, 0
    for trk in gpx.tracks:
        for seg in trk.segments:
            for p in seg.points:
                n += 1
                if p.time is None:
                    n_no_time += 1
                    continue
                t = p.time if p.time.tzinfo else p.time.replace(tzinfo=timezone.utc)   # GPX define tempo em UTC
                rows.append({"time": t.isoformat(), "lat": p.latitude, "lon": p.longitude, "speed": p.speed})
    res = read_rows(rows, ["time", "lat", "lon", "speed"], origin=origin, machine_id=machine_id, source="gpx", **kw) \
        if rows else AdapterResult()
    res.n_input = n
    if n_no_time:
        res.n_rejected_total += n_no_time
        res.rejected.append({"row": 0, "reason": f"{n_no_time} pontos sem <time> descartados (não inventamos timestamps)", "raw": None})
    res.warnings.append("GPX só traz posição e tempo (e speed se <speed> existir); demais campos ficam None.")
    return res


def read_field_boundaries(source: str | bytes) -> dict:
    """GeoJSON de limites de talhão -> {field_id: geometry}. ``properties.id`` (ou ``field_id``/``name``) é obrigatório."""
    from shapely.geometry import shape
    raw = source.decode("utf-8-sig") if isinstance(source, bytes) else source
    try:
        gj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AdapterError(f"GeoJSON ilegível: {e}")
    feats = gj["features"] if gj.get("type") == "FeatureCollection" else [gj]
    out = {}
    for f in feats:
        p = f.get("properties") or {}
        fid = p.get("id") or p.get("field_id") or p.get("name")
        if not fid:
            raise AdapterError("feature de talhão sem properties.id/field_id/name")
        g = shape(f["geometry"])
        if g.geom_type not in ("Polygon", "MultiPolygon") or not g.is_valid:
            raise AdapterError(f"talhão {fid!r}: geometria inválida ou não poligonal")
        out[str(fid)] = f["geometry"]
    return out
