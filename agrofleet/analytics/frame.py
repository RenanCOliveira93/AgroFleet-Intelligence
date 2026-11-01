"""TelemetryPoint <-> DataFrame. Colunas opcionais ausentes viram NaN/None, nunca valor inventado."""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from ..schema import SENSORS, TelemetryPoint

NUM = ["lat", "lon", "speed_ms", "accuracy_m", "flow_lpm", "pressure_bar", "tank_level_l", "consumption_l",
       "working_width_m", "application_rate_lha"]
COLS = ["machine_id", "ts", "origin", *NUM, "state", "gnss_fix", "error_codes", "property_id", "field_id", "operator_id",
        *[f"q_{s}" for s in SENSORS]]


def to_frame(points: Iterable[TelemetryPoint]) -> pd.DataFrame:
    rows = []
    for p in points:
        r = {"machine_id": p.machine_id, "ts": p.ts, "origin": p.origin, "state": p.state.value, "gnss_fix": p.gnss_fix.value,
             "error_codes": list(p.error_codes), "property_id": p.property_id, "field_id": p.field_id, "operator_id": p.operator_id}
        for c in NUM:
            r[c] = getattr(p, c)
        for s in SENSORS:
            q = p.sensor_quality.get(s)
            r[f"q_{s}"] = q.value if q else None
        rows.append(r)
    df = pd.DataFrame(rows, columns=COLS)
    return normalize(df)


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    for c in COLS:
        if c not in df.columns:
            df[c] = None
    df = df[COLS].copy()
    for c in NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.sort_values(["machine_id", "ts"], kind="stable").reset_index(drop=True)


def median_dt_s(ts: pd.Series) -> float:
    d = ts.diff().dt.total_seconds().dropna()
    d = d[d > 0]
    return float(np.median(d)) if len(d) else float("nan")
