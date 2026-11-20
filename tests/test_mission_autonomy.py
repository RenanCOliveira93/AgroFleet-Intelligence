"""Etapa 4: missão (progresso, ritmo, ETA) e autonomia (verde/amarelo/vermelho/cinza).

Casos de autonomia com números calculados à mão; casos de missão com geometria analítica; cenários A/B/C contra a
verdade-terreno do simulador.
"""
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box, mapping

from agrofleet.analytics.autonomy import AutonomyConfig, Remaining, autonomy, level_class, time_weighted_mean
from agrofleet.analytics.frame import to_frame
from agrofleet.analytics.mission import MissionConfig, mission, operation_consumption, operation_frame
from agrofleet.registry import MachineProfile, OperationPlan
from agrofleet.simulator.operations import (run_scenarios, scenario_a, scenario_b, simulate_operation)
from agrofleet.simulator.fleet import DEFAULT_START
from conftest import PROJ, T0, X0, Y0, make_pts, passes  # noqa: F401

# ---------------------------------------------------------------------------------------------------------------
# autonomia: frames sintéticos mínimos (só as colunas que o cálculo usa)
# ---------------------------------------------------------------------------------------------------------------


def frame(seconds=7200, machine="T-1", **cols):
    n = seconds + 1
    d = {"machine_id": machine, "origin": "simulated", "state": "working",
         "ts": [T0 + timedelta(seconds=i) for i in range(n)]}
    for k, v in cols.items():
        d[k] = v(np.arange(n)) if callable(v) else v
    df = pd.DataFrame(d)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for c in ("fuel_level_pct", "fuel_rate_lph", "battery_soc_pct", "battery_energy_kwh", "battery_power_kw"):
        if c not in df:
            df[c] = np.nan
    for q in ("q_fuel_level", "q_fuel_rate", "q_battery"):
        if q not in df:
            df[q] = None
    return df


def diesel(**kw):
    base = dict(machine_id="T-1", name="Trator", kind="tractor", energy_source="diesel", origin="simulated",
                fuel_tank_capacity_l=600, energy_reserve_pct=10, energy_warning_pct=25, energy_critical_pct=12,
                autonomy_margin_pct=15)
    base.update(kw)
    return MachineProfile(**base)


def electric(**kw):
    base = dict(machine_id="T-1", name="Pulv", kind="sprayer", energy_source="electric", origin="simulated",
                battery_capacity_kwh=60, energy_reserve_pct=10, energy_warning_pct=30, energy_critical_pct=15,
                autonomy_margin_pct=15)
    base.update(kw)
    return MachineProfile(**base)


D50 = dict(fuel_level_pct=50.0, fuel_rate_lph=30.0)    # 300 L no tanque de 600; reserva 60 → 240 utilizáveis; 30 L/h


def test_verde_calculado_a_mao():
    a = autonomy(frame(**D50), diesel(), Remaining(5.0, pessimistic_h=6.0))
    assert a.level_amount == pytest.approx(300) and a.reserve_amount == pytest.approx(60)
    assert a.usable_amount == pytest.approx(240) and a.rate == pytest.approx(30)
    assert a.autonomy_h == pytest.approx(8.0)
    assert a.needed_amount == pytest.approx(150) and a.margin_pct == pytest.approx(60.0)
    assert a.needed_pessimistic_amount == pytest.approx(180) and a.margin_pessimistic_pct == pytest.approx(100 * 60 / 180)
    assert a.projected_end_amount == pytest.approx(150) and a.projected_end_pct == pytest.approx(25.0)
    assert a.status == "green" and a.status_label == "Viável" and a.reasons == []


def test_amarelo_margem_abaixo_da_exigida():
    a = autonomy(frame(**D50), diesel(), Remaining(7.5, pessimistic_h=7.9))   # precisa 225 de 240 → 6,7% < 15%
    assert a.margin_pct == pytest.approx(100 * 15 / 225) and a.status == "yellow"


def test_amarelo_pior_caso_negativo():
    a = autonomy(frame(**D50), diesel(), Remaining(5.0, pessimistic_h=8.5))   # central 60%, pior caso 255 > 240
    assert a.margin_pct == pytest.approx(60) and a.margin_pessimistic_pct < 0 and a.status == "yellow"


def test_amarelo_quando_pior_caso_indeterminado():
    a = autonomy(frame(**D50), diesel(), Remaining(5.0, pessimistic_h=None))
    assert a.status == "yellow" and "worst_case_unknown" in {r["code"] for r in a.reasons}


def test_vermelho_energia_acaba_antes():
    a = autonomy(frame(**D50), diesel(), Remaining(9.0, pessimistic_h=10.0))  # precisa 270 > 240
    assert a.margin_pct == pytest.approx(100 * (240 - 270) / 270) and a.status == "red" and a.status_label == "Insuficiente"


def _codes(a):
    return {r["code"] for r in a.reasons}


@pytest.mark.parametrize("prof,code", [
    (dict(fuel_tank_capacity_l=None), "capacity_not_registered"),
    (dict(energy_reserve_pct=None, energy_critical_pct=None), "reserve_not_registered"),
    (dict(autonomy_margin_pct=None), "margin_not_registered"),
])
def test_cinza_cadastro_incompleto(prof, code):
    a = autonomy(frame(**D50), diesel(**prof), Remaining(5.0, 6.0))
    assert a.status == "gray" and code in _codes(a)
    assert a.level_pct == pytest.approx(50)          # o que É calculável continua sendo informado


def test_cinza_sem_tempo_restante_e_dado_velho():
    assert "remaining_unknown" in _codes(a := autonomy(frame(**D50), diesel(), None)) and a.status == "gray"
    a = autonomy(frame(**D50), diesel(), Remaining(5.0, 6.0), data_age_s=600)
    assert a.status == "gray" and "stale_data" in _codes(a)


def test_cinza_sensor_falho_nao_vira_zero():
    a = autonomy(frame(**D50, q_fuel_level="failed"), diesel(), Remaining(5.0, 6.0))
    assert a.status == "gray" and "no_level_reading" in _codes(a)
    assert a.level_pct is None and a.level_amount is None


def test_cinza_sem_taxa_e_nivel_parado():
    a = autonomy(frame(fuel_level_pct=50.0), diesel(), Remaining(5.0, 6.0))
    assert a.status == "gray" and "rate_not_positive" in _codes(a)


def test_taxa_pela_queda_do_nivel_sem_sensor_de_taxa():
    # 5 %/h de 600 L = 30 L/h; termina em 50%
    lvl = lambda i: 50.0 + 5.0 * (7200 - i) / 3600
    a = autonomy(frame(fuel_level_pct=lvl), diesel(), Remaining(5.0, 6.0))
    assert a.rate_source == "level_slope" and a.rate == pytest.approx(30, rel=0.01)
    assert "rate_from_level_slope" in _codes(a) and a.status == "green"


def test_fontes_de_taxa_discordantes_sao_sinalizadas():
    lvl = lambda i: 50.0 + 5.0 * (7200 - i) / 3600                   # nível cai 30 L/h
    a = autonomy(frame(fuel_level_pct=lvl, fuel_rate_lph=45.0), diesel(), Remaining(5.0, 6.0))
    assert a.rate_source == "fuel_rate_lph" and a.rate_cross_check_pct == pytest.approx(50, abs=2)
    assert "rate_sources_disagree" in _codes(a)


def test_abastecimento_na_janela_reinicia_a_taxa():
    # tanque cheio aos 6600 s; antes disso 45 L/h, depois 30 L/h → a janela só usa o trecho posterior
    lvl = lambda i: np.where(i < 6600, 20.0, 90.0)
    rate = lambda i: np.where(i < 6600, 45.0, 30.0)
    a = autonomy(frame(fuel_level_pct=lvl, fuel_rate_lph=rate), diesel(), Remaining(5.0, 6.0),
                 AutonomyConfig(min_rate_cover_s=300))
    assert "refill_in_window" in _codes(a) and a.rate == pytest.approx(30, rel=0.02)
    assert a.level_pct == pytest.approx(90)


def test_eletrico_com_kwh_reportado():
    a = autonomy(frame(battery_energy_kwh=30.0, battery_soc_pct=50.0, battery_power_kw=6.0), electric(), Remaining(3.0, 3.5))
    assert a.unit == "kWh" and a.rate_unit == "kW" and a.level_source == "battery_energy_kwh"
    assert a.usable_amount == pytest.approx(24) and a.autonomy_h == pytest.approx(4.0)
    assert a.margin_pct == pytest.approx(100 * (24 - 18) / 18) and a.status == "green"


def test_eletrico_so_soc_usa_capacidade_cadastrada():
    a = autonomy(frame(battery_soc_pct=50.0, battery_power_kw=6.0), electric(), Remaining(3.0, 3.5))
    assert a.level_amount == pytest.approx(30) and "battery_soc_pct" in a.level_source


def test_recarga_liquida_nao_gera_autonomia():
    a = autonomy(frame(battery_energy_kwh=30.0, battery_power_kw=-2.0), electric(), Remaining(3.0, 3.5))
    assert a.autonomy_h is None and a.status == "gray" and "rate_not_positive" in _codes(a)


def test_missao_coberta_e_verde():
    a = autonomy(frame(**D50), diesel(), Remaining(0.0, 0.0))
    assert a.status == "green" and "mission_done" in _codes(a) and a.margin_pct is None


def test_classe_de_nivel_separada_da_viabilidade():
    p = diesel()
    assert [level_class(v, p) for v in (60, 25, 20, 12, 5, None)] == ["normal", "low", "low", "critical", "critical", "unknown"]
    assert level_class(60, diesel(energy_warning_pct=None, energy_critical_pct=None)) == "not_configured"
    a = autonomy(frame(fuel_level_pct=20.0, fuel_rate_lph=10.0), diesel(), Remaining(1.0, 1.2))
    assert a.level_class == "low" and a.status == "green"     # nível baixo, mas suficiente para terminar


def test_fonte_desconhecida():
    p = MachineProfile(machine_id="T-1", name="X", origin="simulated")
    a = autonomy(frame(**D50), p, Remaining(5.0, 6.0))
    assert a.status == "gray" and "energy_source_unknown" in _codes(a)


# ---------------------------------------------------------------------------------------------------------------
# missão: geometria analítica
# ---------------------------------------------------------------------------------------------------------------

def _field_ll(x0, y0, x1, y1):
    from agrofleet.geo import transform_geom
    poly = box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1)
    return mapping(transform_geom(poly, PROJ.inv))


def _op(**kw):
    base = dict(operation_id="OP-1", name="Teste", machine_id="T-1", field_id="F-1", start=T0, status="active",
                origin="simulated")
    base.update(kw)
    return OperationPlan(**base)


FIELD = _field_ll(0, 0, 100, 500)       # 5 ha


def strips(n, spacing, x_start=12.0, gap_s=1000):
    """n passadas verticais de 500 m, cada uma em seu próprio trecho (sem faixa de manobra entre elas)."""
    pts = []
    for k in range(n):
        x = x_start + k * spacing
        wp = [(x, 0), (x, 500)] if k % 2 == 0 else [(x, 500), (x, 0)]
        pts += make_pts(wp, t0=T0 + timedelta(seconds=k * gap_s))
    return pts


def test_sobreposicao_nao_infla_o_progresso():
    # 4 passadas de 24 m a cada 22 m (2 m de sobreposição), dentro de um talhão de 100 × 500 m
    df = to_frame(strips(4, 22.0))
    m = mission(df, _op(), FIELD)
    assert m.planned_source == "field_geometry" and m.planned_ha == pytest.approx(5.0, rel=1e-6)
    assert m.gross_ha > m.unique_ha and m.worked_ha <= m.unique_ha + 1e-9
    assert m.gross_ha - m.unique_ha == pytest.approx(3 * 2 * 495 / 1e4, rel=0.05)     # 3 faixas de 2 m sobrepostas
    assert m.progress_pct == pytest.approx(100 * m.worked_ha / 5.0)


def test_repasse_nao_conta_duas_vezes():
    p1 = make_pts([(12, 0), (12, 500)])
    p2 = make_pts([(12, 500), (12, 0)], t0=T0 + timedelta(seconds=200))   # mesma faixa, de volta
    one = mission(to_frame(p1), _op(), FIELD)
    two = mission(to_frame(p1 + p2), _op(), FIELD)
    assert two.gross_ha == pytest.approx(2 * one.gross_ha, rel=0.01)
    # a volta cobre os 5 m finais que a ida a 1 Hz não fecha (1 segmento de 5 × 24 m); nada além disso
    assert one.worked_ha <= two.worked_ha <= one.worked_ha + 5 * 24 / 1e4 + 1e-6


def test_faixa_fora_do_talhao_nao_conta():
    df = to_frame(make_pts([(-50, 0), (-50, 500)]))            # passada inteira a oeste do talhão
    m = mission(df, _op(), FIELD)
    assert m.worked_ha == pytest.approx(0, abs=1e-6) and m.outside_field_ha == pytest.approx(m.unique_ha)


def test_area_planejada_do_cadastro_tem_precedencia():
    m = mission(to_frame(make_pts(passes(2, 24.0))), _op(planned_area_ha=4.0), FIELD)
    assert m.planned_source == "operation" and m.planned_ha == 4.0
    assert m.progress_pct == pytest.approx(100 * m.worked_ha / 4.0)


def test_sem_geometria_do_talhao_e_sinalizado():
    m = mission(to_frame(make_pts(passes(2, 24.0))), _op(planned_area_ha=4.0), None)
    assert m.worked_source == "union_total" and "no_field_geometry" in {r["code"] for r in m.reliability}


def test_sem_area_planejada_nao_ha_eta():
    m = mission(to_frame(make_pts(passes(2, 24.0))), _op(), None)
    assert m.planned_ha is None and m.progress_pct is None and m.eta is None
    assert "planned_area_unknown" in {r["code"] for r in m.eta_reasons}


def test_inicio_curto_nao_publica_eta():
    m = mission(to_frame(make_pts(passes(2, 24.0))), _op(), FIELD)     # ~200 s de operação
    assert m.eta is None and "elapsed_too_short" in {r["code"] for r in m.eta_reasons}


def test_outra_origem_e_outra_maquina_nao_entram():
    real = to_frame(make_pts(passes(2, 24.0)))
    real["origin"] = "real"
    other = to_frame(make_pts(passes(2, 24.0), machine="T-2"))
    m = mission(pd.concat([real, other]), _op(), FIELD)
    assert m.worked_ha == 0 and "no_data" in {r["code"] for r in m.eta_reasons}


# ---------------------------------------------------------------------------------------------------------------
# cenários do simulador contra a verdade-terreno
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def run_a():
    full = simulate_operation(scenario_a())
    cut = DEFAULT_START + timedelta(hours=3)
    df = to_frame([p for p in full.points if p.ts <= cut])
    part = simulate_operation(scenario_a(), max_seconds=3 * 3600)     # mesma semente: prefixo idêntico
    return full, part, df


def test_cenario_a_verde_com_eta_proxima_da_verdade(run_a):
    full, part, df = run_a
    m = mission(df, part.operation, full.field_lonlat)
    sums = sum(s["new_ha"] for s in m.series)
    # série soma a área trabalhada (o corte em intervalos tira a junção arredondada no ponto de corte: ~m²)
    assert sums == pytest.approx(m.worked_ha, rel=1e-4)
    assert len(m.series) == 18 and m.eta_reasons == []
    true_h = full.truth["executed_seconds"] / 3600
    assert m.elapsed_h + m.remaining_h == pytest.approx(true_h, rel=0.03)  # ETA a ±3% da duração real
    lo, hi = m.remaining_h_range
    assert lo <= m.remaining_h <= hi
    a = autonomy(df, part.machine, m.remaining())
    assert a.status == "green" and a.rate_source == "fuel_rate_lph" and abs(a.rate_cross_check_pct) < 5
    consumed_after = part.truth["fuel"]["end_l"] - full.truth["fuel"]["end_l"]
    assert a.needed_amount == pytest.approx(consumed_after, rel=0.10)      # necessidade a ±10% do consumo real
    assert a.level_amount == pytest.approx(part.truth["fuel"]["end_l"], abs=3)   # quantização de 0,4% × 600 L


def test_cenario_a_cobertura_final_sem_micro_buracos(run_a):
    full, _, _ = run_a
    m = mission(to_frame(full.points), full.operation, full.field_lonlat)
    assert m.worked_ha == pytest.approx(full.truth["planned_coverage_in_field_ha"], rel=0.005)
    assert m.status == "completed" and m.eta == full.operation.end.isoformat()


@pytest.mark.parametrize("hours", [1.0, 3.0])
def test_cenario_b_vermelho_antes_do_retorno(hours):
    r = simulate_operation(scenario_b(), max_seconds=hours * 3600)
    df = to_frame(r.points)
    m = mission(df, r.operation, r.field_lonlat)
    a = autonomy(df, r.machine, m.remaining())
    assert a.status == "red" and a.margin_pct < 0 and a.unit == "kWh"
    assert a.level_source == "battery_energy_kwh"


def test_cenario_b_verdade_confirma_retorno_sem_conclusao():
    r = simulate_operation(scenario_b())
    assert r.truth["energy"]["returned_to_base"] and not r.truth["completed"]


def test_cenario_c_lacuna_aparece_como_sem_dado():
    (c,) = run_scenarios(("C",), max_seconds=1.5 * 3600)
    seen, pts = set(), []
    for dv in c.truth["_deliveries"]:
        if not dv.duplicate and id(dv.point) not in seen:
            seen.add(id(dv.point)); pts.append(dv.point)
    m = mission(to_frame(pts), c.operation, c.field_lonlat)
    assert m.no_data_h * 3600 == pytest.approx(90, abs=3)             # 90 s perdidos, nem inventados nem ignorados
    full = mission(to_frame(c.points), c.operation, c.field_lonlat)
    assert m.worked_ha < full.worked_ha                                 # área perdida não é interpolada


# ---------------------------------------------------------------------------------------------------------------
# consumo médio da operação (Etapa 8): média ponderada pelo tempo observado dos intervalos de 10 min
# ---------------------------------------------------------------------------------------------------------------

def _b(obs_h, fuel=None, power=None):
    return {"observed_h": obs_h, "fuel_rate_lph": fuel, "battery_power_kw": power, "complete": True}


def test_consumo_diesel_ponderado_pelo_tempo_observado():
    # 30 L/h por 1/6 h, 20 L/h por 1/12 h → (5 + 5/3) / (1/4) = 26,67 L/h (média simples daria 25)
    c = operation_consumption([_b(1 / 6, fuel=30.0), _b(1 / 12, fuel=20.0)], "diesel")
    assert c["source"] == "fuel_rate_lph" and c["unit"] == "L/h" and c["reason"] is None
    assert c["avg"] == pytest.approx(80 / 3) and c["observed_h"] == pytest.approx(0.25) and c["intervals"] == 2
    assert "ponderada pelo tempo observado" in c["basis"]


def test_consumo_eletrico_usa_potencia_e_ignora_combustivel():
    c = operation_consumption([_b(1 / 6, fuel=99.0, power=40.0), _b(1 / 6, power=-10.0)], "electric")   # recarga
    assert c["source"] == "battery_power_kw" and c["unit"] == "kW"
    assert c["avg"] == pytest.approx(15.0) and "Potência líquida" in c["basis"]


def test_consumo_intervalos_sem_taxa_ou_sem_observacao_ficam_fora():
    s = [_b(1 / 6, fuel=30.0), _b(1 / 6), _b(0.0, fuel=500.0), _b(1 / 6, fuel=None, power=12.0), _b(1 / 12, fuel=36.0)]
    c = operation_consumption(s, "diesel")
    assert c["intervals"] == 2 and c["observed_h"] == pytest.approx(0.25)     # nada é preenchido nem zerado
    assert c["avg"] == pytest.approx((30 / 6 + 36 / 12) / 0.25)


def test_consumo_sem_taxa_reportada_e_nulo_com_motivo():
    c = operation_consumption([_b(1 / 6), _b(1 / 6)], "diesel")
    assert c["avg"] is None and c["observed_h"] == 0 and c["reason"] == "Máquina não reporta consumo."
    assert c["source"] == "fuel_rate_lph"                       # informa qual taxa faltou
    e = operation_consumption([], "electric")
    assert e["avg"] is None and e["reason"] == "Sem telemetria no período da operação."
    u = operation_consumption([_b(1 / 6)], "unknown")
    assert u["source"] is None and u["unit"] is None and u["avg"] is None and u["reason"]


def test_consumo_fonte_nao_cadastrada_usa_a_taxa_reportada():
    c = operation_consumption([_b(1 / 6, power=20.0), _b(1 / 6, power=30.0), _b(1 / 6, fuel=10.0)], None)
    assert c["source"] == "battery_power_kw" and c["avg"] == pytest.approx(25.0) and "não cadastrada" in c["basis"]


def _sample_mean(df, op, col, q):
    g = operation_frame(df, op)
    return time_weighted_mean(g, col, q, MissionConfig().coverage.max_gap_s)


def test_consumo_igual_a_media_amostra_a_amostra_cenario_a(run_a):
    full, part, df = run_a
    m = mission(df, part.operation, full.field_lonlat)
    c = operation_consumption(m.series, "diesel")
    ref, cover = _sample_mean(df, part.operation, "fuel_rate_lph", "q_fuel_rate")
    assert c["avg"] == pytest.approx(ref, rel=2e-3)                       # só difere na amostra de fronteira
    assert c["observed_h"] == pytest.approx(cover / 3600, rel=2e-3)


def test_consumo_igual_a_media_amostra_a_amostra_cenario_b():
    r = simulate_operation(scenario_b(), max_seconds=1.0 * 3600)
    df = to_frame(r.points)
    m = mission(df, r.operation, r.field_lonlat)
    c = operation_consumption(m.series, r.machine.energy_source)
    ref, _ = _sample_mean(df, r.operation, "battery_power_kw", "q_battery")
    assert c["source"] == "battery_power_kw" and c["avg"] == pytest.approx(ref, rel=5e-3)


# ---------------------------------------------------------------------------------------------------------------
# API: /operations/{id}/status e /geometry
# ---------------------------------------------------------------------------------------------------------------

USERS = {"admin": ("adm", "adm-pass"), "analyst": ("ana", "ana-pass"), "viewer": ("vie", "vie-pass")}


@pytest.fixture(scope="module")
def api_ops():
    from fastapi.testclient import TestClient
    from backend.app.config import Settings
    from backend.app.main import create_app
    st = Settings(database_url="sqlite://", auth_secret="test-secret-test-secret-test-secret-0123", users=USERS, mqtt_host=None)
    with TestClient(create_app(st)) as c:
        h = {k: {"Authorization": "Bearer " + c.post("/auth/login", json={"username": u, "password": p}).json()["access_token"]}
             for k, (u, p) in USERS.items()}
        r = c.post("/demo/operations/load", params={"scenarios": "A,B,C", "hours": 1.5, "start": DEFAULT_START.isoformat()},
                   headers=h["admin"])
        assert r.status_code == 200, r.text
        yield c, h


AS_OF = (DEFAULT_START + timedelta(hours=1.5)).isoformat()


def test_api_status_reproduz_o_calculo_puro(api_ops):
    c, h = api_ops
    a = c.get("/operations/OP-SIM-A/status", params={"as_of": AS_OF}, headers=h["viewer"]).json()
    r = simulate_operation(scenario_a(), max_seconds=1.5 * 3600)
    df = to_frame(r.points)
    m = mission(df, r.operation, r.field_lonlat)
    au = autonomy(df, r.machine, m.remaining())
    assert a["mode"] == "simulated" and a["mode_label"] == "MODO SIMULAÇÃO"
    assert a["mission"]["worked_ha"] == pytest.approx(m.worked_ha, rel=1e-6)
    assert a["mission"]["eta"] is not None and a["mission"]["eta_reasons"] == []
    assert a["autonomy"]["status"] == au.status == "green"
    assert a["autonomy"]["margin_pct"] == pytest.approx(au.margin_pct, rel=1e-6)
    assert a["machine"]["name"] == "Trator TR-320 (simulado)" and a["field"]["name"] == "Talhão 03"
    assert a["connection"]["basis"] == "as_of" and a["snapshot"]["fuel_level_pct"] is not None
    assert len(a["series"]) == 9 and all(s["fuel_rate_lph"] for s in a["series"])
    b = c.get("/operations/OP-SIM-B/status", params={"as_of": AS_OF}, headers=h["viewer"]).json()
    assert b["autonomy"]["status"] == "red" and b["autonomy"]["unit"] == "kWh"
    # consumo médio (Etapa 8): calculado no backend a partir da mesma série
    ca, cb = a["consumption"], b["consumption"]
    assert ca == operation_consumption(m.series, "diesel")
    assert ca["source"] == "fuel_rate_lph" and ca["unit"] == "L/h" and ca["avg"] > 0 and ca["reason"] is None
    assert ca["avg"] == pytest.approx(_sample_mean(df, r.operation, "fuel_rate_lph", "q_fuel_rate")[0], rel=2e-3)
    assert ca["observed_h"] == pytest.approx(sum(s["observed_h"] for s in a["series"]), rel=1e-9)
    assert cb["source"] == "battery_power_kw" and cb["unit"] == "kW" and cb["avg"] is not None


def test_api_status_sem_as_of_dado_antigo_fica_cinza(api_ops):
    c, h = api_ops
    a = c.get("/operations/OP-SIM-A/status", headers=h["viewer"]).json()
    # telemetria de março, recém-importada: o enlace "chegou agora", mas o dado é velho → sem conexão e cinza
    assert a["connection"]["state"] == "offline" and a["connection"]["age_s"] > 3600
    assert a["autonomy"]["status"] == "gray" and "stale_data" in {r["code"] for r in a["autonomy"]["reasons"]}
    assert a["mission"]["eta"] is None and "stale_data" in {r["code"] for r in a["mission"]["eta_reasons"]}
    assert a["mission"]["worked_ha"] > 0        # o que já foi feito continua informado


def test_api_status_cenario_c_mostra_a_lacuna(api_ops):
    c, h = api_ops
    s = c.get("/operations/OP-SIM-C/status", params={"as_of": AS_OF}, headers=h["viewer"]).json()
    assert s["mission"]["no_data_h"] * 3600 == pytest.approx(90, abs=3)


def test_api_geometria(api_ops):
    from shapely.geometry import shape
    from agrofleet.geo import Projector, transform_geom
    c, h = api_ops
    st = c.get("/operations/OP-SIM-A/status", params={"as_of": AS_OF}, headers=h["viewer"]).json()
    g = c.get("/operations/OP-SIM-A/geometry", params={"as_of": AS_OF}, headers=h["viewer"]).json()
    assert g["mode_label"] == "MODO SIMULAÇÃO" and g["field"] and g["worked"] and g["pending"]
    fld = shape(g["field"]); cen = fld.centroid
    pj = Projector.for_points([cen.x], [cen.y])
    area = lambda gj: transform_geom(shape(gj), pj.fwd).area / 1e4
    assert area(g["worked"]) == pytest.approx(st["mission"]["worked_ha"], rel=0.01)     # simplificação só de desenho
    assert area(g["worked"]) + area(g["pending"]) == pytest.approx(area(g["field"]), rel=0.005)
    assert g["track"]["type"] == "MultiLineString" and g["last"]["heading_deg"] is not None
    assert g["display"]["note"].startswith("Geometria simplificada")


def test_api_operacao_inexistente_e_autenticacao(api_ops):
    c, h = api_ops
    assert c.get("/operations/NOPE/status", headers=h["viewer"]).status_code == 404
    assert c.get("/operations/NOPE/geometry", headers=h["viewer"]).status_code == 404
    assert c.get("/operations/OP-SIM-A/status").status_code == 401
