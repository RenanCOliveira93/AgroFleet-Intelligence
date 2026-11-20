"""Geometria de cobertura: área bruta × área única × sobreposição, a partir de largura efetiva e trajetória.

Definições (por máquina ou conjunto de máquinas):
- faixa (swath) de um segmento P_i→P_{i+1}: retângulo de comprimento |P_i P_{i+1}| e largura ``working_width_m`` (do ponto i),
  em UTM. Só entram segmentos com os DOIS extremos em estado ``working`` (evita faixa espúria na cabeceira, quando o
  estado só muda na amostra seguinte), com posição nos dois extremos, dt <= limite e largura informada.
- área bruta   = Σ comprimento_i × largura_i   (o que a máquina "varreu", contando repetições)
- área única   = área( união das faixas )       (terra efetivamente coberta, sem contar duas vezes). Para a união, os
  segmentos consecutivos de um mesmo trecho contínuo (mesma máquina, amostras seguidas, mesma largura) viram UMA faixa:
  buffer da polilinha com ponta reta e junção arredondada. Retângulos isolados por segmento deixam cunhas abertas no
  lado externo de cada mudança de rumo, que viram micro-buracos quando a passada vizinha fecha o contorno.
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
    idx: np.ndarray | None = None    # índice da amostra inicial dentro da máquina (define trechos contínuos)

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
                      (g["ts"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()[k], k))
    if not parts:
        e = np.array([])
        return Segments(e, e, e, e, e, e, e, ex, proj, idx=np.array([], dtype=int))
    cat = [np.concatenate([p[j] for p in parts]) for j in range(8)]
    return Segments(*cat[:7], excluded=ex, proj=proj, idx=cat[7])


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


def subset(s: Segments, mask: np.ndarray) -> Segments:
    """Segmentos selecionados por máscara booleana ou fatia (mesma projeção)."""
    return Segments(s.x0[mask], s.y0[mask], s.x1[mask], s.y1[mask], s.w[mask], s.machine[mask], s.t0[mask],
                    excluded=dict(s.excluded), proj=s.proj, idx=None if s.idx is None else s.idx[mask])


def run_swaths(s: Segments) -> list:
    """Uma faixa por trecho contínuo: buffer da polilinha (ponta reta, junção arredondada).

    Trecho contínuo = segmentos seguidos da mesma máquina (o fim de um é o início do outro) com a mesma largura.
    Para curvas com raio >= meia largura, a área do buffer é a área varrida pela barra (Pappus), igual a Σ L×w; em
    vértices discretos a diferença por junção é ~ h²(θ/2 − tan θ/2), desprezível a 1 Hz. Sem ``idx`` (segmentos montados
    à mão), cai no retângulo por segmento.
    """
    if not len(s):
        return []
    if s.idx is None:
        return swath_polygons(s)
    keep, runs = run_bounds(s)
    return [run_polygon(s, keep[i0:i1]) for i0, i1 in runs]


def run_bounds(s: Segments) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Segmentos com comprimento (``keep``, índices em ``s``) e os trechos contínuos como intervalos [i0, i1) de
    posições em ``keep``. Exige ``idx``."""
    L = np.hypot(s.x1 - s.x0, s.y1 - s.y0)
    keep = np.where(L > 1e-6)[0]
    if not len(keep):
        return keep, []
    brk = np.ones(len(keep), bool)
    if len(keep) > 1:
        a, b = keep[:-1], keep[1:]
        cont = (s.machine[b] == s.machine[a]) & (s.idx[b] == s.idx[a] + 1) & (s.w[b] == s.w[a])
        brk[1:] = ~cont
    starts = np.flatnonzero(brk)
    ends = np.append(starts[1:], len(keep))
    return keep, list(zip(starts.tolist(), ends.tolist()))


def run_polygon(s: Segments, k: np.ndarray):
    """Faixa de um trecho contínuo (``k``: índices dos segmentos em ``s``, em ordem)."""
    xs = np.concatenate([[s.x0[k[0]]], s.x1[k]]); ys = np.concatenate([[s.y0[k[0]]], s.y1[k]])
    line = shapely.linestrings(np.stack([xs, ys], -1))
    return shapely.buffer(line, float(s.w[k[0]]) / 2, cap_style="flat", join_style="round", quad_segs=4)


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
    # geometrias em metros (projeção ``proj``), para quem precisa recortar ou simplificar sem refazer a união
    union_geom: object = field(default=None, repr=False)
    field_geom: object = field(default=None, repr=False)
    proj: Projector | None = field(default=None, repr=False)
    # cálculo incremental (Etapa 6): a união fica em partes (congelada + cauda) e só é montada se alguém pedir
    union_parts: tuple | None = field(default=None, repr=False)

    def union(self):
        """União das faixas em metros (monta a partir das partes, uma vez, se o cálculo foi incremental)."""
        if self.union_geom is None and self.union_parts:
            parts = [p for p in self.union_parts if p is not None and not p.is_empty]
            self.union_geom = unary_union(parts) if parts else Polygon()
        return self.union_geom

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


def overlap_skip_s(t0: np.ndarray) -> float:
    """Vizinhança de trajetória: 4× o intervalo mediano entre inícios de segmento."""
    dts = np.diff(np.sort(t0)); dts = dts[dts > 0]
    return 4 * float(np.median(dts)) if len(dts) else 4.0


def overlap_pieces(polys: list, left, right, mach: np.ndarray, t0: np.ndarray, skip_s: float) -> list:
    """Interseções dos pares (i, j), j > i, que não são vizinhos de trajetória (índices globais em ``polys``)."""
    pieces = []
    for i, j in zip(left, right):
        if j <= i:
            continue
        if mach[i] == mach[j] and abs(t0[j] - t0[i]) <= skip_s:
            continue
        inter = polys[i].intersection(polys[j])
        if not inter.is_empty and inter.area > 1e-3:
            pieces.append(inter)
    return pieces


def overlap_region(polys: list[Polygon], mach: np.ndarray, t0: np.ndarray, skip_s: float):
    """União das interseções entre faixas NÃO adjacentes no tempo (mesma máquina e |Δt| <= skip_s são vizinhas de
    trajetória: a interseção delas é só a junção do traçado, não repasse)."""
    if len(polys) < 2:
        return None
    tree = STRtree(polys)
    left, right = tree.query(polys, predicate="intersects")
    pieces = overlap_pieces(polys, left, right, mach, t0, skip_s)
    return unary_union(pieces) if pieces else None


def coverage_from_segments(s: Segments, field_lonlat: dict | None = None, with_geojson: bool = True) -> CoverageResult:
    polys = swath_polygons(s)
    if not polys:
        return CoverageResult(0.0, 0.0, 0.0, 0.0, 0, s.excluded, None, None)
    gross = float(sum(p.area for p in polys))
    # gross exato = Σ L×w (polygon.area dá o mesmo para retângulos; mantemos a fórmula explícita)
    L = np.hypot(s.x1 - s.x0, s.y1 - s.y0)
    gross = float(np.sum(L * s.w))
    uni = unary_union(run_swaths(s))
    excess = max(gross - uni.area, 0.0)
    keep = L > 1e-6
    t0 = s.t0[keep]
    skip_s = overlap_skip_s(t0)
    ov = overlap_region(polys, s.machine[keep], t0, skip_s)
    res = CoverageResult(gross, float(uni.area), excess, float(ov.area) if ov is not None else 0.0, len(polys), s.excluded, None, None)
    proj = s.proj
    res.union_geom, res.proj = uni, proj
    def to_ll(g):
        return mapping(transform_geom(g, proj.inv))
    if with_geojson:
        res.union_geojson = to_ll(uni)
        res.overlap_geojson = to_ll(ov) if ov is not None else None
    if field_lonlat is not None:
        fld = transform_geom(shape(field_lonlat), proj.fwd)
        res.field_geom = fld
        res.field_area_m2 = float(fld.area)
        res.covered_in_field_m2 = float(uni.intersection(fld).area)
        res.uncovered_in_field_m2 = float(fld.difference(uni).area)
        res.outside_field_m2 = float(uni.difference(fld).area)
    return res


def coverage(df: pd.DataFrame, cfg: CoverageConfig | None = None, field_lonlat: dict | None = None,
             proj: Projector | None = None, with_geojson: bool = True) -> CoverageResult:
    cfg = cfg or CoverageConfig()
    return coverage_from_segments(build_segments(df, cfg, proj), field_lonlat, with_geojson)
