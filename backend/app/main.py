"""API AgroFleet Intelligence.

Somente LEITURA e ANÁLISE de telemetria. Não existe endpoint, tópico ou função que envie comando a uma máquina
(direção, freio, aplicação). Alertas são informativos.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import math
from contextlib import asynccontextmanager
from dataclasses import asdict, fields as dc_fields
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select, text

from agrofleet import adapters as AD
from agrofleet.adapters.mqtt import MqttSource
from agrofleet.analytics import anomalies as A
from agrofleet.analytics import roi as R
from agrofleet.analytics.coverage import CoverageConfig, coverage
from agrofleet.analytics.metrics import filter_period, machine_metrics, report
from agrofleet.quality import assess
from agrofleet.schema import SCHEMA_VERSION
from agrofleet.simulator.fleet import simulate

from .auth import ROLES, Tokens, hash_password, verify_password
from .config import Settings
from .db import (AlertRow, FieldRow, RejectRow, TelemetryRow, UploadRow, UserRow, get_setting, init_db, insert_points,
                 is_pg, load_frame, make_engine, sessionmaker_for, set_setting)
from .live import Live, alert_config, alert_to_dict, upsert_alert


def clean(o):
    """Substitui NaN/Inf por None e converte tipos numpy (JSON estrito não aceita NaN)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(float(o)) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if o is pd.NaT:
        return None
    return o


class CleanJSON(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(clean(content), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def _utc(x: datetime | None):
    if x is None:
        return None
    return x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x.astimezone(timezone.utc)


LIMITS = {
    "scope": "Telemetria, monitoramento e auditoria. NÃO controla direção, freio nem aplicação; NÃO gera comandos para máquinas.",
    "alerts": A.INFO_NOTE,
    "data_origin": "Todo dado carrega origin = simulated | real | unknown. 'real' é uma declaração de quem enviou: o sistema não consegue provar procedência.",
    "pressure": A.PRESSURE_NOTE,
    "coverage": "Área coberta é ESTIMADA por geometria (largura efetiva × trajetória GNSS). Não interpola sobre perda de GNSS ou lacunas.",
    "ml": "IsolationForest é não supervisionado e experimental; 'atípico' não é 'defeituoso'.",
    "roi": "O ROI depende só das premissas informadas; o cenário de exemplo é ilustrativo, não é benchmark de mercado.",
    "not_validated": ["telemetria de máquina real", "ISOXML/TASKDATA", "API de fabricante", "CAN/ISOBUS", "Docker Desktop (apenas Podman foi executado)",
                      "segurança funcional (ISO 13849/25119): fora de escopo"],
}


class RoiBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict
    sensitivity_pct: float | None = 20.0
    grid: dict | None = None   # {"x": nome, "y": nome, "xs": [...], "ys": [...]}
    break_even: bool = True


def create_app(settings: Settings | None = None) -> FastAPI:
    st = settings or Settings()
    engine = make_engine(st.database_url)
    init_db(engine)
    SF = sessionmaker_for(engine)
    tokens = Tokens(st.auth_secret, st.token_ttl_s)
    live = Live(SF)
    mqtt_ref: dict = {}

    with SF() as s:
        for role, (user, pw) in st.users.items():
            if pw and not s.get(UserRow, user):
                s.add(UserRow(username=user, password_hash=hash_password(pw), role=role))
        s.commit()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await live.start()
        if st.mqtt_host:
            src = MqttSource(st.mqtt_host, st.mqtt_port, username=st.mqtt_user, password=st.mqtt_password, cafile=st.mqtt_cafile,
                             on_point=live.push, on_reject=live.push_reject)
            mqtt_ref["src"] = src
            await asyncio.get_running_loop().run_in_executor(None, src.start, 5.0)
        yield
        if mqtt_ref.get("src"):
            mqtt_ref["src"].stop()
        await live.stop()

    app = FastAPI(title="AgroFleet Intelligence API", version="1.0.0", default_response_class=CleanJSON, lifespan=lifespan,
                  description="Telemetria e analytics de pulverizadores terrestres. Somente leitura/análise: nenhum comando de controle. "
                              "Papéis: viewer < analyst < admin (JWT Bearer).")
    if st.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=st.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.state.settings, app.state.SF, app.state.live, app.state.engine, app.state.tokens = st, SF, live, engine, tokens

    # ---------- dependências ----------
    def session():
        with SF() as s:
            yield s

    def user_from(request: Request) -> dict:
        h = request.headers.get("authorization", "")
        if not h.lower().startswith("bearer "):
            raise HTTPException(401, "token ausente", headers={"WWW-Authenticate": "Bearer"})
        try:
            return tokens.verify(h[7:])
        except Exception:
            raise HTTPException(401, "token inválido ou expirado", headers={"WWW-Authenticate": "Bearer"})

    def need(role: str):
        def dep(u: dict = Depends(user_from)):
            if ROLES.get(u.get("role"), 0) < ROLES[role]:
                raise HTTPException(403, f"requer papel '{role}' ou superior")
            return u
        return dep

    viewer, analyst, admin = need("viewer"), need("analyst"), need("admin")

    # ---------- público ----------
    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "schema_version": SCHEMA_VERSION, "db": engine.dialect.name, "postgis": engine.dialect.name == "postgresql",
                "mqtt": {"enabled": bool(st.mqtt_host), "connected": bool(mqtt_ref.get("src") and mqtt_ref["src"].connected.is_set()),
                         **(mqtt_ref["src"].stats if mqtt_ref.get("src") else {})},
                "ingest": live.stats, "auth_secret_generated": tokens.generated}

    @app.get("/limits", tags=["meta"])
    def limits():
        return {**LIMITS, "adapters": AD.REGISTRY}

    @app.post("/auth/login", tags=["auth"])
    def login(body: dict, s=Depends(session)):
        u = s.get(UserRow, str(body.get("username", "")))
        if not u or not verify_password(str(body.get("password", "")), u.password_hash):
            raise HTTPException(401, "credenciais inválidas")
        return {"access_token": tokens.issue(u.username, u.role), "token_type": "bearer", "role": u.role, "expires_in": st.token_ttl_s}

    @app.get("/auth/me", tags=["auth"])
    def me(u=Depends(viewer)):
        return {"username": u["sub"], "role": u["role"]}

    # ---------- frota ----------
    @app.get("/summary", tags=["fleet"])
    def summary(origin: str = "all", _=Depends(viewer), s=Depends(session)):
        q = select(TelemetryRow.origin, func.count(), func.count(func.distinct(TelemetryRow.machine_id)), func.min(TelemetryRow.ts), func.max(TelemetryRow.ts)) \
            .group_by(TelemetryRow.origin)
        rows = s.execute(q).all()
        by = {r[0]: {"samples": r[1], "machines": r[2], "start": r[3].isoformat(), "end": r[4].isoformat()} for r in rows}
        return {"by_origin": by, "any_simulated": "simulated" in by, "any_real": "real" in by, "any_unknown": "unknown" in by,
                "alerts": s.execute(select(func.count()).select_from(AlertRow)).scalar(), "live_clients": len(live.clients)}

    @app.get("/machines", tags=["fleet"])
    def machines(origin: str = "all", _=Depends(viewer), s=Depends(session)):
        sub = select(TelemetryRow.machine_id, TelemetryRow.origin, func.max(TelemetryRow.ts).label("mx"), func.count().label("n")) \
            .group_by(TelemetryRow.machine_id, TelemetryRow.origin)
        if origin != "all":
            sub = sub.where(TelemetryRow.origin == origin)
        out = []
        for mid, org, mx, n in s.execute(sub).all():
            last = s.execute(select(TelemetryRow).where(TelemetryRow.machine_id == mid, TelemetryRow.origin == org, TelemetryRow.ts == mx)).scalars().first()
            nal = s.execute(select(func.count()).select_from(AlertRow).where(AlertRow.machine_id == mid, AlertRow.origin == org)).scalar()
            out.append({"machine_id": mid, "origin": org, "samples": n, "last_ts": mx.isoformat(), "live": live.is_live(mid, st.live_window_s),
                        "state": last.state, "gnss_fix": last.gnss_fix, "lat": last.lat, "lon": last.lon, "speed_ms": last.speed_ms,
                        "flow_lpm": last.flow_lpm, "pressure_bar": last.pressure_bar, "tank_level_l": last.tank_level_l,
                        "field_id": last.field_id, "operator_id": last.operator_id, "alerts": nal})
        return sorted(out, key=lambda r: (r["machine_id"], r["origin"]))

    @app.get("/machines/{machine_id}/telemetry", tags=["fleet"])
    def telemetry(machine_id: str, start: datetime | None = None, end: datetime | None = None, origin: str = "all",
                  limit: int = Query(5000, le=50000), bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
                  _=Depends(viewer), s=Depends(session)):
        q = select(TelemetryRow).where(TelemetryRow.machine_id == machine_id).order_by(TelemetryRow.ts).limit(limit)
        if start:
            q = q.where(TelemetryRow.ts >= _utc(start))
        if end:
            q = q.where(TelemetryRow.ts <= _utc(end))
        if origin != "all":
            q = q.where(TelemetryRow.origin == origin)
        if bbox:
            try:
                x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
            except ValueError:
                raise HTTPException(422, "bbox inválido")
            if is_pg(s):   # índice espacial GIST do PostGIS
                q = q.where(text("geom && ST_MakeEnvelope(:x0,:y0,:x1,:y1,4326)").bindparams(x0=x0, y0=y0, x1=x1, y1=y1))
            else:
                q = q.where(TelemetryRow.lon.between(x0, x1), TelemetryRow.lat.between(y0, y1))
        cols = [c.name for c in TelemetryRow.__table__.columns if c.name not in ("id", "upload_id")]
        return [{c: (getattr(r, c).isoformat() if c == "ts" else getattr(r, c)) for c in cols} for r in s.execute(q).scalars()]

    @app.get("/machines/{machine_id}/track", tags=["fleet"])
    def track(machine_id: str, start: datetime | None = None, end: datetime | None = None, origin: str = "all",
              _=Depends(viewer), s=Depends(session)):
        """Trilha como LineStrings por estado. Sem posição => a linha é interrompida (nada é interpolado)."""
        df = load_frame(s, [machine_id], _utc(start), _utc(end), origin)
        feats, cur, cur_state = [], [], None

        def flush():
            if len(cur) >= 2:
                feats.append({"type": "Feature", "properties": {"machine_id": machine_id, "state": cur_state}, "geometry": {"type": "LineString", "coordinates": list(cur)}})
        for r in df.itertuples():
            if pd.isna(r.lat):
                flush(); cur = []; cur_state = None; continue
            if r.state != cur_state:
                last = cur[-1] if cur else None
                flush(); cur = [last] if last else []; cur_state = r.state
            cur.append([r.lon, r.lat])
        flush()
        return {"type": "FeatureCollection", "features": feats, "origins": sorted(set(df["origin"])) if len(df) else []}

    @app.get("/playback", tags=["fleet"])
    def playback(machine_ids: str | None = None, start: datetime | None = None, end: datetime | None = None, step_s: float = Query(5.0, ge=1, le=3600),
                 origin: str = "all", _=Depends(viewer), s=Depends(session)):
        """Quadros para reprodução temporal. Posição ausente permanece null (a UI mostra 'sem GNSS')."""
        ids = machine_ids.split(",") if machine_ids else None
        df = load_frame(s, ids, _utc(start), _utc(end), origin)
        out = {}
        for mid, g in df.groupby("machine_id"):
            t = (g["ts"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()
            bucket = np.floor((t - t[0]) / step_s)
            keep = np.r_[True, np.diff(bucket) > 0]
            g = g[keep]
            out[mid] = {"t": ((g["ts"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()).tolist(), "lon": g["lon"].tolist(), "lat": g["lat"].tolist(),
                        "state": g["state"].tolist(), "speed_ms": g["speed_ms"].tolist(), "flow_lpm": g["flow_lpm"].tolist(),
                        "gnss_fix": g["gnss_fix"].tolist(), "origin": g["origin"].tolist()}
        tmin = min((v["t"][0] for v in out.values()), default=None)
        tmax = max((v["t"][-1] for v in out.values()), default=None)
        return {"step_s": step_s, "t_start": tmin, "t_end": tmax, "machines": out}

    # ---------- talhões ----------
    @app.get("/fields", tags=["fields"])
    def fields(_=Depends(viewer), s=Depends(session)):
        return [{"id": f.id, "name": f.name, "property_id": f.property_id, "origin": f.origin, "geojson": f.geojson} for f in s.execute(select(FieldRow)).scalars()]

    @app.post("/fields/upload", tags=["fields"])
    async def fields_upload(file: UploadFile = File(...), origin: str = Form("unknown"), property_id: str | None = Form(None),
                            _=Depends(analyst), s=Depends(session)):
        if origin not in ("simulated", "real", "unknown"):
            raise HTTPException(422, "origin inválida")
        raw = await file.read()
        try:
            geos = AD.read_field_boundaries(raw)
        except (AD.AdapterError, KeyError, ValueError) as e:
            raise HTTPException(422, f"limites inválidos: {e}")
        for fid, g in geos.items():
            row = s.get(FieldRow, fid) or FieldRow(id=fid)
            row.name, row.property_id, row.origin, row.geojson = fid, property_id, origin, g
            s.add(row)
        s.commit()
        if is_pg(s):
            for fid in geos:
                s.execute(text("UPDATE fields SET geom = ST_SetSRID(ST_GeomFromGeoJSON(CAST(geojson AS text)),4326) WHERE id=:i"), {"i": fid})
            s.commit()
        return {"fields": list(geos), "origin": origin}

    # ---------- analytics ----------
    def _ids(machine_ids):
        return machine_ids.split(",") if machine_ids else None

    @app.get("/metrics/{machine_id}", tags=["analytics"])
    def metrics(machine_id: str, start: datetime | None = None, end: datetime | None = None, origin: str = "all",
                hourly_cost: float | None = None, product_cost_per_l: float | None = None, _=Depends(viewer), s=Depends(session)):
        df = load_frame(s, [machine_id], _utc(start), _utc(end), origin)
        if df.empty:
            raise HTTPException(404, "sem telemetria para o filtro")
        m = machine_metrics(df)
        if hourly_cost is not None:
            m["cost"] = R.operational_cost_per_ha(m, hourly_cost, product_cost_per_l)
        return clean(m)

    @app.get("/coverage", tags=["analytics"])
    def cov(machine_ids: str | None = None, field_id: str | None = None, start: datetime | None = None, end: datetime | None = None,
            origin: str = "all", max_accuracy_m: float | None = 5.0, _=Depends(viewer), s=Depends(session)):
        df = load_frame(s, _ids(machine_ids), _utc(start), _utc(end), origin)
        if df.empty:
            raise HTTPException(404, "sem telemetria para o filtro")
        fld = s.get(FieldRow, field_id) if field_id else None
        if field_id and not fld:
            raise HTTPException(404, "talhão desconhecido")
        r = coverage(df, CoverageConfig(max_accuracy_m=max_accuracy_m), fld.geojson if fld else None)
        return clean({"summary": r.summary(), "union": r.union_geojson, "overlap": r.overlap_geojson, "field": fld.geojson if fld else None,
                "origins": sorted(set(df["origin"])), "machines": sorted(set(df["machine_id"])),
                "definition": {"gross": "Σ comprimento×largura dos segmentos em trabalho", "unique": "área da união das faixas",
                               "excess": "bruta − única", "overlap_region": "área coberta 2+ vezes"}})

    @app.get("/reports", tags=["analytics"])
    def reports(group_by: str = "machine", start: datetime | None = None, end: datetime | None = None, origin: str = "all",
                format: str = "json", _=Depends(viewer), s=Depends(session)):
        df = load_frame(s, None, None, None, origin)
        try:
            rep = report(df, group_by, _utc(start), _utc(end))
        except ValueError as e:
            raise HTTPException(422, str(e))
        if format == "csv":
            key = [k for k in rep["rows"][0] if k not in ("sem_valor", "machines", "origins", "time_h", "coverage")][0] if rep["rows"] else group_by
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow([group_by, "origins", "n_samples", "working_h", "idle_h", "engaged_h", "distance_km", "volume_l", "gross_ha", "unique_ha",
                        "excess_ha", "overlap_pct_of_gross", "unique_ha_per_working_h"])
            for r in rep["rows"]:
                c = r["coverage"]
                w.writerow([r.get(next(iter(r))), "|".join(r["origins"]), r["n_samples"], f"{r['time_h']['working']:.4f}", f"{r['time_h']['idle']:.4f}",
                            f"{r['engaged_h']:.4f}", f"{r['distance_km']:.3f}", "" if r["volume_l"] is None else f"{r['volume_l']:.1f}",
                            f"{c['gross_ha']:.3f}", f"{c['unique_ha']:.3f}", f"{c['excess_ha']:.3f}",
                            "" if c["overlap_pct_of_gross"] is None else f"{c['overlap_pct_of_gross']:.2f}",
                            "" if r["unique_ha_per_working_h"] is None else f"{r['unique_ha_per_working_h']:.3f}"])
            return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=report_{group_by}.csv"})
        return clean(rep)

    # ---------- alertas ----------
    @app.get("/alerts", tags=["alerts"])
    def alerts(machine_id: str | None = None, type: str | None = None, origin: str = "all", limit: int = Query(500, le=5000),
               _=Depends(viewer), s=Depends(session)):
        q = select(AlertRow).order_by(AlertRow.start.desc()).limit(limit)
        if machine_id:
            q = q.where(AlertRow.machine_id == machine_id)
        if type:
            q = q.where(AlertRow.type == type)
        if origin != "all":
            q = q.where(AlertRow.origin == origin)
        return [alert_to_dict(r) for r in s.execute(q).scalars()]

    @app.post("/alerts/recompute", tags=["alerts"])
    def recompute(machine_ids: str | None = None, start: datetime | None = None, end: datetime | None = None, origin: str = "all",
                  with_ml: bool = False, replace: bool = True, _=Depends(analyst), s=Depends(session)):
        df = load_frame(s, _ids(machine_ids), _utc(start), _utc(end), origin)
        cfg = alert_config(s)
        cfg.use_iforest = with_ml
        res = A.detect(df, cfg)
        if replace and not df.empty:
            for mid, g in df.groupby("machine_id"):
                s.execute(delete(AlertRow).where(AlertRow.machine_id == mid, AlertRow.start >= g["ts"].min().to_pydatetime(),
                                                 AlertRow.end <= g["ts"].max().to_pydatetime()))
            s.commit()
        for a in res.alerts:
            upsert_alert(s, a)
        s.commit()
        return clean({"alerts": len(res.alerts), "skipped_rules": res.skipped_rules, "config": asdict(cfg)})

    @app.get("/config/alerts", tags=["alerts"])
    def get_alert_cfg(_=Depends(viewer), s=Depends(session)):
        return asdict(alert_config(s))

    @app.put("/config/alerts", tags=["alerts"])
    def put_alert_cfg(body: dict, _=Depends(admin), s=Depends(session)):
        ok = {f.name for f in dc_fields(A.AnomalyConfig)}
        bad = set(body) - ok
        if bad:
            raise HTTPException(422, f"parâmetros desconhecidos: {sorted(bad)}")
        try:
            A.AnomalyConfig(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in body.items()})
        except TypeError as e:
            raise HTTPException(422, str(e))
        set_setting(s, "alert_config", body)
        return asdict(alert_config(s))

    # ---------- upload ----------
    @app.post("/uploads", tags=["ingest"])
    async def upload(file: UploadFile = File(...), kind: str = Form(...), origin: str = Form("unknown"), machine_id: str | None = Form(None),
                     assume_tz: str | None = Form(None), units: str | None = Form(None), commit: bool = Form(False),
                     u=Depends(analyst), s=Depends(session)):
        if origin not in ("simulated", "real", "unknown"):
            raise HTTPException(422, "origin deve ser simulated|real|unknown")
        if kind == "isoxml":
            raise HTTPException(501, f"ISOXML/TASKDATA não implementado: {AD.isoxml.REASON}")
        raw = await file.read(st.max_upload_mb * 1024 * 1024 + 1)
        if len(raw) > st.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"arquivo maior que {st.max_upload_mb} MB")
        try:
            uu = json.loads(units) if units else None
        except json.JSONDecodeError:
            raise HTTPException(422, "units deve ser JSON, ex.: {\"speed\":\"km/h\"}")
        try:
            if kind == "csv":
                res = AD.read_csv(raw, origin=origin, assume_tz=assume_tz, units=uu, machine_id=machine_id)
            elif kind == "json":
                res = AD.read_json(raw, origin=origin, assume_tz=assume_tz, units=uu, machine_id=machine_id)
            elif kind in ("geojson", "gpx"):
                if not machine_id:
                    raise HTTPException(422, "machine_id é obrigatório para GeoJSON/GPX (o arquivo não traz a identidade da máquina)")
                fn = AD.read_geojson if kind == "geojson" else AD.read_gpx
                res = fn(raw, machine_id=machine_id, origin=origin, **({"assume_tz": assume_tz} if kind == "geojson" else {}))
            else:
                raise HTTPException(422, "kind deve ser csv|json|geojson|gpx|isoxml")
        except AD.AdapterError as e:
            raise HTTPException(422, str(e))
        q = assess(res)
        up = UploadRow(filename=(file.filename or "")[:250], kind=kind, origin=origin, created_at=datetime.now(timezone.utc), username=u["sub"],
                       committed=False, rows_ok=len(res.points), rows_rejected=res.n_rejected_total, quality=clean(q))
        s.add(up)
        s.commit()
        stored = None
        if commit:
            if q["verdict"] == "blocking" or not res.points:
                raise HTTPException(422, {"msg": "qualidade bloqueante: nada foi gravado", "quality": clean(q)})
            stored = insert_points(s, res.points, up.id)
            up.committed = True
            s.commit()
        return clean({"upload_id": up.id, "committed": bool(commit), "stored": stored, "duplicates_ignored": (len(res.points) - stored) if stored is not None else None,
                      "quality": q})

    @app.get("/uploads", tags=["ingest"])
    def uploads(_=Depends(analyst), s=Depends(session)):
        return [{"id": r.id, "filename": r.filename, "kind": r.kind, "origin": r.origin, "created_at": r.created_at.isoformat(), "user": r.username,
                 "committed": r.committed, "rows_ok": r.rows_ok, "rows_rejected": r.rows_rejected, "verdict": (r.quality or {}).get("verdict")}
                for r in s.execute(select(UploadRow).order_by(UploadRow.id.desc()).limit(100)).scalars()]

    @app.get("/ingest/rejects", tags=["ingest"])
    def rejects(_=Depends(analyst), s=Depends(session)):
        return [{"ts": r.ts.isoformat(), "source": r.source, "topic": r.topic, "reason": r.reason}
                for r in s.execute(select(RejectRow).order_by(RejectRow.id.desc()).limit(200)).scalars()]

    # ---------- demo / manutenção ----------
    @app.post("/demo/load", tags=["demo"])
    def demo_load(seed: int = 7, _=Depends(admin), s=Depends(session)):
        """Gera e grava a frota SIMULADA (origin=simulated) + talhões simulados + verdade do simulador."""
        pts, truth = simulate(seed=seed)
        n = insert_points(s, pts)
        for fid, g in truth.fields.items():
            row = s.get(FieldRow, fid) or FieldRow(id=fid)
            row.name, row.property_id, row.origin, row.geojson = fid, "PROP-SIM-1", "simulated", g
            s.add(row)
        s.commit()
        if is_pg(s):
            s.execute(text("UPDATE fields SET geom = ST_SetSRID(ST_GeomFromGeoJSON(CAST(geojson AS text)),4326) WHERE origin='simulated'"))
            s.commit()
        set_setting(s, "sim_truth", {"events": truth.events, "areas": truth.areas, "meta": truth.meta})
        return {"stored": n, "samples": len(pts), "origin": "simulated", "seed": seed, "events_injected": len(truth.events)}

    @app.get("/demo/evaluation", tags=["demo"])
    def demo_eval(_=Depends(viewer), s=Depends(session)):
        """Compara alertas com os eventos injetados. Vale SOMENTE para o dado simulado (não é desempenho em campo)."""
        tr = get_setting(s, "sim_truth")
        if not tr:
            raise HTTPException(404, "nenhum dado simulado carregado")
        rows = s.execute(select(AlertRow).where(AlertRow.origin == "simulated")).scalars().all()
        alerts_ = [A.Alert(r.machine_id, r.type, pd.Timestamp(r.start), pd.Timestamp(r.end), r.message, r.evidence, r.rule or "", r.severity, r.origin,
                           True, r.experimental) for r in rows]
        ev = A.evaluate(alerts_, tr["events"])
        return clean({**ev, "origin": "simulated", "caveat": "Eventos injetados por nós, com regras escritas conhecendo o simulador: otimista por construção.",
                      "areas_truth": tr["areas"]})

    @app.delete("/data", tags=["demo"])
    def delete_data(origin: str, _=Depends(admin), s=Depends(session)):
        if origin not in ("simulated", "real", "unknown"):
            raise HTTPException(422, "origin inválida")
        n = s.execute(delete(TelemetryRow).where(TelemetryRow.origin == origin)).rowcount
        a = s.execute(delete(AlertRow).where(AlertRow.origin == origin)).rowcount
        f = s.execute(delete(FieldRow).where(FieldRow.origin == origin)).rowcount
        s.commit()
        return {"telemetry": n, "alerts": a, "fields": f}

    @app.get("/postgis/check/{machine_id}", tags=["analytics"])
    def postgis_check(machine_id: str, field_id: str | None = None, _=Depends(viewer), s=Depends(session)):
        """Verificação INDEPENDENTE (geodésica, PostGIS/geography) do comprimento da trilha e da área de talhão,
        para comparar com o cálculo em UTM do Python. Só disponível com PostgreSQL+PostGIS."""
        if not is_pg(s):
            raise HTTPException(501, "requer PostgreSQL com PostGIS (banco atual: SQLite)")
        L = s.execute(text("SELECT ST_Length(ST_MakeLine(geom ORDER BY ts)::geography) FROM telemetry WHERE machine_id=:m AND geom IS NOT NULL"), {"m": machine_id}).scalar()
        out = {"track_length_m_geography_all_points": L}
        if field_id:
            out["field_area_m2_geography"] = s.execute(text("SELECT ST_Area(geom::geography) FROM fields WHERE id=:i"), {"i": field_id}).scalar()
            out["points_within_field"] = s.execute(text("SELECT count(*) FROM telemetry t JOIN fields f ON ST_Within(t.geom, f.geom) WHERE t.machine_id=:m AND f.id=:i"),
                                                   {"m": machine_id, "i": field_id}).scalar()
        return clean(out)

    # ---------- ROI ----------
    def _roi_inputs(d: dict) -> R.RoiInputs:
        ok = {f.name for f in dc_fields(R.RoiInputs)}
        bad = set(d) - ok
        if bad:
            raise HTTPException(422, f"premissas desconhecidas: {sorted(bad)}")
        try:
            return R.RoiInputs(**d)
        except TypeError as e:
            raise HTTPException(422, str(e))

    @app.get("/roi/example", tags=["roi"])
    def roi_example(_=Depends(viewer)):
        return {"inputs": asdict(R.EXAMPLE_INPUTS), "note": "Cenário ILUSTRATIVO; não é benchmark de mercado. Troque todos os valores."}

    @app.post("/roi", tags=["roi"])
    def roi(body: RoiBody, _=Depends(viewer)):
        i = _roi_inputs(body.inputs)
        try:
            out = {"result": R.compute(i)}
            if body.sensitivity_pct:
                out["sensitivity"] = R.sensitivity(i, body.sensitivity_pct)
            if body.grid:
                out["grid"] = R.grid(i, **body.grid)
            if body.break_even:
                out["break_even"] = {n: R.break_even(i, n, getattr(i, n) * 0.05, getattr(i, n) * 10) for n in
                                     ("area_ha_per_year", "auto_productivity_ha_per_h", "capex_per_machine", "product_cost_per_ha", "baseline_overlap_pct")
                                     if getattr(i, n)}
                out["break_even_note"] = "Valor da premissa (demais fixas) em que o benefício líquido anual é zero; null = não cruza zero entre 5% e 1000% do valor atual."
        except (ValueError, TypeError, AttributeError) as e:
            raise HTTPException(422, str(e))
        return out

    @app.post("/roi/from-telemetry", tags=["roi"])
    def roi_from_telemetry(body: dict, origin: str = "all", machine_ids: str | None = None, _=Depends(viewer), s=Depends(session)):
        """Preenche produtividade e sobreposição da autônoma a partir da telemetria (com proveniência) e deixa o resto a cargo do usuário."""
        df = load_frame(s, _ids(machine_ids), None, None, origin)
        if df.empty:
            raise HTTPException(404, "sem telemetria")
        ms = [machine_metrics(g) for _, g in df.groupby("machine_id")]
        prod = [m["productivity"]["unique_ha_per_working_h"] for m in ms if m.get("productivity", {}).get("unique_ha_per_working_h")]
        ovl = [m["coverage"]["overlap_pct_of_gross"] for m in ms if m.get("coverage", {}).get("overlap_pct_of_gross") is not None]
        d = dict(body)
        prov = dict(d.get("provenance", {}))
        orgs = sorted(set(df["origin"]))
        if prod:
            d["auto_productivity_ha_per_h"] = float(np.mean(prod))
            prov["auto_productivity_ha_per_h"] = {"source": "measured_telemetry", "origin": orgs, "note": f"média de {len(prod)} máquinas (ha únicos / h em trabalho)"}
        if ovl:
            d["auto_overlap_pct"] = float(np.mean(ovl))
            prov["auto_overlap_pct"] = {"source": "measured_telemetry", "origin": orgs, "note": f"média de {len(ovl)} máquinas (excesso/bruta)"}
        d["provenance"] = prov
        i = _roi_inputs(d)
        try:
            return clean({"result": R.compute(i), "sensitivity": R.sensitivity(i, 20.0), "telemetry_origins": orgs})
        except ValueError as e:
            raise HTTPException(422, str(e))

    # ---------- WebSocket ----------
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        """Primeira mensagem: {"type":"auth","token":"..."} em até 5 s. Depois recebe 'telemetry' e 'alert' (somente servidor->cliente)."""
        await sock.accept()
        try:
            first = await asyncio.wait_for(sock.receive_json(), 5)
            claims = tokens.verify(first.get("token", "")) if first.get("type") == "auth" else None
        except Exception:
            claims = None
        if not claims:
            await sock.close(code=4401)
            return
        await sock.send_json({"type": "hello", "role": claims["role"], "schema_version": SCHEMA_VERSION})
        live.clients.add(sock)
        try:
            while True:
                await sock.receive_text()   # ignora o que o cliente enviar; não há comandos
        except WebSocketDisconnect:
            pass
        finally:
            live.clients.discard(sock)

    return app
