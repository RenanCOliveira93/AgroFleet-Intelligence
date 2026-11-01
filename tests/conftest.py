import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agrofleet.geo import Projector  # noqa: E402
from agrofleet.schema import Gnss, State, TelemetryPoint  # noqa: E402

LAT0, LON0 = -16.70, -47.60
T0 = datetime(2026, 3, 10, 10, 0, 0, tzinfo=timezone.utc)
PROJ = Projector.for_points([LON0], [LAT0])
X0, Y0 = (float(a[0]) for a in PROJ.fwd([LON0], [LAT0]))


def make_pts(waypoints, *, machine="T-1", speed=5.0, width=24.0, dt=1.0, state=State.working, drop=(), noise=0.0, seed=0,
             t0=T0, accuracy=0.02, fix=Gnss.rtk_fixed):
    """Pontos 1 Hz ao longo de uma polilinha em metros locais. ``drop`` = intervalos [(i0, i1)) de índices sem GNSS."""
    rng = np.random.default_rng(seed)
    pts, t, i = [], 0.0, 0
    for (xa, ya), (xb, yb) in zip(waypoints[:-1], waypoints[1:]):
        L = float(np.hypot(xb - xa, yb - ya))
        n = max(int(round(L / (speed * dt))), 1)
        for k in range(n):
            f = k / n
            x, y = xa + (xb - xa) * f, ya + (yb - ya) * f
            lo, la = PROJ.inv([X0 + x + rng.normal(0, noise)], [Y0 + y + rng.normal(0, noise)])
            nogps = any(a <= i < b for a, b in drop)
            pts.append(TelemetryPoint(machine_id=machine, ts=t0 + timedelta(seconds=t), origin="simulated",
                                      lat=None if nogps else float(la[0]), lon=None if nogps else float(lo[0]),
                                      speed_ms=speed, state=state, gnss_fix=Gnss.none if nogps else fix,
                                      accuracy_m=None if nogps else accuracy, working_width_m=width,
                                      flow_lpm=speed * 3.6 * width * 100 / 600, application_rate_lha=100.0, pressure_bar=3.0))
            t += dt; i += 1
    return pts


def passes(n, spacing, length=500.0, x_start=12.0):
    """Boustrophedon em n passadas verticais (sem trechos de cabeceira: o salto entre passadas é um segmento 'transport')."""
    wp = []
    for k in range(n):
        x = x_start + k * spacing
        wp += [(x, 0), (x, length)] if k % 2 == 0 else [(x, length), (x, 0)]
    return wp
