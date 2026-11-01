import json
import time

import pytest
from fastapi.testclient import TestClient

from agrofleet.schema import wrap
from agrofleet.simulator.fleet import simulate
from backend.app.config import Settings
from backend.app.main import create_app

USERS = {"admin": ("adm", "adm-pass"), "analyst": ("ana", "ana-pass"), "viewer": ("vie", "vie-pass")}


@pytest.fixture(scope="module")
def client():
    st = Settings(database_url="sqlite://", auth_secret="test-secret-test-secret-test-secret-0123", users=USERS, mqtt_host=None)
    app = create_app(st)
    with TestClient(app) as c:
        yield c


def tok(c, who):
    u, p = USERS[who]
    r = c.post("/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def H(client):
    return {k: tok(client, k) for k in USERS}


@pytest.fixture(scope="module", autouse=True)
def loaded(client, H):
    r = client.post("/demo/load", headers=H["admin"])
    assert r.status_code == 200 and r.json()["origin"] == "simulated"
    r = client.post("/alerts/recompute", headers=H["analyst"], params={"with_ml": False})
    assert r.status_code == 200


def test_health_limits_e_openapi(client):
    assert client.get("/health").json()["status"] == "ok"
    lim = client.get("/limits").json()
    assert "NÃO controla" in lim["scope"] and lim["adapters"]["isoxml"]["status"] == "not_implemented"
    paths = client.get("/openapi.json").json()["paths"]
    assert "/coverage" in paths and "/uploads" in paths and "/roi" in paths
    # nenhum endpoint de comando/controle
    assert not [p for p in paths if any(w in p.lower() for w in ("command", "steer", "brake", "spray/start", "control"))]


def test_auth_e_papeis(client, H):
    assert client.get("/machines").status_code == 401
    assert client.get("/machines", headers={"Authorization": "Bearer lixo"}).status_code == 401
    assert client.post("/auth/login", json={"username": "adm", "password": "errada"}).status_code == 401
    assert client.get("/machines", headers=H["viewer"]).status_code == 200
    assert client.post("/alerts/recompute", headers=H["viewer"]).status_code == 403
    assert client.post("/demo/load", headers=H["analyst"]).status_code == 403
    assert client.put("/config/alerts", json={"flow_tol_pct": 10}, headers=H["analyst"]).status_code == 403
    assert client.get("/auth/me", headers=H["analyst"]).json() == {"username": "ana", "role": "analyst"}


def test_frota_marca_origem_simulada(client, H):
    ms = client.get("/machines", headers=H["viewer"]).json()
    assert len(ms) == 4 and {m["origin"] for m in ms} == {"simulated"} and not any(m["live"] for m in ms)
    sm = client.get("/summary", headers=H["viewer"]).json()
    assert sm["any_simulated"] and not sm["any_real"]
    assert client.get("/machines", params={"origin": "real"}, headers=H["viewer"]).json() == []


def test_telemetria_e_trilha(client, H):
    rows = client.get("/machines/SIM-03/telemetry", params={"limit": 3000}, headers=H["viewer"]).json()
    assert any(r["lat"] is None for r in rows)            # perda de GNSS preservada como null
    assert all(r["origin"] == "simulated" for r in rows)
    tr = client.get("/machines/SIM-03/track", headers=H["viewer"]).json()
    assert tr["origins"] == ["simulated"] and all(f["geometry"]["type"] == "LineString" for f in tr["features"])
    assert {f["properties"]["state"] for f in tr["features"]} >= {"working", "transport"}
    bb = client.get("/machines/SIM-01/telemetry", params={"bbox": "-47.7,-16.8,-47.5,-16.6", "limit": 10}, headers=H["viewer"]).json()
    assert len(bb) == 10
    assert client.get("/machines/SIM-01/telemetry", params={"bbox": "x"}, headers=H["viewer"]).status_code == 422


def test_playback_downsample(client, H):
    r = client.get("/playback", params={"step_s": 10, "machine_ids": "SIM-01,SIM-03"}, headers=H["viewer"]).json()
    assert set(r["machines"]) == {"SIM-01", "SIM-03"} and r["t_end"] > r["t_start"]
    m = r["machines"]["SIM-03"]
    assert None in m["lat"] and len(m["t"]) < 400


def test_cobertura_e_metricas(client, H):
    c = client.get("/coverage", params={"machine_ids": "SIM-01", "field_id": "TALHAO-A"}, headers=H["viewer"]).json()
    s = c["summary"]
    assert c["origins"] == ["simulated"] and 20 < s["gross_ha"] < 24 and s["excess_ha"] > 1.5 and s["coverage_pct_of_field"] > 95
    assert c["union"]["type"] in ("Polygon", "MultiPolygon") and c["field"]
    assert client.get("/coverage", params={"field_id": "nao"}, headers=H["viewer"]).status_code == 404
    m = client.get("/metrics/SIM-02", params={"hourly_cost": 300, "product_cost_per_l": 1.0}, headers=H["viewer"]).json()
    assert m["time_h"]["fault"] > 0.1 and m["cost"]["cost_per_ha"] > 0 and m["origins"] == ["simulated"]
    assert client.get("/metrics/NADA", headers=H["viewer"]).status_code == 404


def test_relatorios_json_e_csv(client, H):
    r = client.get("/reports", params={"group_by": "operator"}, headers=H["viewer"]).json()
    assert len(r["rows"]) == 2
    csv_ = client.get("/reports", params={"group_by": "field", "format": "csv"}, headers=H["viewer"])
    lines = csv_.text.strip().splitlines()
    assert csv_.headers["content-type"].startswith("text/csv") and len(lines) == 5 and lines[0].startswith("field,origins")
    assert client.get("/reports", params={"group_by": "cultura"}, headers=H["viewer"]).status_code == 422
    p = client.get("/reports", params={"group_by": "machine", "start": "2026-03-10T10:00:00Z", "end": "2026-03-10T10:10:00Z"}, headers=H["viewer"]).json()
    full = client.get("/reports", params={"group_by": "machine"}, headers=H["viewer"]).json()
    assert p["rows"][0]["n_samples"] < full["rows"][0]["n_samples"]


def test_alertas_e_avaliacao_do_simulador(client, H):
    al = client.get("/alerts", headers=H["viewer"]).json()
    types = {a["type"] for a in al}
    assert {"fault_stop", "gnss_loss", "flow_deviation", "sensor_missing"} <= types
    assert all(a["informational"] and a["origin"] == "simulated" for a in al)
    ev = client.get("/demo/evaluation", headers=H["viewer"]).json()
    assert ev["recall"] == 1.0 and ev["origin"] == "simulated" and "otimista" in ev["caveat"]


def test_config_de_alertas_validada(client, H):
    assert client.put("/config/alerts", json={"nao_existe": 1}, headers=H["admin"]).status_code == 422
    r = client.put("/config/alerts", json={"flow_tol_pct": 40.0}, headers=H["admin"])
    assert r.status_code == 200 and r.json()["flow_tol_pct"] == 40.0
    out = client.post("/alerts/recompute", headers=H["analyst"], params={"machine_ids": "SIM-04"}).json()
    assert client.get("/alerts", params={"machine_id": "SIM-04", "type": "flow_deviation"}, headers=H["viewer"]).json() == []   # desvio de 35% < tolerância de 40%
    client.put("/config/alerts", json={"flow_tol_pct": 15.0}, headers=H["admin"])
    client.post("/alerts/recompute", headers=H["analyst"], params={"machine_ids": "SIM-04"})
    assert client.get("/alerts", params={"machine_id": "SIM-04", "type": "flow_deviation"}, headers=H["viewer"]).json()


CSV = ("machine_id,timestamp,lat,lon,speed_kmh,state,flow,pressure,gnss_fix,field_id\n"
       "REAL-X,2026-05-01T12:00:00Z,-20.1,-45.1,18,working,72,3.0,rtk_fixed,F1\n"
       "REAL-X,2026-05-01T12:00:01Z,-20.10004,-45.1,18,working,72,3.0,rtk_fixed,F1\n"
       "REAL-X,2026-05-01T12:00:02Z,-20.10009,-45.1,18,working,72,3.0,rtk_fixed,F1\n")


def test_upload_validacao_commit_e_idempotencia(client, H):
    f = {"file": ("t.csv", CSV, "text/csv")}
    pre = client.post("/uploads", files=f, data={"kind": "csv", "origin": "real"}, headers=H["analyst"]).json()
    assert pre["committed"] is False and pre["quality"]["rows_ok"] == 3 and pre["stored"] is None
    assert client.get("/machines", params={"origin": "real"}, headers=H["viewer"]).json() == []   # só validou
    c1 = client.post("/uploads", files=f, data={"kind": "csv", "origin": "real", "commit": "true"}, headers=H["analyst"]).json()
    c2 = client.post("/uploads", files=f, data={"kind": "csv", "origin": "real", "commit": "true"}, headers=H["analyst"]).json()
    assert c1["stored"] == 3 and c2["stored"] == 0 and c2["duplicates_ignored"] == 3
    ms = client.get("/machines", params={"origin": "real"}, headers=H["viewer"]).json()
    assert [m["machine_id"] for m in ms] == ["REAL-X"] and ms[0]["origin"] == "real"
    assert client.get("/summary", headers=H["viewer"]).json()["any_real"]
    assert client.post("/uploads", files=f, data={"kind": "csv", "origin": "real"}, headers=H["viewer"]).status_code == 403


def test_upload_erros(client, H):
    a = H["analyst"]
    assert client.post("/uploads", files={"file": ("x.bin", b"x", "application/octet-stream")}, data={"kind": "isoxml"}, headers=a).status_code == 501
    assert client.post("/uploads", files={"file": ("x.csv", "a,b\n1,2\n", "text/csv")}, data={"kind": "csv"}, headers=a).status_code == 422
    assert client.post("/uploads", files={"file": ("x.gpx", "<gpx/>", "text/xml")}, data={"kind": "gpx"}, headers=a).status_code == 422   # sem machine_id
    bad = "machine_id,ts,lat,lon\nM,garbage,1,1\n"
    r = client.post("/uploads", files={"file": ("x.csv", bad, "text/csv")}, data={"kind": "csv", "commit": "true"}, headers=a)
    assert r.status_code == 422 and r.json()["detail"]["quality"]["verdict"] == "blocking"
    assert client.post("/uploads", files={"file": ("x.csv", CSV, "text/csv")}, data={"kind": "csv", "origin": "talvez"}, headers=a).status_code == 422


def test_roi_endpoints(client, H):
    ex = client.get("/roi/example", headers=H["viewer"]).json()
    assert ex["inputs"]["is_example"] is True
    r = client.post("/roi", json={"inputs": ex["inputs"], "grid": {"x": "area_ha_per_year", "y": "product_cost_per_ha", "xs": [3000, 6000], "ys": [60, 120]}}, headers=H["viewer"]).json()
    assert r["result"]["is_example"] and len(r["sensitivity"]["tornado"]) >= 10 and len(r["grid"]["net_benefit_per_year"]) == 2
    assert r["break_even"]["area_ha_per_year"] > 6000     # no exemplo ilustrativo o benefício só fica positivo acima de ~7 mil ha/ano
    assert client.post("/roi", json={"inputs": {"foo": 1}}, headers=H["viewer"]).status_code == 422
    assert client.post("/roi", json={"inputs": {"area_ha_per_year": 0}}, headers=H["viewer"]).status_code == 422
    t = client.post("/roi/from-telemetry", params={"origin": "simulated"}, json=ex["inputs"], headers=H["viewer"]).json()
    assert t["result"]["provenance"]["auto_productivity_ha_per_h"]["source"] == "measured_telemetry"
    assert t["result"]["provenance"]["auto_productivity_ha_per_h"]["origin"] == ["simulated"]


def test_postgis_indisponivel_no_sqlite(client, H):
    assert client.get("/postgis/check/SIM-01", headers=H["viewer"]).status_code == 501


def test_websocket_exige_auth_e_recebe_ao_vivo(client, H):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws") as w:
            w.send_json({"type": "auth", "token": "x"})
            w.receive_json()
    token = H["viewer"]["Authorization"].split()[1]
    pts, _ = simulate(max_seconds=3)
    with client.websocket_connect("/ws") as w:
        w.send_json({"type": "auth", "token": token})
        assert w.receive_json()["type"] == "hello"
        live = client.app.state.live
        for p in pts[:8]:
            live.push(p.model_copy(update={"origin": "simulated", "machine_id": "WS-1"}))
        got = w.receive_json()
        assert got["type"] == "telemetry" and got["points"][0]["machine_id"] == "WS-1" and got["points"][0]["origin"] == "simulated"
    assert any(m["machine_id"] == "WS-1" and m["live"] for m in client.get("/machines", headers=H["viewer"]).json())


def test_delete_dados_simulados(client, H):
    r = client.delete("/data", params={"origin": "simulated"}, headers=H["admin"]).json()
    assert r["telemetry"] > 1000 and r["fields"] == 4
    assert all(m["origin"] != "simulated" for m in client.get("/machines", headers=H["viewer"]).json())
