import numpy as np
import pytest

from agrofleet.analytics.coverage import CoverageConfig, coverage
from agrofleet.analytics.frame import to_frame
from agrofleet.geo import Projector, expected_flow_lpm, haversine_m
from agrofleet.schema import State
from conftest import make_pts, passes


def _cov(pts, **kw):
    return coverage(to_frame(pts), CoverageConfig(**kw))


def test_uma_passada_area_exata():
    # 500 m a 5 m/s, largura 24 m: 500*24 = 1,2 ha (1 Hz => passos de 5 m: o último passo some, tolerância de 1 amostra)
    r = _cov(make_pts([(0, 0), (0, 500)], speed=5.0))
    assert r.gross_m2 == pytest.approx(495 * 24, rel=1e-3)
    assert r.unique_m2 == pytest.approx(r.gross_m2, rel=1e-6) and r.excess_m2 < 1e-6


def test_sobreposicao_conhecida_analiticamente():
    # 2 passadas paralelas a 22 m com barra de 24 m => faixa de 2 m sobreposta em ~495 m
    from conftest import T0
    from datetime import timedelta
    p1 = make_pts([(12, 0), (12, 500)])
    p2 = make_pts([(34, 500), (34, 0)], t0=T0 + timedelta(seconds=1000))
    r = coverage(to_frame(p1 + p2), CoverageConfig())
    L = 495.0
    assert r.gross_m2 == pytest.approx(2 * L * 24, rel=1e-3)
    assert r.excess_m2 == pytest.approx(L * 2.0, rel=0.02)
    assert r.unique_m2 == pytest.approx(r.gross_m2 - r.excess_m2, rel=1e-6)
    assert r.overlap_region_m2 == pytest.approx(L * 2.0, rel=0.03)


def test_nao_interpola_cobertura_sobre_perda_de_gnss():
    full = _cov(make_pts([(0, 0), (0, 500)]))
    gap = _cov(make_pts([(0, 0), (0, 500)], drop=[(40, 60)]))
    assert gap.unique_m2 < full.unique_m2
    # 20 amostras sem posição => 21 segmentos perdidos (cada um 5 m * 24 m)
    assert full.unique_m2 - gap.unique_m2 == pytest.approx(21 * 5 * 24, rel=0.01)
    assert gap.excluded["no_position"] == 21


def test_estado_transport_nao_gera_faixa():
    r = _cov(make_pts([(0, 0), (0, 500)], state=State.transport))
    assert r.gross_m2 == 0 and r.n_segments == 0 and r.excluded["not_working"] > 0


def test_largura_ausente_nao_e_inventada():
    pts = make_pts([(0, 0), (0, 100)])
    pts = [p.model_copy(update={"working_width_m": None}) for p in pts]
    r = _cov(pts)
    assert r.gross_m2 == 0 and r.excluded["no_width"] > 0


def test_filtro_de_precisao():
    r = _cov(make_pts([(0, 0), (0, 100)], accuracy=3.0), max_accuracy_m=1.0)
    assert r.n_segments == 0 and r.excluded["low_accuracy"] > 0
    r2 = _cov(make_pts([(0, 0), (0, 100)], accuracy=3.0), max_accuracy_m=None)
    assert r2.n_segments > 0


def test_lacuna_temporal_quebra_segmento():
    pts = make_pts([(0, 0), (0, 200)])
    from datetime import timedelta
    pts2 = [p.model_copy(update={"ts": p.ts + timedelta(seconds=300)}) if i >= 20 else p for i, p in enumerate(pts)]
    r = _cov(pts2)
    assert r.excluded["gap"] == 1   # um segmento de 300 s não vira faixa


def test_talhao_cobertura_dentro_fora():
    pr = Projector.for_points([-47.6], [-16.7])
    x0, y0 = (float(a[0]) for a in pr.fwd([-47.6], [-16.7]))
    # talhão 100 x 500 m; passada centrada em x=12 com 24 m => cobre x in [0,24]; talhão x in [0,100]
    ring = [(0, 0), (100, 0), (100, 500), (0, 500), (0, 0)]
    lon, lat = pr.inv([x0 + a for a, _ in ring], [y0 + b for _, b in ring])
    fld = {"type": "Polygon", "coordinates": [[[float(a), float(b)] for a, b in zip(lon, lat)]]}
    r = coverage(to_frame(make_pts([(12, 0), (12, 500)])), field_lonlat=fld)
    s = r.summary()
    assert s["field_ha"] == pytest.approx(5.0, rel=1e-3)
    assert s["covered_in_field_ha"] == pytest.approx(495 * 24 / 1e4, rel=0.01)
    assert s["covered_in_field_ha"] + s["uncovered_in_field_ha"] == pytest.approx(s["field_ha"], rel=1e-6)
    assert s["outside_field_ha"] < 0.01


def test_projecao_vs_haversine():
    pr = Projector.for_points([-47.6], [-16.7])
    lo, la = pr.inv([pr.fwd([-47.6], [-16.7])[0][0] + 1000], [pr.fwd([-47.6], [-16.7])[1][0] + 1000])
    d_proj = float(np.hypot(1000, 1000))
    d_hav = float(haversine_m(-16.7, -47.6, la[0], lo[0]))
    assert d_hav == pytest.approx(d_proj, rel=2e-3)   # UTM tem fator de escala ~0,9996 + esfera: diferença < 0,2%


def test_vazao_esperada():
    # 100 L/ha a 18 km/h com 24 m: 100*18*24/600 = 72 L/min
    assert expected_flow_lpm(100, 5.0, 24.0) == pytest.approx(72.0)
