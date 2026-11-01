from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import (JSON, BigInteger, Boolean, Column, DateTime, Float, Index, Integer, String, Text,
                        TypeDecorator, UniqueConstraint, create_engine, event, select, text)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agrofleet.analytics.frame import COLS, normalize


class UTCDateTime(TypeDecorator):
    """Guarda UTC; devolve sempre datetime com tzinfo=UTC (SQLite perde o fuso)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, v, dialect):
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("datetime sem fuso")
        v = v.astimezone(timezone.utc)
        return v.replace(tzinfo=None) if dialect.name == "sqlite" else v

    def process_result_value(self, v, dialect):
        return None if v is None else (v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc))


class Base(DeclarativeBase):
    pass


BigPK = BigInteger().with_variant(Integer, "sqlite")


class TelemetryRow(Base):
    __tablename__ = "telemetry"
    id = Column(BigPK, primary_key=True, autoincrement=True)
    machine_id = Column(String(64), nullable=False)
    ts = Column(UTCDateTime, nullable=False)
    origin = Column(String(16), nullable=False, default="unknown")
    lat = Column(Float); lon = Column(Float)
    speed_ms = Column(Float); state = Column(String(16), nullable=False); gnss_fix = Column(String(16), nullable=False)
    accuracy_m = Column(Float); flow_lpm = Column(Float); pressure_bar = Column(Float); tank_level_l = Column(Float)
    consumption_l = Column(Float); working_width_m = Column(Float); application_rate_lha = Column(Float)
    error_codes = Column(JSON, nullable=False, default=list)
    sensor_quality = Column(JSON, nullable=False, default=dict)
    property_id = Column(String(64)); field_id = Column(String(64)); operator_id = Column(String(64))
    upload_id = Column(Integer)
    __table_args__ = (UniqueConstraint("machine_id", "ts", "origin", name="uq_tel_machine_ts_origin"),
                      Index("ix_tel_machine_ts", "machine_id", "ts"), Index("ix_tel_ts", "ts"))


class FieldRow(Base):
    __tablename__ = "fields"
    id = Column(String(64), primary_key=True)
    property_id = Column(String(64)); name = Column(String(128))
    origin = Column(String(16), nullable=False, default="unknown")
    geojson = Column(JSON, nullable=False)


class AlertRow(Base):
    __tablename__ = "alerts"
    id = Column(String(32), primary_key=True)
    machine_id = Column(String(64), nullable=False, index=True)
    type = Column(String(48), nullable=False); severity = Column(String(8), nullable=False)
    start = Column(UTCDateTime, nullable=False, index=True); end = Column(UTCDateTime, nullable=False)
    message = Column(Text, nullable=False); evidence = Column(JSON, nullable=False); rule = Column(String(128))
    origin = Column(String(16), nullable=False); experimental = Column(Boolean, nullable=False, default=False)


class UploadRow(Base):
    __tablename__ = "uploads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(256)); kind = Column(String(16)); origin = Column(String(16))
    created_at = Column(UTCDateTime, nullable=False); username = Column(String(64))
    committed = Column(Boolean, nullable=False, default=False)
    rows_ok = Column(Integer); rows_rejected = Column(Integer); quality = Column(JSON)


class RejectRow(Base):
    __tablename__ = "ingest_rejects"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(UTCDateTime, nullable=False); source = Column(String(16)); topic = Column(String(256)); reason = Column(Text)


class UserRow(Base):
    __tablename__ = "users"
    username = Column(String(64), primary_key=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False)


class SettingRow(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=False)


def make_engine(url: str):
    kw = {}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///:memory:"):
            kw["poolclass"] = StaticPool
        else:
            from pathlib import Path
            p = url.split("sqlite:///", 1)[-1]
            Path(p).parent.mkdir(parents=True, exist_ok=True)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    eng = create_engine(url, pool_pre_ping=True, **kw)
    return eng


def wait_for_db(engine, timeout_s: float = 90.0):
    """Espera o banco aceitar conexões (o Postgres reinicia ao fim do init na 1ª subida)."""
    import time
    t0, last = time.time(), None
    while time.time() - t0 < timeout_s:
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5)
    raise RuntimeError(f"banco indisponível após {timeout_s:.0f}s: {last}")


def init_db(engine):
    wait_for_db(engine)
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            # coluna geométrica gerada a partir de lat/lon (PostGIS) + índice espacial
            c.execute(text("ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS geom geometry(Point,4326) GENERATED ALWAYS AS "
                           "(CASE WHEN lat IS NULL OR lon IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint(lon, lat), 4326) END) STORED"))
            c.execute(text("CREATE INDEX IF NOT EXISTS ix_tel_geom ON telemetry USING GIST (geom)"))
            c.execute(text("ALTER TABLE fields ADD COLUMN IF NOT EXISTS geom geometry(Geometry,4326)"))
            c.execute(text("CREATE INDEX IF NOT EXISTS ix_fields_geom ON fields USING GIST (geom)"))


def sessionmaker_for(engine):
    return sessionmaker(engine, expire_on_commit=False)


def is_pg(session: Session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


TEL_COLS = [c.name for c in TelemetryRow.__table__.columns if c.name not in ("id", "upload_id")]


def insert_points(session: Session, points, upload_id: int | None = None) -> int:
    """Inserção idempotente por (machine_id, ts, origin): reentrega QoS 1 ou reenvio de arquivo não duplica."""
    from sqlalchemy.dialects import postgresql, sqlite
    ins = (postgresql if is_pg(session) else sqlite).insert
    n = 0
    rows = []
    for p in points:
        d = p.model_dump(mode="python")
        d["state"] = p.state.value; d["gnss_fix"] = p.gnss_fix.value
        d["sensor_quality"] = {k: v.value for k, v in p.sensor_quality.items()}
        d["upload_id"] = upload_id
        rows.append(d)
    for i in range(0, len(rows), 400):
        stmt = ins(TelemetryRow).values(rows[i:i + 400]).on_conflict_do_nothing(index_elements=["machine_id", "ts", "origin"])
        n += len(session.execute(stmt.returning(TelemetryRow.id)).all())   # rowcount não é confiável em ON CONFLICT DO NOTHING
    session.commit()
    return n


def load_frame(session: Session, machine_ids=None, start=None, end=None, origin: str | None = None, field_id=None) -> pd.DataFrame:
    q = select(*[getattr(TelemetryRow, c) for c in TEL_COLS]).order_by(TelemetryRow.machine_id, TelemetryRow.ts)
    if machine_ids:
        q = q.where(TelemetryRow.machine_id.in_(machine_ids))
    if start is not None:
        q = q.where(TelemetryRow.ts >= start)
    if end is not None:
        q = q.where(TelemetryRow.ts <= end)
    if origin and origin != "all":
        q = q.where(TelemetryRow.origin == origin)
    if field_id:
        q = q.where(TelemetryRow.field_id == field_id)
    rows = session.execute(q).mappings().all()
    df = pd.DataFrame(rows, columns=TEL_COLS)
    if df.empty:
        return normalize(pd.DataFrame(columns=COLS))
    sq = df["sensor_quality"].apply(lambda d: d or {})
    for s in ("gnss", "speed", "flow", "pressure", "tank_level"):
        df[f"q_{s}"] = sq.apply(lambda d, s=s: d.get(s))
    df["error_codes"] = df["error_codes"].apply(lambda v: v or [])
    return normalize(df)


def get_setting(session: Session, key: str, default=None):
    r = session.get(SettingRow, key)
    return r.value if r else default


def set_setting(session: Session, key: str, value):
    r = session.get(SettingRow, key)
    if r:
        r.value = value
    else:
        session.add(SettingRow(key=key, value=value))
    session.commit()
