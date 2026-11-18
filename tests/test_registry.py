"""Etapa 2 — cadastro de propriedades, perfis de máquina e operações (modelos e API)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agrofleet.registry import MachineProfile, OperationPlan, PropertyInfo, check_operation_links
from backend.app.config import Settings
from backend.app.main import create_app

T0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
DIESEL = dict(machine_id="TR-1", name="Trator 1", kind="tractor", energy_source="diesel", fuel_tank_capacity_l=400.0,
              energy_reserve_pct=10.0, energy_warning_pct=25.0, energy_critical_pct=12.0, origin="real")
OP = dict(operation_id="OP-1", name="Preparo", operation_type="soil_preparation", machine_id="TR-1", field_id="F1", start=T0, origin="real")


# --- modelos ----------------------------------------------------------------------------------------------------

def test_perfil_valido_e_capacidade_da_fonte_declarada():
    m = MachineProfile(**DIESEL)
    assert m.energy_capacity == (400.0, "L")
    e = MachineProfile(machine_id="E-1", name="E", energy_source="electric", battery_capacity_kwh=60.0)
    assert e.energy_capacity == (60.0, "kWh")
    assert MachineProfile(machine_id="U-1", name="U").energy_capacity == (None, None)   # sem cadastro: sem capacidade


@pytest.mark.parametrize("patch,frag", [
    ({"battery_capacity_kwh": 50.0}, "diesel não aceita"),
    ({"energy_source": "electric", "battery_capacity_kwh": 50.0}, "elétrica não aceita"),   # DIESEL já traz fuel_tank_capacity_l
    ({"energy_source": "unknown"}, "informe energy_source"),
    ({"energy_critical_pct": 30.0}, "menor que energy_warning_pct"),
    ({"energy_critical_pct": 8.0}, "abaixo da reserva"),
    ({"maintenance_interval_h": 250.0}, "maintenance_interval_h E last_maintenance_engine_h"),
    ({"energy_reserve_pct": 60.0}, "energy_reserve_pct"),
    ({"machine_id": "TR 1"}, "machine_id"),
    ({"surprise": 1}, "surprise"),
])
def test_perfil_incoerente_e_recusado(patch, frag):
    with pytest.raises(ValidationError, match=frag):
        MachineProfile(**{**DIESEL, **patch})


def test_manutencao_nao_se_aplica_a_eletrica():
    with pytest.raises(ValidationError, match="não se aplica"):
        MachineProfile(machine_id="E-1", name="E", energy_source="electric", maintenance_interval_h=100, last_maintenance_engine_h=0)


def test_propriedade_valida_fuso():
    assert PropertyInfo(property_id="P1", name="Faz").timezone == "America/Sao_Paulo"
    with pytest.raises(ValidationError, match="fuso horário desconhecido"):
        PropertyInfo(property_id="P1", name="Faz", timezone="Brasil/Centro")


@pytest.mark.parametrize("patch,frag", [
    ({"start": datetime(2026, 5, 1, 12, 0)}, "sem fuso"),
    ({"end": T0 - timedelta(hours=1)}, "posterior a start"),
    ({"status": "completed"}, "exige end"),
    ({"planned_area_ha": 0}, "planned_area_ha"),
])
def test_operacao_invalida(patch, frag):
    with pytest.raises(ValidationError, match=frag):
        OperationPlan(**{**OP, **patch})


def test_vinculos_da_operacao():
    op, m = OperationPlan(**OP), MachineProfile(**DIESEL)
    check_operation_links(op, m, "real")
    with pytest.raises(ValueError, match="não cadastrada"):
        check_operation_links(op, None, "real")
    with pytest.raises(ValueError, match="talhão"):
        check_operation_links(op, m, None)
    with pytest.raises(ValueError, match="não se misturam"):
        check_operation_links(op, m, "simulated")
    with pytest.raises(ValueError, match="propriedades diferentes"):
        check_operation_links(OperationPlan(**{**OP, "property_id": "P2"}), MachineProfile(**{**DIESEL, "property_id": "P1"}), "real")


# --- API ----------------------------------------------------------------------------------------------------------

USERS = {"admin": ("adm", "adm-pass"), "analyst": ("ana", "ana-pass"), "viewer": ("vie", "vie-pass")}
FIELD = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"id": "F1"},
         "geometry": {"type": "Polygon", "coordinates": [[[-45.1, -20.1], [-45.09, -20.1], [-45.09, -20.09], [-45.1, -20.09], [-45.1, -20.1]]]}}]}


@pytest.fixture(scope="module")
def api():
    st = Settings(database_url="sqlite://", auth_secret="test-secret-test-secret-test-secret-0123", users=USERS, mqtt_host=None)
    with TestClient(create_app(st)) as c:
        h = {k: {"Authorization": "Bearer " + c.post("/auth/login", json={"username": u, "password": p}).json()["access_token"]}
             for k, (u, p) in USERS.items()}
        import json
        r = c.post("/fields/upload", headers=h["analyst"], files={"file": ("f.geojson", json.dumps(FIELD), "application/geo+json")},
                   data={"origin": "real", "property_id": "P1"})
        assert r.status_code == 200, r.text
        yield c, h


def test_cadastro_de_propriedade_e_maquina_exige_admin(api):
    c, h = api
    body = {"property_id": "P1", "name": "Fazenda Exemplo", "origin": "real"}
    assert c.put("/registry/properties/P1", json=body, headers=h["analyst"]).status_code == 403
    assert c.put("/registry/properties/P1", json=body, headers=h["admin"]).status_code == 200
    assert c.put("/registry/properties/P2", json=body, headers=h["admin"]).status_code == 422     # caminho ≠ corpo
    m = {**DIESEL, "property_id": "P1"}
    assert c.put("/registry/machines/TR-1", json=m, headers=h["analyst"]).status_code == 403
    r = c.put("/registry/machines/TR-1", json=m, headers=h["admin"])
    assert r.status_code == 200 and r.json()["kind_label"] == "Trator" and r.json()["energy_label"] == "Diesel"
    bad = c.put("/registry/machines/TR-1", json={**m, "battery_capacity_kwh": 9}, headers=h["admin"])
    assert bad.status_code == 422 and "diesel" in bad.text
    sim = c.put("/registry/machines/TR-X", json={**m, "machine_id": "TR-X", "origin": "simulated"}, headers=h["admin"])
    assert sim.status_code == 422 and "origens diferentes" in sim.text
    assert [p["name"] for p in c.get("/registry/properties", headers=h["viewer"]).json()] == ["Fazenda Exemplo"]
    assert c.get("/registry/machines/TR-1", headers=h["viewer"]).json()["fuel_tank_capacity_l"] == 400.0
    assert c.get("/registry/machines/NOPE", headers=h["viewer"]).status_code == 404


def test_operacao_valida_vinculos(api):
    c, h = api
    c.put("/registry/machines/TR-1", json={**DIESEL, "property_id": "P1"}, headers=h["admin"])
    op = {**OP, "start": T0.isoformat(), "property_id": "P1", "status": "active"}
    assert c.put("/operations/OP-1", json=op, headers=h["viewer"]).status_code == 403
    r = c.put("/operations/OP-1", json=op, headers=h["analyst"])
    assert r.status_code == 200, r.text
    assert c.put("/operations/OP-2", json={**op, "operation_id": "OP-2", "field_id": "NOPE"}, headers=h["analyst"]).status_code == 422
    mix = c.put("/operations/OP-3", json={**op, "operation_id": "OP-3", "origin": "simulated"}, headers=h["analyst"])
    assert mix.status_code == 422 and "não se misturam" in mix.text
    got = c.get("/operations", params={"status": "active"}, headers=h["viewer"]).json()
    assert [o["operation_id"] for o in got] == ["OP-1"] and got[0]["operation_label"] == "Preparo de solo"
    assert c.get("/operations/OP-1", headers=h["viewer"]).json()["status_label"] == "Em andamento"


def test_machines_junta_cadastro_so_da_mesma_origem(api):
    c, h = api
    c.put("/registry/machines/TR-1", json={**DIESEL, "property_id": "P1"}, headers=h["admin"])
    import json
    pts = [{"machine_id": "TR-1", "ts": "2026-05-01T12:00:00Z", "origin": "real", "fuel_level_pct": 50},
           {"machine_id": "TR-1", "ts": "2026-05-01T12:00:00Z", "origin": "simulated", "fuel_level_pct": 40}]
    for p in pts:
        r = c.post("/uploads", headers=h["analyst"], files={"file": ("p.json", json.dumps([p]), "application/json")},
                   data={"kind": "json", "origin": p["origin"], "commit": "true"})
        assert r.status_code == 200, r.text
    ms = {m["origin"]: m for m in c.get("/machines", headers=h["viewer"]).json() if m["machine_id"] == "TR-1"}
    assert ms["real"]["name"] == "Trator 1" and ms["real"]["energy_source"] == "diesel"
    assert ms["simulated"]["name"] is None            # perfil real não é emprestado a telemetria simulada


def test_delete_data_remove_cadastro_somente_da_origem(api):
    c, h = api
    r = c.delete("/data", params={"origin": "simulated"}, headers=h["admin"]).json()
    assert r["machine_profiles"] == 0 and r["operations"] == 0
    assert c.get("/registry/machines/TR-1", headers=h["viewer"]).status_code == 200
