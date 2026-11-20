"""Cálculo incremental da missão (Etapa 6): o passado congela, só a cauda recente é recalculada.

Por que é exato: a cobertura e a série só ACRESCENTAM no tempo. Uma faixa, um par de faixas sobrepostas ou um
intervalo de 10 min que dependem apenas de amostras anteriores a um corte não mudam com dado novo posterior ao corte.
O cache guarda essas partes e confere, a cada chamada, que o dado anterior ao corte é o mesmo (hash das colunas que
entram no cálculo). Se não for (chegada tardia, reentrega com valor diferente, outro cadastro ou parâmetro), descarta
tudo e recalcula do zero, registrando o motivo em ``stats``. O resultado é o mesmo do cálculo completo, a menos do
arredondamento do GEOS ao unir as mesmas peças em outra ordem (o próprio cálculo completo varia assim: 1e-5 m² em 333 m²
de região ≥2× no cenário A de 5 h). Testado com tolerância de 1e-6 relativo em ``tests/test_incremental.py``.

Corte ``cut`` = início da operação + K intervalos, com K = ⌊(última amostra − ``margin_s`` − início) / intervalo⌋. A
margem (padrão 120 s) absorve atraso e fora de ordem comuns do enlace sem descartar o cache. O que é congelado:
- **intervalos da série** 0..K−1 (entradas e a união acumulada ∩ talhão);
- **trechos contínuos** cuja quebra já está decidida por amostras anteriores ao corte (a amostra que decide se o trecho
  continua, índice ``último + 2``, é anterior ao corte), unidos em ``U``; a área em talhão de ``U`` é acumulada;
- **faixas por segmento** com as duas pontas antes do corte, com a árvore espacial e a união das sobreposições entre
  elas (``ov``).
Na cauda: trechos não congelados (união ``T``), faixas novas contra a árvore congelada e entre si, intervalos ≥ K.
Áreas: única = |U| + |T − U|; em talhão = |U ∩ F| + |(T − U) ∩ F|; região ≥2× = |ov| + |N − ov|, N = peças novas.

Parâmetros globais do cálculo completo (limite de lacuna pelo dt mediano, vizinhança de trajetória pelo dt mediano,
zona UTM, cadastro da operação, talhão e configuração) fazem parte da chave: se mudarem, o cache recomeça.
"""
from __future__ import annotations

import hashlib
import json
import threading

import numpy as np
import pandas as pd
from shapely import STRtree, box
from shapely.geometry import shape
from shapely.ops import unary_union

from ..geo import transform_geom
from .coverage import (CoverageResult, Segments, overlap_pieces, overlap_skip_s, run_bounds, run_polygon, subset,
                       swath_polygons)
from .frame import median_dt_s

# colunas que entram na missão (segmentos, tempos e médias por intervalo)
PREFIX_COLS = ("ts", "lat", "lon", "state", "accuracy_m", "working_width_m", "speed_ms", "flow_lpm",
               "fuel_rate_lph", "q_fuel_rate", "battery_power_kw", "q_battery")


def _epoch(ts: pd.Series) -> np.ndarray:
    return (ts - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()


def prefix_hash(g: pd.DataFrame, n: int) -> str:
    cols = [c for c in PREFIX_COLS if c in g.columns]
    h = pd.util.hash_pandas_object(g.iloc[:n][cols], index=False).to_numpy()
    return hashlib.blake2b(h.tobytes(), digest_size=16).hexdigest()


def _mask(n: int, idx: np.ndarray) -> np.ndarray:
    m = np.zeros(n, bool)
    m[idx] = True
    return m


def _minus(a, b):
    """a − b, recortando b à caixa de a (b costuma ser a união grande; a, a peça nova pequena)."""
    if b is None or b.is_empty:
        return a
    return a.difference(b.intersection(box(*a.bounds)))


class MissionCache:
    """Estado incremental de UMA operação. Não é seguro para uso concorrente: quem chama segura ``lock``."""

    def __init__(self, margin_s: float = 120.0):
        self.margin_s = float(margin_s)
        self.lock = threading.RLock()
        self.stats = {"calls": 0, "resets": 0, "advances": 0, "last_reset": None, "frozen_buckets": 0}
        self._clear()

    def _clear(self):
        self.key = None
        self.F = None
        self.cut = None
        self.K = 0                       # intervalos congelados na cobertura (a série pode estar atrás)
        self.n_prefix, self.hash = 0, None
        self.n_runs, self.U, self.U_area, self.U_in = 0, None, 0.0, 0.0
        self.polys, self.tree, self.ov, self.ov_area = [], None, None, 0.0
        self.series, self.cum = [], None

    def release(self):
        """Libera a memória (fim de uma reprodução); a próxima chamada recomeça do zero, sem contar como descarte."""
        with self.lock:
            self._clear()

    def reset(self, why: str):
        if self.key is not None:
            self.stats["resets"] += 1
            self.stats["last_reset"] = why
        self._clear()

    # ------------------------------------------------------------------------------------------------------------
    def coverage(self, g: pd.DataFrame, seg: Segments, op, field_lonlat: dict | None, cfg) -> CoverageResult:
        """Mesmo resultado de ``coverage_from_segments(seg, field_lonlat, with_geojson=False)``."""
        self.stats["calls"] += 1
        ts = _epoch(g["ts"])
        n = len(ts)
        L = np.hypot(seg.x1 - seg.x0, seg.y1 - seg.y0)
        kidx = np.where(L > 1e-6)[0]
        t0k = seg.t0[kidx]
        mach = seg.machine[kidx]
        skip_s = overlap_skip_s(t0k)
        thr = max(cfg.coverage.max_gap_s, 3 * (median_dt_s(g["ts"]) if n > 2 else 0) or 0)
        key = (op.model_dump_json(), json.dumps(field_lonlat, sort_keys=True) if field_lonlat else None, repr(cfg),
               seg.proj.epsg if seg.proj is not None else None, thr, skip_s, self.margin_s)
        if key != self.key:
            self.reset("cadastro, talhão, configuração ou parâmetro global (dt mediano, zona UTM) mudou")
        elif n < self.n_prefix or prefix_hash(g, self.n_prefix) != self.hash:
            self.reset("dado anterior ao corte mudou (chegada tardia ou reentrega diferente)")
        if self.key is None:
            self.key = key
            self.F = transform_geom(shape(field_lonlat), seg.proj.fwd) if field_lonlat is not None else None
        if not len(kidx):
            return CoverageResult(0.0, 0.0, 0.0, 0.0, 0, seg.excluded, None, None)

        kb, runs = run_bounds(seg)                            # kb == kidx
        t_start = pd.Timestamp(op.start).timestamp()
        B = cfg.bucket_min * 60
        K = max(self.K, int(np.floor((ts[-1] - self.margin_s - t_start) / B)), 0)
        if K > self.K:
            self._advance(g, seg, ts, kidx, kb, runs, mach, t0k, skip_s, t_start + K * B, K)

        # ---- cauda ---------------------------------------------------------------------------------------------
        F = self.F
        tail = [run_polygon(seg, kb[i0:i1]) for i0, i1 in runs[self.n_runs:]]
        T = unary_union(tail) if tail else None
        unique, inside = self.U_area, self.U_in
        if T is not None and not T.is_empty:
            fresh = _minus(T, self.U)
            unique += fresh.area
            if F is not None:
                inside += fresh.intersection(F).area
        nf = len(self.polys)
        tp = swath_polygons(subset(seg, _mask(len(seg), kidx[nf:])))
        ov_area = self.ov_area
        if tp:
            allp = self.polys + tp
            pieces = []
            if self.tree is not None:
                p, i = self.tree.query(tp, predicate="intersects")
                pieces += overlap_pieces(allp, i, p + nf, mach, t0k, skip_s)
            p, q = STRtree(tp).query(tp, predicate="intersects")
            pieces += overlap_pieces(allp, p + nf, q + nf, mach, t0k, skip_s)
            if pieces:
                ov_area += _minus(unary_union(pieces), self.ov).area
        gross = float(np.sum(L * seg.w))
        res = CoverageResult(gross, float(unique), max(gross - unique, 0.0), float(ov_area), int(len(kidx)),
                             seg.excluded, None, None)
        res.proj, res.union_parts = seg.proj, (self.U, T)
        if F is not None:
            res.field_geom = F
            res.field_area_m2 = float(F.area)
            res.covered_in_field_m2 = float(inside)
            res.uncovered_in_field_m2 = max(float(F.area - inside), 0.0)
            res.outside_field_m2 = max(float(unique - inside), 0.0)
        return res

    def _advance(self, g, seg, ts, kidx, kb, runs, mach, t0k, skip_s, cut, K):
        n = len(ts)
        F = self.F
        # trechos contínuos cuja quebra já está decidida antes do corte
        r = self.n_runs
        while r < len(runs):
            k_last = int(seg.idx[kb[runs[r][1] - 1]])
            if not (k_last + 2 < n and ts[k_last + 2] < cut):
                break
            r += 1
        if r > self.n_runs:
            new = unary_union([run_polygon(seg, kb[i0:i1]) for i0, i1 in runs[self.n_runs:r]])
            fresh = _minus(new, self.U)
            if F is not None:
                self.U_in += fresh.intersection(F).area
            self.U = new if self.U is None else unary_union([self.U, new])
            self.U_area = float(self.U.area)
            self.n_runs = r
        # faixas por segmento com as duas pontas antes do corte, e as sobreposições entre elas
        end_ts = ts[seg.idx[kidx] + 1]
        nf_new = int(np.sum(end_ts < cut))
        nf = len(self.polys)
        if nf_new > nf:
            newp = swath_polygons(subset(seg, _mask(len(seg), kidx[nf:nf_new])))
            allp = self.polys + newp
            tree = STRtree(allp)
            p, i = tree.query(newp, predicate="intersects")
            pieces = overlap_pieces(allp, i, p + nf, mach, t0k, skip_s)
            if pieces:
                self.ov = unary_union(pieces if self.ov is None else [self.ov, *pieces])
                self.ov_area = float(self.ov.area)
            self.polys, self.tree = allp, tree
        self.cut, self.K = cut, K
        self.n_prefix = min(int(np.searchsorted(ts, cut, "left")) + 1, n)   # + a 1ª amostra depois do corte (dt)
        self.hash = prefix_hash(g, self.n_prefix)
        self.stats["advances"] += 1

    # ------------------------------------------------------------------------------------------------------------
    def series_prefix(self, ctx, bucket_fn) -> tuple[list, object]:
        """Intervalos congelados da série (0..K−1) e a união acumulada ao fim deles. Calcula os que faltam."""
        if self.K >= ctx.nb:                                  # dado truncado no corte (``as_of``): sem atalho
            return [], None
        acc = 0.0                                             # mesma soma corrente do cálculo completo
        for s in self.series:
            acc += s["new_ha"]
        while len(self.series) < self.K:
            i = len(self.series)
            e, cum = bucket_fn(ctx, i, self.cum, acc)
            if e is None:
                self.reset("intervalo congelado vazio")
                return [], None
            self.series.append(e)
            self.cum = cum
            acc += e["new_ha"]
        self.stats["frozen_buckets"] = len(self.series)
        return [dict(e) for e in self.series], self.cum
