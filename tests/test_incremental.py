"""Etapa 6: o cálculo incremental da missão dá o mesmo resultado que o cálculo completo.

Cada teste avança um único ``MissionCache`` por vários instantes, como o laço ao vivo faria, e compara campo a campo
com ``mission`` sem cache sobre o mesmo dado. Tolerância: 1e-6 relativo. Unir as mesmas peças em outra ordem já muda
a área no GEOS: na região ≥2× de 5 h do cenário A (333 m², 17.744 peças), o próprio cálculo completo varia 1e-5 m²
(3e-8 relativo) conforme a ordem; o incremental fica nessa mesma ordem. Textos (ETA, motivos) iguais.
"""
import math
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from agrofleet.analytics.frame import to_frame
from agrofleet.analytics.incremental import MissionCache
from agrofleet.analytics.mission import mission
from agrofleet.simulator.fleet import DEFAULT_START
from agrofleet.simulator.operations import run_scenarios, scenario_a, simulate_operation

REL = 1e-6


def same(a, b, path="") -> list[str]:
    """Diferenças entre dois resultados (vazio = iguais)."""
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return [f"{path}: chaves {sorted(set(a) ^ set(b))}"]
        return [d for k in a for d in same(a[k], b[k], f"{path}.{k}")]
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return [f"{path}: tamanho {len(a)} != {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in same(x, y, f"{path}[{i}]")]
    if isinstance(a, float) and isinstance(b, float):
        return [] if math.isclose(a, b, rel_tol=REL, abs_tol=1e-7) else [f"{path}: {a!r} != {b!r}"]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def check(df, r, cache, **kw):
    full = mission(df, r.operation, r.field_lonlat, **kw)
    inc = mission(df, r.operation, r.field_lonlat, cache=cache, **kw)
    diff = same(inc.to_dict(), full.to_dict())
    assert not diff, diff[:5]
    cf, ci = full.coverage, inc.coverage
    for f in ("gross_m2", "unique_m2", "overlap_region_m2", "covered_in_field_m2", "uncovered_in_field_m2", "outside_field_m2"):
        assert math.isclose(getattr(ci, f), getattr(cf, f), rel_tol=REL, abs_tol=1e-3), f
    assert ci.excluded == cf.excluded and ci.n_segments == cf.n_segments
    return full, inc


@pytest.fixture(scope="module")
def run_a():
    r = simulate_operation(scenario_a(), max_seconds=2.5 * 3600)
    return r, to_frame(r.points)


def test_incremental_igual_ao_completo_ao_longo_da_operacao(run_a):
    r, df = run_a
    cache = MissionCache()
    for minutes in range(20, 151, 11):
        t = DEFAULT_START + timedelta(minutes=minutes)
        check(df[df["ts"] <= t], r, cache, data_age_s=0.0)
    st = cache.stats
    assert st["resets"] == 0 and st["advances"] >= 10 and st["frozen_buckets"] >= 12
    # união montada em partes = união completa (a geometria do mapa sai dela)
    full = mission(df, r.operation, r.field_lonlat)
    inc = mission(df, r.operation, r.field_lonlat, cache=cache)
    assert inc.coverage.union().symmetric_difference(full.coverage.union_geom).area < 1e-3


def test_chamada_com_cache_quente_e_mais_rapida(run_a):
    r, df = run_a
    cache = MissionCache()
    mission(df.iloc[:-30], r.operation, r.field_lonlat, cache=cache)
    t = time.perf_counter(); mission(df, r.operation, r.field_lonlat); full = time.perf_counter() - t
    t = time.perf_counter(); mission(df, r.operation, r.field_lonlat, cache=cache); warm = time.perf_counter() - t
    assert warm < 0.5 * full, (warm, full)


def test_enlace_com_falhas_cenario_c_sem_descartar_o_cache():
    """C: perda de 90 s, 4 min guardados e reenviados, latência de 25 s fora de ordem e reentregas. O dado é montado
    pela ORDEM DE CHEGADA; atrasos menores que a margem não descartam o cache."""
    (r,) = run_scenarios(("C",), max_seconds=1.5 * 3600)
    dv = r.truth["_deliveries"]
    cache = MissionCache()
    for minutes in (25, 34, 36, 39, 45, 52, 56, 61, 70, 85):
        got = {}
        for d in dv:
            if d.deliver_s <= minutes * 60 and not d.duplicate:
                got[d.point.ts] = d.point
        check(to_frame(list(got.values())), r, cache, data_age_s=0.0)
    assert cache.stats["resets"] == 0 and cache.stats["advances"] > 0


def test_chegada_tardia_antes_do_corte_recalcula_do_zero(run_a):
    r, df = run_a
    cut = DEFAULT_START + timedelta(minutes=60)
    sem = df[(df["ts"] <= cut) & (df["ts"] != DEFAULT_START + timedelta(minutes=30))]     # amostra das 10:30 faltando
    cache = MissionCache()
    check(sem, r, cache)
    check(df[df["ts"] <= cut], r, cache)                       # chega atrasada: dado antes do corte mudou
    assert cache.stats["resets"] == 1 and "tardia" in cache.stats["last_reset"]


def test_mudanca_de_cadastro_ou_as_of_anterior_recalcula(run_a):
    r, df = run_a
    cache = MissionCache()
    check(df[df["ts"] <= DEFAULT_START + timedelta(minutes=90)], r, cache)
    r2 = SimpleNamespace(operation=r.operation.model_copy(update={"planned_area_ha": 100.0}), field_lonlat=r.field_lonlat)
    check(df[df["ts"] <= DEFAULT_START + timedelta(minutes=95)], r2, cache)
    assert cache.stats["resets"] == 1 and "cadastro" in cache.stats["last_reset"]
    check(df[df["ts"] <= DEFAULT_START + timedelta(minutes=40)], r2, cache)     # reprodução de um instante anterior
    assert cache.stats["resets"] == 2
