"""Missão da operação (Etapa 4): progresso, produtividade e previsão de conclusão. Cálculo puro.

Definições:
- área planejada   = ``OperationPlan.planned_area_ha``; se ausente, área do talhão (geometria). A fonte é informada.
- área trabalhada  = área( união das faixas ∩ talhão ). Repasse NÃO conta duas vezes e faixa fora do talhão não conta.
                     Sem geometria do talhão, usa a área única total e sinaliza (``no_field_geometry``).
- progresso        = trabalhada / planejada.
- série            = intervalos de ``bucket_min`` desde o início da operação. Em cada um, a área NOVA é a parte da
                     união do intervalo que não estava coberta antes (soma das áreas novas = área trabalhada).
                     ha/h efetivo = área nova / tempo observado do intervalo (relógio, inclui manobras e paradas;
                     exclui só o tempo sem dado, que é desconhecido). ha/h em trabalho = área nova / tempo em ``working``.
- ritmo atual      = Σ área nova / Σ tempo observado nos intervalos da janela ``rate_window_min`` mais recente.
- faixa do ritmo   = quartis 25% e 75% do ha/h efetivo dos intervalos completos (faixa empírica do que já foi
                     observado nesta operação, não intervalo de confiança estatístico).
- restante         = max(planejada − trabalhada, 0); tempo restante = restante / ritmo; ETA = última amostra + tempo.
                     Otimista com o ritmo do 3º quartil, pessimista com o do 1º (indeterminado se o 1º for zero).

A ETA não é publicada quando faltam entradas ou há pouca evidência (``eta_reasons``); avisos que não impedem o cálculo
ficam em ``reliability``. É extrapolação do ritmo observado, não previsão validada.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from shapely import box
from shapely.ops import unary_union

from ..geo import ha
from ..registry import OperationPlan
from .autonomy import Remaining, reason, time_weighted_mean
from .coverage import CoverageConfig, CoverageResult, build_segments, coverage_from_segments, run_swaths, subset
from .metrics import MetricsConfig, _intervals

ENGAGED = ("working", "transport", "idle", "refilling", "fault")


@dataclass
class MissionConfig:
    bucket_min: float = 10.0
    rate_window_min: float = 60.0
    min_elapsed_min: float = 30.0       # antes disso não há ETA
    min_progress_pct: float = 2.0       # idem
    min_buckets_for_range: int = 3
    high_cv: float = 0.5                # coeficiente de variação do ha/h por intervalo acima disso → aviso
    gap_warn_pct: float = 10.0          # tempo sem dado na janela acima disso → aviso
    stale_after_s: float = 300.0
    coverage: CoverageConfig = field(default_factory=CoverageConfig)


@dataclass
class MissionResult:
    operation_id: str
    machine_id: str
    status: str
    start: str
    last_ts: str | None
    planned_ha: float | None
    planned_source: str | None
    worked_ha: float
    worked_source: str
    remaining_ha: float | None
    progress_pct: float | None
    gross_ha: float
    unique_ha: float
    overlap_ha: float
    outside_field_ha: float | None
    elapsed_h: float | None
    working_h: float
    engaged_h: float
    no_data_h: float
    rate_ha_h: float | None                 # ritmo atual (efetivo, janela recente)
    rate_ha_h_working: float | None         # média na operação, por hora em trabalho
    rate_range_ha_h: list | None            # [p25, p75] dos intervalos completos
    remaining_h: float | None
    remaining_h_range: list | None          # [otimista, pessimista]
    eta: str | None
    eta_range: list | None                  # [mais cedo, mais tarde]
    eta_reasons: list = field(default_factory=list)
    reliability: list = field(default_factory=list)
    series: list = field(default_factory=list)
    basis: str = "Extrapolação do ritmo observado nesta operação; estimativa, não previsão validada."
    coverage: CoverageResult | None = field(default=None, repr=False)

    def remaining(self) -> Remaining:
        r = self.remaining_h_range or [None, None]
        return Remaining(self.remaining_h, pessimistic_h=r[1], optimistic_h=r[0])

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("coverage", None)
        return d


def operation_frame(df: pd.DataFrame, op: OperationPlan) -> pd.DataFrame:
    """Amostras da máquina da operação, na janela da operação e da MESMA origem."""
    g = df[(df["machine_id"] == op.machine_id) & (df["origin"] == op.origin) & (df["ts"] >= pd.Timestamp(op.start))]
    if op.end is not None:
        g = g[g["ts"] <= pd.Timestamp(op.end)]
    return g.sort_values("ts").reset_index(drop=True)


def _q(v, q):
    return float(np.percentile(v, q)) if len(v) else None


@dataclass
class _BucketCtx:
    B: float; span: float; nb: int; seg: object; seg_t: np.ndarray; fld: object
    tsec: np.ndarray; dt: np.ndarray; valid: np.ndarray; states: np.ndarray
    g: pd.DataFrame; t_start: pd.Timestamp; first_off: float; max_gap_s: float


def bucket_entry(c: _BucketCtx, i: int, cum, prev_ha: float):
    """Intervalo ``i`` da série. ``cum`` = união (∩ talhão) dos intervalos anteriores; ``prev_ha`` = soma das áreas
    novas anteriores. Retorna (entrada ou None, novo ``cum``). Usado pelo cálculo completo e pelo incremental."""
    a, b = i * c.B, min((i + 1) * c.B, c.span)
    if b <= a:
        return None, cum
    sm = (c.seg_t >= a) & (c.seg_t < b) if i < c.nb - 1 else (c.seg_t >= a)
    new = 0.0
    if sm.any():
        piece = unary_union(run_swaths(subset(c.seg, sm)))
        if c.fld is not None:
            piece = piece.intersection(c.fld)
        if cum is not None and not piece.is_empty:
            local = cum.intersection(box(*piece.bounds))
            fresh = piece.difference(local)
        else:
            fresh = piece
        new = ha(fresh.area)
        cum = piece if cum is None else unary_union([cum, piece])
    dt, valid = c.dt, c.valid
    im = (c.tsec >= a) & (c.tsec < b)
    obs = float(dt[im & valid].sum())
    nodata = float(dt[im & ~valid & (dt > 0)].sum()) + (max(min(c.first_off, b) - a, 0.0) if i == 0 else 0.0)
    wk = float(dt[im & valid & (c.states == "working")].sum())
    t0, t1 = c.t_start + pd.Timedelta(seconds=a), c.t_start + pd.Timedelta(seconds=b)
    gb = c.g[(c.g["ts"] >= t0) & (c.g["ts"] < t1)]
    fuel, _ = time_weighted_mean(gb, "fuel_rate_lph", "q_fuel_rate", c.max_gap_s)
    power, _ = time_weighted_mean(gb, "battery_power_kw", "q_battery", c.max_gap_s)
    return {
        "t_start": t0.isoformat(), "t_end": t1.isoformat(),
        "complete": (b - a) >= c.B - 1e-6, "new_ha": new, "observed_h": obs / 3600, "no_data_h": nodata / 3600,
        "working_h": wk / 3600, "ha_per_h": new / (obs / 3600) if obs > 0 else None,
        "ha_per_working_h": new / (wk / 3600) if wk > 0 else None,
        "fuel_rate_lph": fuel, "battery_power_kw": power,
        "cumulative_ha": prev_ha + new}, cum


def mission(df: pd.DataFrame, op: OperationPlan, field_lonlat: dict | None = None, cfg: MissionConfig | None = None,
            data_age_s: float | None = None, cache=None) -> MissionResult:
    """``cache``: ``incremental.MissionCache`` da operação (opcional). Com ele, só a cauda recente é recalculada; o
    resultado é o mesmo do cálculo completo (testado em ``tests/test_incremental.py``)."""
    cfg = cfg or MissionConfig()
    g = operation_frame(df, op)
    seg = build_segments(g, cfg.coverage) if len(g) else None
    if cache is not None and seg is not None and len(g) >= 2:
        cov = cache.coverage(g, seg, op, field_lonlat, cfg)
    else:
        cache = None
        cov = coverage_from_segments(seg, field_lonlat, with_geojson=False) if seg is not None else None
    fld = cov.field_geom if cov is not None and cov.field_geom is not None else None

    # ---- áreas -----------------------------------------------------------------------------------------------
    if op.planned_area_ha is not None:
        planned, psrc = op.planned_area_ha, "operation"
    elif cov is not None and cov.field_area_m2 is not None:
        planned, psrc = ha(cov.field_area_m2), "field_geometry"
    elif field_lonlat is not None:                      # talhão sem telemetria ainda
        from shapely.geometry import shape
        from ..geo import Projector, transform_geom
        shp = shape(field_lonlat)
        c = shp.centroid
        planned, psrc = ha(transform_geom(shp, Projector.for_points([c.x], [c.y]).fwd).area), "field_geometry"
    else:
        planned, psrc = None, None
    R, W = [], []
    if field_lonlat is None:
        W.append(reason("no_field_geometry", "Talhão sem geometria: área trabalhada = área única total, inclusive fora do talhão."))
    if cov is not None and cov.covered_in_field_m2 is not None:
        worked, wsrc = ha(cov.covered_in_field_m2), "union_in_field"
    else:
        worked, wsrc = (ha(cov.unique_m2) if cov is not None else 0.0), "union_total"

    res = MissionResult(
        op.operation_id, op.machine_id, op.status, op.start.isoformat(),
        g["ts"].iloc[-1].isoformat() if len(g) else None, planned, psrc, worked, wsrc,
        max(planned - worked, 0.0) if planned is not None else None,
        100 * worked / planned if planned else None,
        ha(cov.gross_m2) if cov else 0.0, ha(cov.unique_m2) if cov else 0.0, ha(cov.overlap_region_m2) if cov else 0.0,
        ha(cov.outside_field_m2) if cov is not None and cov.outside_field_m2 is not None else None,
        None, 0.0, 0.0, 0.0, None, None, None, None, None, None, None, R, W, coverage=cov)
    if planned is None:
        R.append(reason("planned_area_unknown", "Área planejada desconhecida (operação sem área e talhão sem geometria)."))
    if res.progress_pct is not None and res.progress_pct > 100.5:
        W.append(reason("progress_exceeds_plan", "Área trabalhada maior que a planejada; conferir o cadastro."))
    if len(g) < 2:
        R.append(reason("no_data", "Sem telemetria suficiente no período da operação."))
        return res

    # ---- tempos ----------------------------------------------------------------------------------------------
    t_start = pd.Timestamp(op.start)
    last = g["ts"].iloc[-1]
    res.elapsed_h = (last - t_start).total_seconds() / 3600
    dt, valid, thr = _intervals(g, MetricsConfig(max_gap_s=cfg.coverage.max_gap_s))
    n = len(g) - 1
    dt, valid = np.nan_to_num(dt[:n]), valid[:n]
    states = g["state"].to_numpy()[:n]
    tsec = ((g["ts"] - t_start).dt.total_seconds()).to_numpy()[:n]
    res.working_h = float(dt[valid & (states == "working")].sum() / 3600)
    res.engaged_h = float(dt[valid & np.isin(states, ENGAGED)].sum() / 3600)
    # tempo sem dado = lacunas internas + atraso do primeiro dado em relação ao início
    first_off = max((g["ts"].iloc[0] - t_start).total_seconds(), 0.0)
    res.no_data_h = float((dt[~valid & (dt > 0)].sum() + first_off) / 3600)
    if res.working_h > 0:
        res.rate_ha_h_working = worked / res.working_h

    # ---- série por intervalo ---------------------------------------------------------------------------------
    B = cfg.bucket_min * 60
    span = (last - t_start).total_seconds()
    ctx = _BucketCtx(B=B, span=span, nb=max(int(np.ceil(span / B)), 1), seg=seg, seg_t=seg.t0 - t_start.timestamp(),
                     fld=fld, tsec=tsec, dt=dt, valid=valid, states=states, g=g, t_start=t_start, first_off=first_off,
                     max_gap_s=cfg.coverage.max_gap_s)
    if cache is not None:
        res.series, cum = cache.series_prefix(ctx, bucket_entry)      # intervalos congelados (idênticos ao cálculo completo)
    else:
        cum = None
    for i in range(len(res.series), ctx.nb):
        e, cum = bucket_entry(ctx, i, cum, sum(s["new_ha"] for s in res.series))
        if e is not None:
            res.series.append(e)

    # ---- ritmo, faixa e ETA ----------------------------------------------------------------------------------
    k = max(int(np.ceil(cfg.rate_window_min / cfg.bucket_min)), 1)
    recent = res.series[-k:]
    obs_w = sum(s["observed_h"] for s in recent)
    nod_w = sum(s["no_data_h"] for s in recent)
    if obs_w > 0:
        res.rate_ha_h = sum(s["new_ha"] for s in recent) / obs_w
    if obs_w + nod_w > 0 and 100 * nod_w / (obs_w + nod_w) > cfg.gap_warn_pct:
        W.append(reason("data_gaps", f"{100 * nod_w / (obs_w + nod_w):.0f}% da janela recente sem dados."))
    full = np.array([s["ha_per_h"] for s in res.series
                     if s["complete"] and s["ha_per_h"] is not None and s["observed_h"] >= 0.5 * cfg.bucket_min / 60])
    if len(full) >= cfg.min_buckets_for_range:
        res.rate_range_ha_h = [_q(full, 25), _q(full, 75)]
        m = full.mean()
        if m > 0 and full.std() / m > cfg.high_cv:
            W.append(reason("high_variability", "Ritmo muito variável entre intervalos; faixa larga."))
    else:
        W.append(reason("few_intervals", f"Menos de {cfg.min_buckets_for_range} intervalos completos: sem faixa de ritmo."))
    if data_age_s is not None and data_age_s > cfg.stale_after_s:
        R.append(reason("stale_data", f"Último dado há {data_age_s / 60:.0f} min; ritmo atual desconhecido."))

    if op.status == "completed":
        res.eta = op.end.isoformat() if op.end else None
        res.remaining_h = 0.0
        return res
    if res.remaining_ha == 0:
        res.remaining_h, res.remaining_h_range = 0.0, [0.0, 0.0]
        res.eta = last.isoformat()
        res.eta_range = [res.eta, res.eta]
        W.append(reason("covered_not_closed", "Área planejada coberta, mas a operação não foi encerrada no cadastro."))
        return res
    if res.elapsed_h * 60 < cfg.min_elapsed_min:
        R.append(reason("elapsed_too_short", f"Menos de {cfg.min_elapsed_min:.0f} min de operação."))
    if res.progress_pct is not None and res.progress_pct < cfg.min_progress_pct:
        R.append(reason("progress_too_small", f"Progresso abaixo de {cfg.min_progress_pct:.0f}%."))
    if res.rate_ha_h is None or res.rate_ha_h <= 0:
        R.append(reason("no_recent_progress", "Nenhuma área nova na janela recente (máquina parada ou fora do talhão)."))
    if R or res.remaining_ha is None:
        return res

    res.remaining_h = res.remaining_ha / res.rate_ha_h
    at = lambda h: (last + pd.Timedelta(hours=h)).round("s").isoformat()     # estimativa: precisão de segundos basta
    res.eta = at(res.remaining_h)
    if res.rate_range_ha_h is not None:
        fast = max(res.rate_range_ha_h[1], res.rate_ha_h)
        slow = min(res.rate_range_ha_h[0], res.rate_ha_h)
        opt = res.remaining_ha / fast
        pes = res.remaining_ha / slow if slow > 0 else None
        res.remaining_h_range = [opt, pes]
        res.eta_range = [at(opt), at(pes) if pes is not None else None]
    return res
