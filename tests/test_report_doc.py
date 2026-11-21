"""Etapa 10: relatório para impressão/envio (GET /reports/document) e grupo "total" do relatório agregado."""
from datetime import timedelta

import pytest

from agrofleet.analytics.frame import to_frame
from agrofleet.analytics.metrics import report
from agrofleet.simulator.fleet import DEFAULT_START
from conftest import make_pts, passes  # noqa: F401

USERS = {"admin": ("adm", "adm-pass"), "analyst": ("ana", "ana-pass"), "viewer": ("vie", "vie-pass")}


def test_total_une_as_maquinas_e_bate_com_a_soma_de_tempos():
    from agrofleet.simulator.operations import run_scenarios
    pts = [p for r in run_scenarios(["A", "B"], seed=7, start=DEFAULT_START, max_seconds=1800) for p in r.points]
    df = to_frame(pts)
    tot = report(df, "total")["rows"]
    per = report(df, "machine")["rows"]
    assert len(tot) == 1 and tot[0]["total"] == "total" and not tot[0]["sem_valor"]
    assert tot[0]["machines"] == sorted(r["machine_id"] for r in per)
    assert tot[0]["time_h"]["working"] == pytest.approx(sum(r["time_h"]["working"] for r in per))
    assert tot[0]["distance_km"] == pytest.approx(sum(r["distance_km"] for r in per))
    # área única do total é a união: nunca maior que a soma das áreas únicas por máquina
    assert tot[0]["coverage"]["unique_ha"] <= sum(r["coverage"]["unique_ha"] for r in per) + 1e-9


@pytest.fixture(scope="module")
def api_doc():
    from fastapi.testclient import TestClient
    from backend.app.config import Settings
    from backend.app.main import create_app
    st = Settings(database_url="sqlite://", auth_secret="test-secret-test-secret-test-secret-0123", users=USERS, mqtt_host=None)
    with TestClient(create_app(st)) as c:
        h = {k: {"Authorization": "Bearer " + c.post("/auth/login", json={"username": u, "password": p}).json()["access_token"]}
             for k, (u, p) in USERS.items()}
        r = c.post("/demo/operations/load", params={"scenarios": "A,B", "hours": 1, "start": DEFAULT_START.isoformat()}, headers=h["admin"])
        assert r.status_code == 200, r.text
        yield c, h


def test_documento_traz_totais_maquinas_operacoes_e_alertas(api_doc):
    c, h = api_doc
    r = c.get("/reports/document", params={"origin": "simulated", "property_id": "PROP-SIM-SC"}, headers=h["viewer"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["generated_by"] == "vie" and d["property"]["property_id"] == "PROP-SIM-SC"
    assert d["data_origins"] == ["simulated"] and "SIMULADOS" in d["origin_notes"][0]
    assert d["totals"]["n_samples"] == sum(m["n_samples"] for m in d["machines"])
    assert all(m["name"] for m in d["machines"])                      # nome vem do cadastro
    ops = {o["operation_id"]: o for o in d["operations"]}
    assert set(ops) == {"OP-SIM-A", "OP-SIM-B"}
    a = ops["OP-SIM-A"]
    assert a["mission"]["worked_ha"] > 0 and a["machine_name"] and a["field_name"] and a["unavailable"] is None
    assert a["consumption"]["unit"]
    assert set(d["alerts"]["counts"]) == {"critical", "warning", "info"}
    assert any("monitoramento" in n for n in d["notes"])
    # mesmos números do cálculo de status da operação
    stt = c.get("/operations/OP-SIM-A/status", headers=h["viewer"]).json()
    assert a["mission"]["worked_ha"] == pytest.approx(stt["mission"]["worked_ha"])


def test_documento_filtra_periodo_e_valida(api_doc):
    c, h = api_doc
    later = (DEFAULT_START + timedelta(days=30)).isoformat()
    before = {"start": (DEFAULT_START - timedelta(days=2)).isoformat(), "end": (DEFAULT_START - timedelta(days=1)).isoformat()}
    d = c.get("/reports/document", params=before, headers=h["viewer"]).json()
    assert d["totals"] is None and d["machines"] == [] and d["operations"] == []      # nada inventado
    # operação ativa sem fim previsto se sobrepõe a um período futuro, mas sem telemetria nele
    fut = c.get("/reports/document", params={"start": later}, headers=h["viewer"]).json()
    assert fut["totals"] is None and {o["status"] for o in fut["operations"]} == {"active"}
    assert c.get("/reports/document", params={"start": later, "end": DEFAULT_START.isoformat()}, headers=h["viewer"]).status_code == 422
    assert c.get("/reports/document", params={"property_id": "NOPE"}, headers=h["viewer"]).status_code == 404
    assert c.get("/reports/document").status_code == 401
    real = c.get("/reports/document", params={"origin": "real"}, headers=h["viewer"]).json()
    assert real["operations"] == [] and real["totals"] is None


def test_relatorio_agregado_aceita_total(api_doc):
    c, h = api_doc
    r = c.get("/reports", params={"group_by": "total", "origin": "simulated"}, headers=h["viewer"])
    assert r.status_code == 200 and len(r.json()["rows"]) == 1
    csv = c.get("/reports", params={"group_by": "total", "format": "csv"}, headers=h["viewer"]).text
    assert csv.splitlines()[1].startswith("total,")
