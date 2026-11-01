"""Projeção local (UTM) e distâncias. Toda geometria de área/comprimento é feita em metros num UTM escolhido
a partir do centróide dos dados, nunca em graus."""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from pyproj import CRS, Transformer


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=32)
def _tf(epsg: int):
    return (Transformer.from_crs(4326, epsg, always_xy=True), Transformer.from_crs(epsg, 4326, always_xy=True))


class Projector:
    """lon/lat <-> x/y (m) em UTM. ``Projector.for_points`` escolhe a zona pelo centróide."""

    def __init__(self, epsg: int):
        self.epsg = epsg
        self._fwd, self._inv = _tf(epsg)

    @classmethod
    def for_points(cls, lons, lats) -> "Projector":
        lons = np.asarray(lons, float)
        lats = np.asarray(lats, float)
        return cls(utm_epsg(float(np.mean(lons)), float(np.mean(lats))))

    def fwd(self, lons, lats):
        x, y = self._fwd.transform(np.asarray(lons, float), np.asarray(lats, float))
        return np.asarray(x), np.asarray(y)

    def inv(self, xs, ys):
        lon, lat = self._inv.transform(np.asarray(xs, float), np.asarray(ys, float))
        return np.asarray(lon), np.asarray(lat)

    @property
    def crs(self) -> CRS:
        return CRS.from_epsg(self.epsg)


def transform_geom(geom, fn):
    """Aplica fn(x_array, y_array) -> (x2, y2) a todas as coordenadas de uma geometria shapely."""
    import shapely

    def f(c):
        a, b = fn(c[:, 0], c[:, 1])
        return np.column_stack([a, b])
    return shapely.transform(geom, f)


def haversine_m(lat1, lon1, lat2, lon2):
    """Distância geodésica esférica (R=6371008,8 m); usada só para checagens independentes da projeção."""
    r = 6371008.8
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def ha(m2: float) -> float:
    return m2 / 10_000.0


def kmh(ms: float) -> float:
    return ms * 3.6


def expected_flow_lpm(rate_lha: float, speed_ms: float, width_m: float) -> float:
    """Vazão (L/min) necessária para aplicar ``rate_lha`` a ``speed_ms`` com barra de ``width_m``.
    L/min = L/ha * (km/h) * m / 600.  Derivação em docs/pt-BR/06_algoritmos_modelos.md."""
    return rate_lha * kmh(speed_ms) * width_m / 600.0


def finite(x) -> bool:
    return x is not None and isinstance(x, (int, float, np.floating)) and math.isfinite(x)
