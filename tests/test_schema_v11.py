"""Etapa 1 — contrato de telemetria 1.1 (campos opcionais de motor e energia).

O que se prova aqui:
- compatibilidade: payloads 1.0 continuam aceitos e os campos novos ficam None (nunca 0);
- validação de faixa/finitude dos campos novos; sinal de potência de bateria permitido (recarga/regeneração);
- conversão explícita de unidades no CSV e suspeita de unidade sinalizada (não corrigida);
- persistência (SQLite), migração aditiva de banco criado no schema 1.0 e exposição na API/WebSocket.
"""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import MetaData, Table, UniqueConstraint, inspect, text

from agrofleet.adapters import AdapterError, read_csv, read_json
from agrofleet.adapters.mqtt import parse_message
from agrofleet.analytics.frame import to_frame
from agrofleet.quality import assess
from agrofleet.schema import ENGINE_ENERGY_FIELDS, SCHEMA_VERSION, TOPIC_PREFIX, TelemetryPoint, unwrap, wrap
from backend.app.config import Settings
from backend.app.db import TelemetryRow, add_missing_columns, init_db, insert_points, load_frame, make_engine, sessionmaker_for
from backend.app.live import point_brief
from backend.app.main import create_app

EX = Path(__file__).resolve().parents[1] / "payload_examples"
T0 = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
NEW = list(ENGINE_ENERGY_FIELDS)


def raw(name):
    return (EX / name).read_bytes()


def pt(**kw):
    return TelemetryPoint(**{"machine_id": "M-1", "ts": T0, "origin": "simulated", **kw})


# --- contrato ---------------------------------------------------------------------------------------------------

def test_versao_e_topico():
    assert SCHEMA_VERSION == "1.1" and TOPIC_PREFIX == "agrofleet/v1"       # minor: mesmo tópico, mesmo major
    assert wrap(pt(), "x")["schema_version"] == "1.1"


@pytest.mark.parametrize("name,topic", [("mqtt_envelope_valid.json", "FIELD-TEST-1"), ("mqtt_envelope_valid_sparse.json", "FIELD-TEST-1")])
def test_payload_1_0_continua_valido_e_campos_novos_ficam_none(name, topic):
    p = parse_message(f"agrofleet/v1/{topic}/telemetry", raw(name))
    assert all(getattr(p, f) is None for f in NEW)


def test_envelope_1_0_com_campos_novos_e_aceito():
    """O minor é informativo: um dispositivo que ainda declara 1.0 mas envia campo 1.1 válido não é descartado."""
    msg = json.loads(raw("mqtt_envelope_v11_diesel.json"))
    msg["schema_version"] = "1.0"
    assert unwrap(msg).engine_rpm == 1950.0


def test_exemplo_diesel():
    p = parse_message("agrofleet/v1/TRATOR-EX-1/telemetry", raw("mqtt_envelope_v11_diesel.json"))
    assert (p.engine_rpm, p.engine_load_pct, p.fuel_level_pct, p.fuel_rate_lph, p.heading_deg) == (1950.0, 78.0, 62.0, 25.4, 37.5)
    assert p.battery_soc_pct is None and p.battery_energy_kwh is None and p.flow_lpm is None   # trator: sem bateria nem calda
    assert {"engine", "fuel_level", "fuel_rate", "heading"} <= set(p.sensor_quality)


def test_exemplo_eletrico():
    p = parse_message("agrofleet/v1/PULV-EX-E1/telemetry", raw("mqtt_envelope_v11_electric.json"))
    assert (p.battery_soc_pct, p.battery_energy_kwh, p.battery_power_kw) == (54.0, 21.6, 6.4)
    assert p.engine_rpm is None and p.fuel_level_pct is None and p.fuel_rate_lph is None       # elétrico: sem motor a combustão
    assert p.tank_level_l == 310.0                                                             # tanque de CALDA, não energia


def test_exemplo_invalido_nivel_de_combustivel():
    with pytest.raises(ValueError, match="fuel_level_pct"):
        parse_message("agrofleet/v1/TRATOR-EX-1/telemetry", raw("mqtt_envelope_v11_invalid_fuel_level.json"))


@pytest.mark.parametrize("field,value", [
    ("engine_rpm", -1.0), ("engine_load_pct", 251.0), ("engine_load_pct", -0.1), ("engine_hours_h", -5.0),
    ("fuel_level_pct", 100.1), ("fuel_level_pct", -1.0), ("fuel_rate_lph", -0.5),
    ("battery_soc_pct", 101.0), ("battery_energy_kwh", -1.0), ("heading_deg", 360.0), ("heading_deg", -1.0),
    ("battery_power_kw", math.nan), ("fuel_rate_lph", math.inf), ("engine_rpm", math.nan),
])
def test_faixas_e_finitude(field, value):
    with pytest.raises(ValidationError, match=field):
        pt(**{field: value})


def test_valores_limite_e_sinal_da_potencia():
    p = pt(engine_load_pct=250.0, fuel_level_pct=0.0, battery_soc_pct=100.0, heading_deg=0.0, battery_power_kw=-12.5)
    assert p.battery_power_kw == -12.5        # recarga/regeneração


def test_chaves_novas_de_qualidade_de_sensor():
    p = pt(sensor_quality={"engine": "ok", "fuel_level": "degraded", "fuel_rate": "failed", "battery": "ok", "heading": "unknown"})
    assert len(p.sensor_quality) == 5
    with pytest.raises(ValidationError, match="sensor desconhecido"):
        pt(sensor_quality={"fuel": "ok"})


# --- adaptadores e qualidade --------------------------------------------------------------------------------------

UNITS = {"speed": "km/h", "fuel_level": "fraction", "fuel_rate": "gal/h", "battery_soc": "fraction", "battery_energy": "wh",
         "battery_power": "w", "heading": "rad"}


def test_csv_v11_com_unidades_declaradas():
    r = read_csv(raw("telemetry_v11_units.csv"), origin="simulated", units=UNITS)
    assert r.n_rejected_total == 0 and len(r.points) == 4
    t, e = r.points[0], r.points[2]
    assert (t.engine_rpm, t.engine_load_pct, t.engine_hours_h) == (1950.0, 78.0, 4821.6)
    assert t.fuel_level_pct == pytest.approx(62.0) and t.fuel_rate_lph == pytest.approx(25.4, abs=0.01)
    assert t.heading_deg == pytest.approx(37.5, abs=0.01) and t.speed_ms == pytest.approx(7.5 / 3.6)
    assert t.battery_soc_pct is None and e.engine_rpm is None and e.fuel_level_pct is None   # vazio continua None
    assert e.battery_soc_pct == pytest.approx(54.0) and e.battery_energy_kwh == pytest.approx(21.6) and e.battery_power_kw == pytest.approx(6.4)
    assert r.points[3].battery_power_kw == pytest.approx(-1.2)
    assert e.heading_deg == pytest.approx(182.0, abs=0.01)
    assert r.meta["columns_mapped"]["engine_rpm"] == "rpm" and r.meta["units"]["fuel_rate"] == "gal/h"


def test_csv_v11_sem_declarar_fracao_e_sinalizado_nao_corrigido():
    units = {k: v for k, v in UNITS.items() if k not in ("fuel_level", "battery_soc", "heading")}
    r = read_csv(raw("telemetry_v11_units.csv"), origin="simulated", units=units)
    assert r.points[0].fuel_level_pct == pytest.approx(0.62)          # não é "consertado" em silêncio
    msgs = " ".join(f["msg"] for f in assess(r)["flags"] if f["code"] == "unit_suspect")
    assert "nível de combustível" in msgs and "estado de carga" in msgs


def test_csv_unidade_desconhecida_para_campo_novo():
    with pytest.raises(AdapterError, match="fuel_rate"):
        read_csv(raw("telemetry_v11_units.csv"), origin="simulated", units={"fuel_rate": "kg/h"})


def test_json_de_envelopes_v11():
    body = json.dumps([json.loads(raw("mqtt_envelope_v11_diesel.json")), json.loads(raw("mqtt_envelope_v11_electric.json"))])
    r = read_json(body)
    assert r.n_rejected_total == 0 and {p.machine_id for p in r.points} == {"TRATOR-EX-1", "PULV-EX-E1"}
    assert next(p for p in r.points if p.machine_id == "TRATOR-EX-1").sensor_quality["fuel_rate"].value == "ok"


def test_qualidade_relata_nulos_dos_campos_novos():
    r = read_csv(raw("telemetry_v11_units.csv"), origin="simulated", units=UNITS)
    q = assess(r)
    assert q["null_pct"]["engine_rpm"] == 50.0 and q["null_pct"]["battery_soc_pct"] == 50.0
    assert not [f for f in q["flags"] if f["code"] == "unit_suspect"]


def test_frame_tem_colunas_novas_com_nan_quando_ausente():
    df = to_frame([pt(engine_rpm=1800.0), pt(ts=T0 + timedelta(seconds=1))])
    assert set(NEW) <= set(df.columns) and {"q_engine", "q_battery"} <= set(df.columns)
    assert df["engine_rpm"].iloc[0] == 1800.0 and math.isnan(df["engine_rpm"].iloc[1])


# --- persistência e migração ------------------------------------------------------------------------------------

def test_banco_ida_e_volta_e_ingested_at():
    eng = make_engine("sqlite://")
    init_db(eng)
    SF = sessionmaker_for(eng)
    before = datetime.now(timezone.utc)
    with SF() as s:
        assert insert_points(s, [pt(fuel_level_pct=62.0, fuel_rate_lph=25.4, heading_deg=10.0, sensor_quality={"fuel_level": "ok"}),
                                 pt(ts=T0 + timedelta(seconds=1))]) == 2
        df = load_frame(s, ["M-1"])
        row = s.query(TelemetryRow).order_by(TelemetryRow.ts).first()
    assert df["fuel_level_pct"].iloc[0] == 62.0 and math.isnan(df["fuel_level_pct"].iloc[1])
    assert df["q_fuel_level"].iloc[0] == "ok" and pd.isna(df["q_fuel_level"].iloc[1])
    assert row.ingested_at is not None and row.ingested_at >= before and row.ts == T0   # ts = horário do dado; ingested_at = servidor


def _old_v10_schema(eng):
    """Cria a tabela telemetry como era no schema 1.0 (sem colunas de motor/energia nem ingested_at)."""
    md = MetaData()
    drop = set(NEW) | {"ingested_at"}
    Table("telemetry", md, *[c._copy() for c in TelemetryRow.__table__.columns if c.name not in drop],
          UniqueConstraint("machine_id", "ts", "origin", name="uq_tel_machine_ts_origin"))
    md.create_all(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO telemetry (machine_id, ts, origin, state, gnss_fix, error_codes, sensor_quality, speed_ms) "
                       "VALUES ('OLD-1', '2026-03-10 10:00:00', 'simulated', 'working', 'rtk_fixed', '[]', '{}', 5.0)"))


def test_migracao_aditiva_de_banco_1_0(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'v10.db'}")
    _old_v10_schema(eng)
    assert "fuel_level_pct" not in {c["name"] for c in inspect(eng).get_columns("telemetry")}
    init_db(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("telemetry")}
    assert set(NEW) | {"ingested_at"} <= cols
    assert add_missing_columns(eng) == {}                                  # idempotente
    SF = sessionmaker_for(eng)
    with SF() as s:
        old = load_frame(s, ["OLD-1"])
        assert len(old) == 1 and old["speed_ms"].iloc[0] == 5.0 and old[NEW].isna().all(axis=None)   # linha antiga preservada, NULL
        assert s.query(TelemetryRow).filter_by(machine_id="OLD-1").one().ingested_at is None
        assert insert_points(s, [pt(machine_id="NEW-1", battery_soc_pct=80.0)]) == 1
        assert load_frame(s, ["NEW-1"])["battery_soc_pct"].iloc[0] == 80.0


# --- API e WebSocket --------------------------------------------------------------------------------------------

USERS = {"admin": ("adm", "adm-pass"), "analyst": ("ana", "ana-pass"), "viewer": ("vie", "vie-pass")}


@pytest.fixture(scope="module")
def api():
    st = Settings(database_url="sqlite://", auth_secret="test-secret-test-secret-test-secret-0123", users=USERS, mqtt_host=None)
    with TestClient(create_app(st)) as c:
        h = {k: {"Authorization": "Bearer " + c.post("/auth/login", json={"username": u, "password": p}).json()["access_token"]}
             for k, (u, p) in USERS.items()}
        body = json.dumps([json.loads(raw("mqtt_envelope_v11_diesel.json")), json.loads(raw("mqtt_envelope_v11_electric.json"))])
        r = c.post("/uploads", headers=h["analyst"], files={"file": ("v11.json", body, "application/json")},
                   data={"kind": "json", "origin": "simulated", "commit": "true"})
        assert r.status_code == 200 and r.json()["stored"] == 2, r.text
        yield c, h


def test_health_informa_1_1(api):
    c, _ = api
    assert c.get("/health").json()["schema_version"] == "1.1"


def test_machines_expoe_campos_novos_e_ingested_at(api):
    c, h = api
    ms = {m["machine_id"]: m for m in c.get("/machines", headers=h["viewer"]).json()}
    t, e = ms["TRATOR-EX-1"], ms["PULV-EX-E1"]
    assert t["fuel_level_pct"] == 62.0 and t["engine_rpm"] == 1950.0 and t["battery_soc_pct"] is None
    assert e["battery_soc_pct"] == 54.0 and e["fuel_level_pct"] is None and e["engine_rpm"] is None
    assert t["ingested_at"] and t["ingested_at"] != t["last_ts"]


def test_telemetria_bruta_expoe_campos_novos(api):
    c, h = api
    rows = c.get("/machines/PULV-EX-E1/telemetry", headers=h["viewer"]).json()
    assert rows[0]["battery_power_kw"] == 6.4 and rows[0]["heading_deg"] == 182.0 and rows[0]["ingested_at"]


def test_point_brief_do_websocket_e_aditivo():
    b = point_brief(pt(fuel_level_pct=40.0))
    assert b["fuel_level_pct"] == 40.0 and b["battery_soc_pct"] is None
    assert {"machine_id", "ts", "lat", "lon", "speed_ms", "state", "gnss_fix", "flow_lpm", "pressure_bar", "tank_level_l", "origin"} <= set(b)
