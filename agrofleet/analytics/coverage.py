"""Geometria de cobertura: área bruta × área única × sobreposição, a partir de largura efetiva e trajetória.

Definições (por máquina ou conjunto de máquinas):
- faixa (swath) de um segmento P_i→P_{i+1}: retângulo de comprimento |P_i P_{i+1}| e largura ``working_width_m`` (do ponto i),
  em UTM. Só entram segmentos com os DOIS extremos em estado ``working`` (evita faixa espúria na cabeceira, quando o
  estado só muda na amostra seguinte), com posição nos dois extremos, dt <= limite e largura informada.
- área bruta   = Σ comprimento_i × largura_i   (o que a máquina "varreu", contando repetições)
- área única   = área( união das faixas )       (terra efetivamente coberta, sem contar duas vezes)
- excesso      = bruta − única                  (área "reaplicada"; inclui pequenos artefatos geométricos em curvas)
- região ≥2×   = área( união das interseções entre faixas não adjacentes )  (onde o produto caiu 2 ou mais vezes)
Nenhuma faixa é criada sobre lacunas de dados: entre duas amostras distantes no tempo NÃO se interpola cobertura.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from ..geo import Projector, ha, transform_geom
from .frame import median_dt_s


@dataclass
class CoverageConfig:
    max_gap_s: float = 10.0          # acima disso (ou 3× o dt mediano) o segmento é lacuna
    min_speed_ms: float = 0.2        # abaixo disso o ponto não gera faixa (parado)
    max_accuracy_m: float | None = 5.0  # pontos com precisão pior são excluídos (None = não filtrar)
    working_states: tuple[str, ...] = ("working",)
    require_flow_on: bool = False    # se True, exige flow_lpm > 0 (só quando o sensor existe)


@dataclass
class Segments:
    x0: np.ndarray; y0: np.ndarray; x1: np.ndarray; y1: np.ndarray; w: np.ndarray
    machine: np.ndarray; t0: np.ndarray
    excluded: dict = field(default_factory=dict)
    proj: Projector | None = None

    def __len__(self):
        return len(self.x0)


def build_segments(df: pd.DataFrame, cfg: CoverageConfig, proj: Projector | None = None) -> Segments:
    """Segmentos de faixa a partir de um DataFrame (pode ter várias máquinas)."""
    ex = {"no_position": 0, "not_working": 0, "gap": 0, "low_accuracy": 0, "no_width": 0, "low_speed": 0, "no_flow": 0}
    pos = df.dropna(subset=["lat", "lon"])
    if proj is None:
        proj = Projector.for_points(pos["lon"], pos["lat"]) if len(pos) else Projector(32722)
    parts = []
    for mid, g in df.groupby("machine_id", sort=False):
        g = g.sort_values("ts").reset_index(drop=True)
        thr = max(cfg.max_gap_s, 3 * (median_dt_s(g["ts"]) if len(g) > 2 else 0) or 0)
        has_pos = g["lat"].notna() & g["lon"].notna()
        x = np.full(len(g), np.nan); y = np.full(len(g), np.nan)
        if has_pos.any():
            xx, yy = proj.fwd(g.loc[has_pos, "lon"], g.loc[has_pos, "lat"])
            x[has_pos.values] = xx; y[has_pos.values] = yy
        dt = g["ts"].diff().shift(-1).dt.total_seconds().to_numpy()  # dt do ponto i ao i+1
        working = g["state"].isin(cfg.working_states).to_numpy()
        acc_bad = (g["accuracy_m"] > cfg.max_accuracy_m).to_numpy() if cfg.max_accuracy_m is not None else np.zeros(len(g), bool)
        w = g["working_width_m"].to_numpy()
        spd = g["speed_ms"].to_numpy()
        flow = g["flow_lpm"].to_numpy()
        n = len(g) - 1
        if n <= 0:
            continue
        i = np.arange(n)
        a, b = i, i + 1
        pos_ok = has_pos.to_numpy()
        reasons = np.zeros(n, dtype=object)
        ok = np.ones(n, bool)
        def drop(mask, why):
            nonlocal ok
            m = ok & mask
            ex[why] += int(m.sum()); ok &= ~mask
        drop(~(pos_ok[a] & pos_ok[b]), "no_position")
        drop(~(working[a] & working[b]), "not_working")   # os DOIS extremos devem estar em aplicação
        drop(~(dt[a] <= thr) | ~(dt[a] > 0), "gap")
        drop(acc_bad[a] | acc_bad[b], "low_accuracy")
        drop(np.isnan(w[a]), "no_width")
        drop(~(np.nan_to_num(spd[a], nan=np.inf) >= cfg.min_speed_ms), "low_speed")
        if cfg.require_flow_on:
            drop(~(np.nan_to_num(flow[a], nan=-1) > 0), "no_flow")
        k = np.where(ok)[0]
        parts.append((x[k], y[k], x[k + 1], y[k + 1], w[k], np.array([mid] * len(k), dtype=object),
                      (g["ts"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()[k]))
    if not parts:
        e = np.array([])
        return Segments(e, e, e, e, e, e, e, ex, proj)
    cat = [np.concatenate([p[j] for p in parts]) for j in range(7)]
    return Segments(*cat, excluded=ex, proj=proj)


def swath_polygons(s: Segments) -> list[Polygon]:
    if not len(s):
        return []
    dx, dy = s.x1 - s.x0, s.y1 - s.y0
    L = np.hypot(dx, dy)
    keep = L > 1e-6
    nx, ny = -dy[keep] / L[keep], dx[keep] / L[keep]
    h = s.w[keep] / 2
    c = np.stack([
        np.stack([s.x0[keep] + nx * h, s.y0[keep] + ny * h], -1),
        np.stack([s.x1[keep] + nx * h, s.y1[keep] + ny * h], -1),
        np.stack([s.x1[keep] - nx * h, s.y1[keep] - ny * h], -1),
        np.stack([s.x0[keep] - nx * h, s.y0[keep] - ny * h], -1)], axis=1)
    return list(shapely.polygons(c))


@dataclass
class CoverageResult:
    gross_m2: float
    unique_m2: float
    excess_m2: float
    overlap_region_m2: float
    n_segments: int
    excluded: dict
    union_geojson: dict | None
    overlap_geojson: dict | None
    field_area_m2: float | None = None
    covered_in_field_m2: float | None = None
    uncovered_in_field_m2: float | None = None
    outside_field_m2: float | None = None

    def summary(self) -> dict:
        d = {"gross_ha": ha(self.gross_m2), "unique_ha": ha(self.unique_m2), "excess_ha": ha(self.excess_m2),
             "overlap_region_ha": ha(self.overlap_region_m2),
             "overlap_pct_of_gross": (100 * self.excess_m2 / self.gross_m2) if self.gross_m2 > 0 else None,
             "n_segments": self.n_segments, "excluded_segments": self.excluded}
        if self.field_area_m2 is not None:
            d.update(field_ha=ha(self.field_area_m2), covered_in_field_ha=ha(self.covered_in_field_m2),
                     uncovered_in_field_ha=ha(self.uncovered_in_field_m2), outside_field_ha=ha(self.outside_field_m2),
                     coverage_pct_of_field=100 * self.covered_in_field_m2 / self.field_area_m2)
        return d


def overlap_region(polys: list[Polygon], mach: np.ndarray, t0: np.ndarray, skip_s: float):
    """União das interseções entre faixas NÃO adjacentes no tempo (mesma máquina e |Δt| <= skip_s são vizinhas de
    trajetória: a interseção delas é só a junção do traçado, não repasse)."""
    if len(polys) < 2:
        return None
    tree = STRtree(polys)
    left, right = tree.query(polys, predicate="intersects")
    pieces = []
    for i, j in zip(left, right):
        if j <= i:
            continue
        if mach[i] == mach[j] and abs(t0[j] - t0[i]) <= skip_s:
            continue
        inter = polys[i].intersection(polys[j])
        if not inter.is_empty and inter.area > 1e-3:
            pieces.append(inter)
    return unary_union(pieces) if pieces else None


def coverage_from_segments(s: Segments, field_lonlat: dict | None = None, with_geojson: bool = True) -> CoverageResult:
    polys = swath_polygons(s)
    if not polys:
        return CoverageResult(0.0, 0.0, 0.0, 0.0, 0, s.excluded, None, None)
    gross = float(sum(p.area for p in polys))
    # gross exato = Σ L×w (polygon.area dá o mesmo para retângulos; mantemos a fórmula explícita)
    L = np.hypot(s.x1 - s.x0, s.y1 - s.y0)
    gross = float(np.sum(L * s.w))
    uni = unary_union(polys)
    excess = max(gross - uni.area, 0.0)
    keep = L > 1e-6
    t0 = s.t0[keep]
    dts = np.diff(np.sort(t0)); dts = dts[dts > 0]
    skip_s = 4 * float(np.median(dts)) if len(dts) else 4.0
    ov = overlap_region(polys, s.machine[keep], t0, skip_s)
    res = CoverageResult(gross, float(uni.area), excess, float(ov.area) if ov is not None else 0.0, len(polys), s.excluded, None, None)
    proj = s.proj
    def to_ll(g):
        return mapping(transform_geom(g, proj.inv))
    if with_geojson:
        res.union_geojson = to_ll(uni)
        res.overlap_geojson = to_ll(ov) if ov is not None else None
    if field_lonlat is not None:
        fld = transform_geom(shape(field_lonlat), proj.fwd)
        res.field_area_m2 = float(fld.area)
        res.covered_in_field_m2 = float(uni.intersection(fld).area)
        res.uncovered_in_field_m2 = float(fld.difference(uni).area)
        res.outside_field_m2 = float(uni.difference(fld).area)
    return res


def coverage(df: pd.DataFrame, cfg: CoverageConfig | None = None, field_lonlat: dict | None = None,
             proj: Projector | None = None, with_geojson: bool = True) -> CoverageResult:
    cfg = cfg or CoverageConfig()
    return coverage_from_segments(build_segments(df, cfg, proj), field_lonlat, with_geojson)
