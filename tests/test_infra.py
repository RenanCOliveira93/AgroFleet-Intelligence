"""Integração com PostGIS e Mosquitto REAIS. Marcados 'infra': rodam com `pytest -m infra` e as variáveis abaixo
(a pilha do compose, ou contêineres avulsos — ver docs/GUIA_TECNICO.md (seção 4.6)). Sem as variáveis, são pulados."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from agrofleet.adapters.mqtt import MqttPublisher, MqttSource
from agrofleet.analytics.frame import to_frame
from agrofleet.analytics.metrics import machine_metrics
from pyproj import Geod
from agrofleet.schema import wrap
from agrofleet.simulator.fleet import simulate
from backend.app.config import Settings
from backend.app.main import create_app

pytestmark = pytest.mark.infra
PG = os.environ.get("AGROFLEET_TEST_PG_URL")
MQ = os.environ.get("AGROFLEET_TEST_MQTT")          # host:port
MQ_DEV = (os.environ.get("AGROFLEET_TEST_MQTT_DEVICE_USER"), os.environ.get("AGROFLEET_TEST_MQTT_DEVICE_PASSWORD"))
MQ_ING = (os.environ.get("AGROFLEET_TEST_MQTT_INGEST_USER"), os.environ.get("AGROFLEET_TEST_MQTT_INGEST_PASSWORD"))
USERS = {"admin": ("adm", "adm-pass"), "analyst": ("ana", "ana-pass"), "viewer": ("vie", "vie-pass")}
SECRET = "infra-secret-infra-secret-infra-secret-01"


def _login(c, who="admin"):
    u, p = USERS[who]
    return {"Authorization": "Bearer " + c.post("/auth/login", json={"username": u, "password": p}).json()["access_token"]}


@pytest.mark.skipif(not PG, reason="AGROFLEET_TEST_PG_URL não definido")
def test_postgis_real_cobertura_cruzada_e_idempotencia():
    st = Settings(database_url=PG, auth_secret=SECRET, users=USERS, mqtt_host=None)
    with TestClient(create_app(st)) as c:
        h = _login(c)
        c.delete("/data", params={"origin": "simulated"}, headers=h)
        r1 = c.post("/demo/load", headers=h).json()
        r2 = c.post("/demo/load", headers=h).json()
        assert r1["stored"] == r1["samples"] and r2["stored"] == 0          # ON CONFLICT DO NOTHING no PostgreSQL
        assert c.get("/health").json()["postgis"] is True
        # 1) comprimento da trilha: geodésico (PostGIS geography) vs UTM (Python), mesmos pontos
        chk = c.get("/postgis/check/SIM-01", params={"field_id": "TALHAO-A"}, headers=h).json()
        pts, truth = simulate()
        df = to_frame([p for p in pts if p.machine_id == "SIM-01"]).dropna(subset=["lat", "lon"])
        d = Geod(ellps="WGS84").line_length(df.lon.to_numpy(), df.lat.to_numpy())   # geodésica elipsoidal (pyproj) vs PostGIS
        assert chk["track_length_m_geography_all_points"] == pytest.approx(d, rel=1e-4)
        m = c.get("/metrics/SIM-01", headers=h).json()
        assert m["distance_km"] * 1000 == pytest.approx(chk["track_length_m_geography_all_points"], rel=0.02)   # ruído GNSS e filtro de salto
        # 2) área do talhão: geography (elipsoide) vs UTM
        cov = c.get("/coverage", params={"machine_ids": "SIM-01", "field_id": "TALHAO-A"}, headers=h).json()["summary"]
        assert chk["field_area_m2_geography"] / 1e4 == pytest.approx(cov["field_ha"], rel=3e-3)
        # 3) ST_Within com índice GIST
        assert chk["points_within_field"] > 1500
        bb = c.get("/machines/SIM-01/telemetry", params={"bbox": "-47.601,-16.701,-47.59,-16.69", "limit": 50}, headers=h).json()
        assert bb
        c.delete("/data", params={"origin": "simulated"}, headers=h)


@pytest.mark.skipif(not MQ, reason="AGROFLEET_TEST_MQTT não definido")
def test_mqtt_real_recebe_rejeita_e_autentica():
    host, port = MQ.split(":")
    got, rej = [], []
    src = MqttSource(host, int(port), username=MQ_ING[0], password=MQ_ING[1], on_point=got.append, on_reject=lambda t, r: rej.append((t, r)))
    assert src.start(10)
    pub = MqttPublisher(host, int(port), username=MQ_DEV[0], password=MQ_DEV[1])
    pts, _ = simulate(max_seconds=5)
    for p in pts[:8]:
        pub.publish_point(p)
    pub.publish_raw("agrofleet/v1/SIM-01/telemetry", "{lixo")
    pub.publish_raw("agrofleet/v1/SIM-01/telemetry", json.dumps({**wrap(pts[0], "x"), "schema_version": "3.0"}))
    pub.publish_raw("agrofleet/v1/SIM-02/telemetry", json.dumps(wrap(pts[0], "y")))      # machine_id != tópico
    t0 = time.time()
    while (len(got) < 8 or len(rej) < 3) and time.time() - t0 < 10:
        time.sleep(0.1)
    assert len(got) == 8 and all(p.origin == "simulated" for p in got) and len(rej) == 3
    assert any("não é JSON" in r for _, r in rej) and any("incompatível" in r for _, r in rej) and any("difere do tópico" in r for _, r in rej)
    # ACL: o dispositivo só publica em telemetria; publicar em tópico de "comando" não chega a ninguém
    spy = []
    s2 = MqttSource(host, int(port), username=MQ_ING[0], password=MQ_ING[1], on_point=spy.append, topic="agrofleet/#")
    assert s2.start(10)
    pub.publish_raw("agrofleet/v1/SIM-01/command", "{}")
    time.sleep(1.0)
    assert s2.stats["received"] == 0     # ACL bloqueou (e o ingest user nem assina fora de telemetria)
    # sem credencial: conexão recusada
    anon = MqttSource(host, int(port), on_point=lambda p: None)
    assert anon.start(3) is False
    for x in (src, s2, anon):
        x.stop()
    pub.close()


@pytest.mark.skipif(not (PG and MQ), reason="requer PG e MQTT reais")
def test_ponta_a_ponta_mqtt_para_banco_alertas_e_websocket():
    host, port = MQ.split(":")
    st = Settings(database_url=PG, auth_secret=SECRET, users=USERS, mqtt_host=host, mqtt_port=int(port), mqtt_user=MQ_ING[0], mqtt_password=MQ_ING[1])
    with TestClient(create_app(st)) as c:
        h = _login(c, "viewer")
        _login(c, "admin")
        hadm = _login(c, "admin")
        c.delete("/data", params={"origin": "simulated"}, headers=hadm)
        t0 = time.time()
        while not c.get("/health").json()["mqtt"]["connected"] and time.time() - t0 < 10:
            time.sleep(0.2)
        assert c.get("/health").json()["mqtt"]["connected"]
        token = h["Authorization"].split()[1]
        pts, truth = simulate(max_seconds=900)
        with c.websocket_connect("/ws") as w:
            w.send_json({"type": "auth", "token": token}); assert w.receive_json()["type"] == "hello"
            pub = MqttPublisher(host, int(port), username=MQ_DEV[0], password=MQ_DEV[1])
            for p in pts:
                pub.publish_point(p)
            msgs = []
            t0 = time.time()
            while time.time() - t0 < 25:
                try:
                    msgs.append(w.receive_json())
                except Exception:
                    break
                if any(m["type"] == "alert" for m in msgs) and len(msgs) > 3:
                    break
            pub.close()
        types = {m["type"] for m in msgs}
        assert "telemetry" in types and "alert" in types
        deadline = time.time() + 30
        while time.time() < deadline:
            n = sum(m["samples"] for m in c.get("/machines", headers=h).json())
            if n >= len(pts):
                break
            time.sleep(0.5)
        ms = c.get("/machines", headers=h).json()
        assert sum(m["samples"] for m in ms) == len(pts) and all(m["origin"] == "simulated" and m["live"] for m in ms)
        al = c.get("/alerts", headers=h).json()
        assert {"gnss_loss", "fault_stop"} <= {a["type"] for a in al}          # eventos injetados nos primeiros 15 min
        assert len({a["id"] for a in al}) == len(al)                           # sem alerta duplicado por reavaliação em janela deslizante
        c.delete("/data", params={"origin": "simulated"}, headers=hadm)


@pytest.mark.skipif(not PG, reason="AGROFLEET_TEST_PG_URL não definido")
def test_migracao_aditiva_1_0_para_1_1_no_postgis():
    """Banco criado no schema 1.0 (com a coluna gerada geom do PostGIS) recebe as colunas 1.1 sem perder dados.
    Usa um banco descartável próprio para não interferir no banco dos outros testes."""
    from datetime import datetime, timezone
    from sqlalchemy import MetaData, Table, UniqueConstraint, create_engine, inspect, text
    from sqlalchemy.engine import make_url
    from agrofleet.schema import ENGINE_ENERGY_FIELDS, TelemetryPoint
    from backend.app.db import TelemetryRow, add_missing_columns, init_db, insert_points, load_frame, sessionmaker_for
    name = "agrofleet_migr_test"
    admin = create_engine(PG, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {name}")); c.execute(text(f"CREATE DATABASE {name}"))
    eng = create_engine(make_url(PG).set(database=name))
    try:
        new = set(ENGINE_ENERGY_FIELDS) | {"ingested_at"}
        md = MetaData()
        Table("telemetry", md, *[col._copy() for col in TelemetryRow.__table__.columns if col.name not in new],
              UniqueConstraint("machine_id", "ts", "origin", name="uq_tel_machine_ts_origin"))
        md.create_all(eng)
        with eng.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            c.execute(text("ALTER TABLE telemetry ADD COLUMN geom geometry(Point,4326) GENERATED ALWAYS AS "
                           "(CASE WHEN lat IS NULL OR lon IS NULL THEN NULL ELSE ST_SetSRID(ST_MakePoint(lon, lat), 4326) END) STORED"))
            c.execute(text("INSERT INTO telemetry (machine_id, ts, origin, state, gnss_fix, error_codes, sensor_quality, lat, lon) "
                           "VALUES ('OLD-1', '2026-03-10T10:00:00Z', 'simulated', 'working', 'rtk_fixed', '[]', '{}', -16.7, -47.6)"))
        init_db(eng)
        cols = {c["name"]: c for c in inspect(eng).get_columns("telemetry")}
        assert new <= set(cols) and "geom" in cols
        assert cols["ingested_at"]["type"].timezone is True                     # timestamptz, como ts
        assert add_missing_columns(eng) == {}
        SF = sessionmaker_for(eng)
        with SF() as s:
            old = load_frame(s, ["OLD-1"])
            assert len(old) == 1 and old[list(ENGINE_ENERGY_FIELDS)].isna().all(axis=None)
            p = TelemetryPoint(machine_id="NEW-1", ts=datetime(2026, 5, 1, tzinfo=timezone.utc), origin="simulated",
                               lat=-16.7, lon=-47.6, fuel_level_pct=55.0, heading_deg=90.0)
            assert insert_points(s, [p]) == 1
            assert load_frame(s, ["NEW-1"])["fuel_level_pct"].iloc[0] == 55.0
            assert s.execute(text("SELECT count(*) FROM telemetry WHERE geom IS NOT NULL")).scalar() == 2   # coluna gerada intacta
    finally:
        eng.dispose()
        with admin.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        admin.dispose()


@pytest.mark.skipif(not PG, reason="AGROFLEET_TEST_PG_URL não definido")
def test_cenarios_de_operacao_no_postgis():
    """Etapa 2/3: cadastro em PostgreSQL e talhão irregular georreferenciado coerente com a telemetria (ST_Within)."""
    st = Settings(database_url=PG, auth_secret=SECRET, users=USERS, mqtt_host=None)
    with TestClient(create_app(st)) as c:
        h = _login(c)
        c.delete("/data", params={"origin": "simulated"}, headers=h)
        r = c.post("/demo/operations/load", params={"scenarios": "A,B", "hours": 0.25}, headers=h).json()
        assert r["stored"] == 2 * 901
        assert {o["operation_id"] for o in c.get("/operations", headers=h).json()} == {"OP-SIM-A", "OP-SIM-B"}
        chk = c.get("/postgis/check/SIM-TR-320", params={"field_id": "SIM-TALHAO-03"}, headers=h).json()
        assert chk["field_area_m2_geography"] / 1e4 == pytest.approx(120.0, rel=3e-3)        # área elipsoidal vs. UTM do simulador
        n_work = sum(1 for p in c.get("/machines/SIM-TR-320/telemetry", headers=h).json() if p["state"] == "working")
        assert chk["points_within_field"] >= n_work > 300                                    # trabalho acontece dentro do talhão
        # Etapa 4: status ao vivo no PostgreSQL (idade por ingested_at TIMESTAMPTZ; dado terminando agora)
        s = c.get("/operations/OP-SIM-A/status", headers=h)
        assert s.status_code == 200, s.text
        s = s.json()
        assert s["connection"]["basis"] == "max(ingested_at, ts)" and s["connection"]["age_s"] < 300
        assert s["connection"]["state"] in ("online", "delayed") and s["mission"]["worked_ha"] > 0
        assert "elapsed_too_short" in {r["code"] for r in s["mission"]["eta_reasons"]}       # 15 min: sem ETA
        g = c.get("/operations/OP-SIM-A/geometry", headers=h).json()
        assert g["worked"] and g["track"]["type"] == "MultiLineString"
        d = c.delete("/data", params={"origin": "simulated"}, headers=h).json()
        assert d["operations"] == 2 and d["machine_profiles"] == 2
