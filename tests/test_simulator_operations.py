"""Etapa 3 — simulador v2: geometria do talhão, cenários A/B/C, coerência sensor × verdade e falhas de enlace.

Os testes verificam o SIMULADOR (que o que ele reporta é coerente com o que ele "sabe"), não máquinas reais.
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString

from agrofleet.analytics.coverage import coverage
from agrofleet.analytics.frame import to_frame
from agrofleet.simulator.operations import (FieldSpec, LinkFault, apply_link_faults, make_field, plan_passes, run_scenarios,
                                            scenario_a, scenario_b, simulate_operation)
from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture(scope="module")
def run_a():
    return simulate_operation(scenario_a(), max_seconds=3600)


@pytest.fixture(scope="module")
def run_b():
    return simulate_operation(scenario_b())          # até voltar à base


@pytest.fixture(scope="module")
def run_c():
    return run_scenarios(("C",))[0]                   # completo (60 ha)


# --- geometria ------------------------------------------------------------------------------------------------------

def test_talhao_irregular_area_exata_e_deterministico():
    s = FieldSpec("F", "F", 120.0, (0.0, 0.0), seed=3)
    p = make_field(s)
    assert p.area / 1e4 == pytest.approx(120.0, rel=1e-9) and p.is_valid
    assert p.equals(make_field(s)) and not p.equals(make_field(FieldSpec("F", "F", 120.0, (0.0, 0.0), seed=4)))
    assert len(p.exterior.coords) > 12                                   # não é um retângulo


def test_passadas_recortadas_paralelas_e_alternadas():
    poly = make_field(FieldSpec("F", "F", 60.0, (0.0, 0.0), angle_deg=35.0, seed=1))
    ps = plan_passes(poly, 12.0, 35.0)
    for a, b in ps:
        assert poly.buffer(0.01).contains(LineString([a, b]))            # dentro do talhão
        ang = np.degrees(np.arctan2(*(b - a)[::-1])) % 180
        assert ang == pytest.approx(35.0, abs=0.01)                      # direção das passadas
    d = [LineString(ps[i]).distance(LineString(ps[i + 1])) for i in range(len(ps) - 1)]
    assert np.allclose(d, 12.0, atol=0.01)                               # espaçamento = largura (sem sobreposição nominal)
    dirs = [np.sign(np.dot(b - a, ps[0][1] - ps[0][0])) for a, b in ps]
    assert all(dirs[i] == -dirs[i + 1] for i in range(len(dirs) - 1))    # boustrophedon


# --- cenário A: trator diesel --------------------------------------------------------------------------------------

def test_a_deterministico_e_simulado(run_a):
    again = simulate_operation(scenario_a(), max_seconds=3600)
    assert [p.model_dump() for p in again.points[:500]] == [p.model_dump() for p in run_a.points[:500]]
    other = simulate_operation(scenario_a(), seed=8, max_seconds=600)
    assert other.points[300].fuel_rate_lph != run_a.points[300].fuel_rate_lph
    assert {p.origin for p in run_a.points} == {"simulated"}
    assert run_a.machine.origin == run_a.operation.origin == run_a.property.origin == "simulated"


def test_a_so_reporta_sensores_de_trator(run_a):
    p = run_a.points[1000]
    assert p.engine_rpm is not None and p.fuel_level_pct is not None and p.fuel_rate_lph is not None
    assert p.battery_soc_pct is None and p.flow_lpm is None and p.tank_level_l is None    # sem bateria nem calda


def test_a_integral_do_consumo_reportado_bate_com_a_verdade(run_a):
    df = to_frame(run_a.points)
    dt = df["ts"].diff().dt.total_seconds().shift(-1).fillna(0).to_numpy()
    litros = float(np.sum(df["fuel_rate_lph"].to_numpy() * dt / 3600))
    assert litros == pytest.approx(run_a.truth["fuel"]["consumed_l"], rel=0.01)
    queda_pct = df["fuel_level_pct"].iloc[:60].median() - df["fuel_level_pct"].iloc[-60:].median()
    assert queda_pct == pytest.approx(run_a.truth["fuel"]["consumed_l"] / 600 * 100, abs=0.5)   # nível ruidoso e quantizado
    lv = df["fuel_level_pct"].to_numpy()
    assert np.allclose(np.round(lv / 0.4) * 0.4, lv)                                            # resolução 0,4 %


def test_a_carga_e_consumo_variam_com_o_modo(run_a):
    df = to_frame(run_a.points)
    by = df.groupby("state")[["engine_load_pct", "fuel_rate_lph", "engine_rpm"]].mean()
    assert by.loc["working", "engine_load_pct"] > by.loc["transport", "engine_load_pct"] + 25
    assert by.loc["working", "fuel_rate_lph"] > by.loc["transport", "fuel_rate_lph"]
    w = df[df.state == "working"]["engine_load_pct"]
    assert w.max() - w.min() > 15                                         # carga variável (solo), não constante
    hours = df["engine_hours_h"].iloc[-1] - df["engine_hours_h"].iloc[0]
    assert hours == pytest.approx(3600 / 3600, abs=0.06)


def test_a_rumo_coerente_com_o_deslocamento_e_ausente_parado(run_a):
    df = to_frame(run_a.points)
    w = df[(df.state == "working") & (df.speed_ms > 1.5)]
    h = w["heading_deg"].to_numpy() % 180                                 # passadas a 35° do leste = 55°/235° de rumo
    assert np.median(np.minimum(np.abs(h - 55), 180 - np.abs(h - 55))) < 3
    assert df.loc[df.speed_ms < 0.2, "heading_deg"].isna().all()          # COG inválido parado: não reportado


def test_a_registro_gerado(run_a):
    m, op = run_a.machine, run_a.operation
    assert m.kind == "tractor" and m.energy_source == "diesel" and m.fuel_tank_capacity_l == 600.0
    assert m.maintenance_interval_h == 250.0 and m.last_maintenance_engine_h == 4582.0   # regra configurada (não inventada)
    assert op.status == "active" and op.end is None and op.field_id == "SIM-TALHAO-03"


def test_a_completo_cobre_o_planejado():
    r = simulate_operation(scenario_a())
    assert r.truth["completed"] and r.operation.status == "completed" and r.operation.end is not None
    fuel = r.truth["fuel"]
    assert fuel["end_l"] > 600 * 0.10                                     # cenário A: autonomia suficiente com reserva
    assert 30 < fuel["mean_working_rate_lph"] < 40


# --- cenário B: pulverizador elétrico autônomo -----------------------------------------------------------------------

def test_b_bateria_cai_e_volta_a_base_sem_concluir(run_b):
    t = run_b.truth
    assert not t["completed"] and t["energy"]["returned_to_base"] and run_b.operation.status == "active"
    ev = next(e for e in t["events"] if e["type"] == "energy_return_to_base")
    assert ev["soc_pct"] <= 12.0
    df = to_frame(run_b.points)
    assert (np.diff(df["battery_energy_kwh"].to_numpy()) <= 0.1 + 1e-9).all()       # só descarrega (tolerância da resolução)
    after = df[df.ts > ev["at"]]
    assert not (after.state == "working").any()
    dt = df["ts"].diff().dt.total_seconds().shift(-1).fillna(0).to_numpy()
    kwh = float(np.sum(df["battery_power_kw"].to_numpy() * dt / 3600))
    assert kwh == pytest.approx(t["energy"]["consumed_kwh"], rel=0.02)


def test_b_so_reporta_sensores_de_eletrico_e_calda(run_b):
    p = next(p for p in run_b.points if p.state.value == "working")
    assert p.battery_soc_pct is not None and p.flow_lpm is not None and p.tank_level_l is not None
    assert p.engine_rpm is None and p.fuel_level_pct is None and p.fuel_rate_lph is None
    assert run_b.machine.autonomous and run_b.machine.battery_capacity_kwh == 60.0


def test_b_reabastece_calda(run_b):
    df = to_frame(run_b.points)
    assert (df.state == "refilling").any()
    after_refill = df["tank_level_l"][df.state.shift(1).eq("refilling") & df.state.ne("refilling")]
    assert (after_refill > 990).all()


# --- cenário C: falhas de enlace -----------------------------------------------------------------------------------

def test_c_agenda_de_entrega(run_c):
    dl = run_c.truth["_deliveries"]
    t0 = run_c.points[0].ts
    s = lambda p: (p.ts - t0).total_seconds()
    assert not any(1200 <= s(d.point) < 1290 for d in dl)                                   # perdidos
    buf = [d for d in dl if 2100 <= s(d.point) < 2340]
    assert len(buf) == 240 and all(d.deliver_s >= 2340 for d in buf)                         # reenviados depois
    order = [s(d.point) for d in dl]
    assert any(b < a for a, b in zip(order, order[1:]))                                     # chegam fora de ordem (latência)
    dups = [d for d in dl if d.duplicate]
    assert dups and all(3600 <= s(d.point) < 4200 for d in dups)
    assert run_c.truth["delivered_unique"] == len(run_c.points) - 90
    assert {e["type"] for e in run_c.truth["link_events"]} == {"link_outage_lost", "link_outage_buffered", "link_latency", "link_duplicate"}


def test_falhas_nao_alteram_as_amostras():
    r = simulate_operation(scenario_a(), max_seconds=300)
    dl, _ = apply_link_faults(r.points, [LinkFault("latency", 0, 10_000, latency_s=10)])
    assert all(d.point is p for d, p in zip(dl, r.points)) and all(d.deliver_s >= 10 for d in dl)


def test_c_cobertura_estimada_vs_planejada(run_c):
    c = coverage(to_frame(run_c.points), field_lonlat=run_c.field_lonlat, with_geojson=False).summary()
    planned = run_c.truth["planned_coverage_in_field_ha"]
    assert -0.03 < (c["covered_in_field_ha"] - planned) / planned < 0.005     # mesmo viés de subestimação já documentado


# --- API e publicador ----------------------------------------------------------------------------------------------

USERS = {"admin": ("adm", "adm-pass"), "analyst": ("ana", "ana-pass"), "viewer": ("vie", "vie-pass")}


def test_demo_operations_load():
    st = Settings(database_url="sqlite://", auth_secret="test-secret-test-secret-test-secret-0123", users=USERS, mqtt_host=None)
    with TestClient(create_app(st)) as c:
        h = {k: {"Authorization": "Bearer " + c.post("/auth/login", json={"username": u, "password": p}).json()["access_token"]}
             for k, (u, p) in USERS.items()}
        assert c.post("/demo/operations/load", headers=h["analyst"]).status_code == 403
        assert c.post("/demo/operations/load", params={"scenarios": "Z"}, headers=h["admin"]).status_code == 422
        r = c.post("/demo/operations/load", params={"scenarios": "A,B,C", "hours": 0.5}, headers=h["admin"]).json()
        assert r["origin"] == "simulated" and r["stored"] == 1801 + 1801 + (1801 - 90)            # t = 0..1800 s inclusive; C perde 90
        assert c.post("/demo/operations/load", params={"scenarios": "A,B,C", "hours": 0.5, "start": r["start"]},
                      headers=h["admin"]).json()["stored"] == 0                                       # idempotente
        ms = {m["machine_id"]: m for m in c.get("/machines", headers=h["viewer"]).json()}
        assert ms["SIM-TR-320"]["name"] == "Trator TR-320 (simulado)" and ms["SIM-TR-320"]["fuel_level_pct"] is not None
        assert ms["SIM-PE-07"]["energy_source"] == "electric" and ms["SIM-PE-07"]["battery_soc_pct"] is not None
        ops = c.get("/operations", headers=h["viewer"]).json()
        assert {o["operation_id"] for o in ops} == {"OP-SIM-A", "OP-SIM-B", "OP-SIM-C"} and {o["status"] for o in ops} == {"active"}
        fl = {f["id"]: f for f in c.get("/fields", headers=h["viewer"]).json()}
        assert fl["SIM-TALHAO-03"]["name"] == "Talhão 03" and fl["SIM-TALHAO-03"]["origin"] == "simulated"
        assert [p["name"] for p in c.get("/registry/properties", headers=h["viewer"]).json()] == ["Fazenda Santa Clara (simulada)"]
        cov = c.get("/coverage", params={"machine_ids": "SIM-TR-320", "field_id": "SIM-TALHAO-03"}, headers=h["viewer"]).json()
        assert 0 < cov["summary"]["coverage_pct_of_field"] < 10                                        # 30 min de 12 h
        d = c.delete("/data", params={"origin": "simulated"}, headers=h["admin"]).json()
        assert d["operations"] == 3 and d["machine_profiles"] == 3 and d["properties"] == 1


def _publish_module():
    spec = importlib.util.spec_from_file_location("publish", Path(__file__).resolve().parents[1] / "simulator" / "publish.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_publicador_agenda_cenarios_e_preserva_frota():
    from datetime import datetime, timezone
    pub = _publish_module()
    st = datetime(2026, 5, 1, tzinfo=timezone.utc)
    fleet = pub.schedule("fleet", 7, st, 3 * 3600)
    assert len(fleet) == 11331 and fleet[0][0] == 0.0                       # comportamento original
    plan = pub.schedule("A,C", 7, st, 1800)
    assert [x[0] for x in plan] == sorted(x[0] for x in plan)
    assert {p.machine_id for _, p in plan} == {"SIM-TR-320", "SIM-TR-410"}
