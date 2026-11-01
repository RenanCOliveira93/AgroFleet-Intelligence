import numpy as np
import pandas as pd
import pytest

from agrofleet.analytics import anomalies as A
from agrofleet.analytics import roi
from agrofleet.analytics.coverage import coverage
from agrofleet.analytics.frame import to_frame
from agrofleet.analytics.metrics import machine_metrics, report
from agrofleet.simulator.fleet import simulate
from conftest import make_pts


@pytest.fixture(scope="module")
def sim():
    pts, truth = simulate()
    return to_frame(pts), truth


def test_cobertura_estimada_vs_verdade_do_simulador(sim):
    df, truth = sim
    for mid, fid in [("SIM-01", "TALHAO-A"), ("SIM-02", "TALHAO-B"), ("SIM-04", "TALHAO-D")]:   # sem perda de GNSS
        t = truth.areas[mid]
        c = coverage(df[df.machine_id == mid], field_lonlat=truth.fields[fid]).summary()
        assert c["gross_ha"] == pytest.approx(t["planned_gross_ha"], rel=0.02)
        assert c["unique_ha"] == pytest.approx(t["planned_unique_ha"], rel=0.02)
        assert c["overlap_pct_of_gross"] == pytest.approx(100 * (1 - t["planned_unique_ha"] / t["planned_gross_ha"]), abs=1.0)


def test_perda_gnss_subestima_cobertura_e_e_declarada(sim):
    df, truth = sim
    c = coverage(df[df.machine_id == "SIM-03"]).summary()
    assert c["unique_ha"] < 0.95 * truth.areas["SIM-03"]["planned_unique_ha"]      # área perdida é visível, não interpolada
    assert c["excluded_segments"]["no_position"] > 200


def test_metricas_tempo_e_volume(sim):
    df, truth = sim
    m = machine_metrics(df[df.machine_id == "SIM-02"])
    assert m["time_h"]["fault"] == pytest.approx(420 / 3600, rel=0.02)
    assert sum(m["time_h"].values()) == pytest.approx(m["span_h"], rel=0.01)
    assert m["volume_from_flow_l"] == pytest.approx(m["volume_from_counter_l"], rel=0.01)   # duas fontes independentes concordam
    assert m["productivity"]["unique_ha_per_working_h"] > 0 and m["distance_km"] > 10


def test_lacunas_vao_para_no_data_e_distancia_nao_cruza_lacuna():
    pts = make_pts([(0, 0), (0, 300)])
    from datetime import timedelta
    shifted = [p.model_copy(update={"ts": p.ts + timedelta(seconds=600)}) if i >= 30 else p for i, p in enumerate(pts)]
    m = machine_metrics(to_frame(shifted))
    assert m["gap_count"] == 1 and m["time_h"]["no_data"] == pytest.approx(600 / 3600, rel=0.05)
    assert m["distance_across_gaps_km_straight_line"] > 0
    assert m["distance_km"] == pytest.approx((300 - 5 - (300 / 60)) / 1000, rel=0.06)


def test_relatorio_por_grupo_une_area_entre_maquinas(sim):
    df, _ = sim
    r = report(df, "field")
    assert {x["field_id"] for x in r["rows"]} == {"TALHAO-A", "TALHAO-B", "TALHAO-C", "TALHAO-D"}
    op = report(df, "operator")
    assert len(op["rows"]) == 2 and set(op["rows"][0]["machines"]) == {"SIM-01", "SIM-02"}
    # duas máquinas sobre a MESMA faixa: área única não dobra
    a = make_pts([(12, 0), (12, 500)], machine="A")
    b = make_pts([(12, 0), (12, 500)], machine="B")
    d = to_frame(a + b)
    d["field_id"] = "F"
    rr = report(d, "field")["rows"][0]["coverage"]
    assert rr["gross_ha"] == pytest.approx(2 * rr["unique_ha"], rel=1e-6)
    with pytest.raises(ValueError):
        report(d, "cultura")


def test_amostras_sem_chave_vao_para_sem_valor(sim):
    df, _ = sim
    d = df.copy(); d.loc[d.machine_id == "SIM-01", "operator_id"] = None
    rows = report(d, "operator")["rows"]
    assert any(r["sem_valor"] for r in rows)


def test_anomalias_recall_e_alertas_so_informativos(sim):
    df, truth = sim
    res = A.detect(df)
    ev = A.evaluate(res.alerts, truth.events)
    assert ev["recall"] == 1.0
    assert all(a.to_dict()["informational"] for a in res.alerts) and all(a.severity in ("info", "warning") for a in res.alerts)
    assert not any("comando" in a.message.lower().replace("códigos", "") and "enviar" in a.message.lower() for a in res.alerts)
    flow = [a for a in res.alerts if a.type == "flow_deviation"]
    assert flow and all(a.machine_id == "SIM-04" for a in flow) and all(-40 < a.evidence["median_deviation_pct"] < -30 for a in flow)
    pr = [a for a in res.alerts if a.type == "pressure_outlier"]
    assert pr and all("global" in a.message for a in pr)    # nunca diagnostica bico


def test_maquina_normal_nao_gera_alerta_de_regra(sim):
    df, _ = sim
    res = A.detect(df[df.machine_id == "SIM-01"], A.AnomalyConfig(use_iforest=False))
    assert res.alerts == []


def test_regras_puladas_quando_nao_ha_sensor(sim):
    df, _ = sim
    d = df[df.machine_id == "SIM-04"].copy()
    d["flow_lpm"] = np.nan; d["pressure_bar"] = np.nan
    res = A.detect(d)
    assert not any(a.type in ("flow_deviation", "pressure_outlier", "ml_multivariate_outlier") for a in res.alerts)
    skipped = {s["rule"] for s in res.skipped_rules}
    assert {"flow_deviation", "pressure_*", "iforest"} <= skipped


def test_faixa_de_pressao_configurada():
    pts = make_pts([(0, 0), (0, 400)])
    d = to_frame(pts)
    assert not [a for a in A.detect(d, A.AnomalyConfig(use_iforest=False, pressure_band_bar=(2, 4))).alerts if a.type == "pressure_out_of_band"]
    al = [a for a in A.detect(d, A.AnomalyConfig(use_iforest=False, pressure_band_bar=(4, 6))).alerts if a.type == "pressure_out_of_band"]
    assert len(al) == 1 and "global" in al[0].message


def test_iforest_taxa_de_falso_alarme_cresce_com_contaminacao(sim):
    df, _ = sim
    clean = df[df.machine_id == "SIM-01"]
    n = [len([a for a in A.detect(clean, A.AnomalyConfig(iforest_contamination=c)).alerts if a.type == "ml_multivariate_outlier"]) for c in (0.02, 0.15)]
    assert n[0] <= n[1] and n[1] > 0   # contamination é um orçamento de alarmes: numa máquina limpa, tudo que ele marca é falso positivo


def test_roi_formulas_conferidas_a_mao():
    i = roi.RoiInputs(n_machines=1, capex_per_machine=1000, useful_life_years=10, residual_pct=0, interest_rate_pct=0,
                      maintenance_pct_of_capex_per_year=0, energy_cost_per_h=0, supervision_cost_per_h=0, area_ha_per_year=100,
                      applications_per_year=1, product_cost_per_ha=10, auto_productivity_ha_per_h=10, auto_overlap_pct=0,
                      baseline_hourly_cost=100, baseline_productivity_ha_per_h=10, baseline_overlap_pct=10, discount_rate_pct=0)
    r = roi.compute(i)
    assert r["autonomous"]["machine_cost_per_ha"] == pytest.approx(1.0)          # 100 depreciação/ano / 100 ha
    assert r["baseline"]["total_cost_per_ha"] == pytest.approx(10 + 11)           # 100/10 + 10*1.1
    assert r["autonomous"]["total_cost_per_ha"] == pytest.approx(1 + 10)
    assert r["net_benefit_per_year"] == pytest.approx(1000.0) and r["payback_years"] == pytest.approx(1.0)
    assert r["npv_over_useful_life"] == pytest.approx(-1000 + 10 * 1000)


def test_roi_exemplo_e_marcado_e_sensibilidade_ordenada():
    r = roi.compute(roi.EXAMPLE_INPUTS)
    assert r["is_example"] is True and r["provenance"]["capex_per_machine"]["source"] == "assumed"
    s = roi.sensitivity(roi.EXAMPLE_INPUTS)
    sw = [t["swing_net_benefit"] for t in s["tornado"]]
    assert sw == sorted(sw, reverse=True) and len(sw) >= 10
    be = roi.break_even(roi.EXAMPLE_INPUTS, "area_ha_per_year", 100, 50000)
    j = roi.RoiInputs(**{**roi.EXAMPLE_INPUTS.__dict__, "area_ha_per_year": be})
    assert roi.compute(j)["net_benefit_per_year"] == pytest.approx(0, abs=1)


def test_roi_sem_payback_quando_nao_ha_beneficio():
    i = roi.RoiInputs(**{**roi.EXAMPLE_INPUTS.__dict__, "capex_per_machine": 5_000_000})
    r = roi.compute(i)
    assert r["payback_years"] is None and r["payback_within_useful_life"] is False
    with pytest.raises(ValueError):
        roi.compute(roi.RoiInputs(area_ha_per_year=0))


def test_custo_por_ha_da_telemetria_nao_inventa(sim):
    df, _ = sim
    m = machine_metrics(df[df.machine_id == "SIM-01"])
    c = roi.operational_cost_per_ha(m, hourly_cost=300, product_cost_per_l=0.9)
    assert c["cost_per_ha"] == pytest.approx(c["machine_cost_per_ha"] + c["product_cost_per_ha"])
    assert roi.operational_cost_per_ha({"coverage": {}, "time_h": {}}, 300)["cost_per_ha"] is None


def test_distancia_sobre_perda_de_posicao_e_reportada_a_parte():
    pts = make_pts([(0, 0), (0, 600)], drop=[(40, 70)])           # 30 amostras sem GNSS (~150 m de percurso real)
    m = machine_metrics(to_frame(pts))
    medida, nao_medida = m["distance_km"], m["distance_across_gaps_km_straight_line"]
    assert nao_medida == pytest.approx(0.155, abs=0.01)           # reta entre a última posição antes e a primeira depois
    assert medida + nao_medida == pytest.approx(0.595, rel=0.02)  # as duas partes fecham o percurso (600 m menos o último passo)
