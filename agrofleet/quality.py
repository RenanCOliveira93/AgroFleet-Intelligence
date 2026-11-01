"""Validação de qualidade de telemetria (upload de arquivo ou janela do banco). Só relata; não corrige nem preenche."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .adapters.base import AdapterResult
from .analytics.frame import median_dt_s, to_frame
from .geo import Projector

FIELDS = ["lat", "lon", "speed_ms", "flow_lpm", "pressure_bar", "tank_level_l", "consumption_l", "working_width_m",
          "application_rate_lha", "accuracy_m"]


def assess(res: AdapterResult, *, max_gap_s: float = 30.0, max_speed_ms: float = 25.0, max_jump_ms: float = 40.0,
           now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    pts = res.points
    reasons = Counter(r["reason"].split(":")[0][:80] for r in res.rejected)
    rep: dict = {"rows_input": res.n_input, "rows_ok": len(pts), "rows_rejected": res.n_rejected_total,
                 "rejection_reasons": dict(reasons.most_common(10)), "rejected_sample": res.rejected[:20],
                 "adapter_warnings": res.warnings, "mapping": res.meta, "flags": []}
    flags = rep["flags"]
    if not pts:
        rep["verdict"] = "blocking"
        flags.append({"level": "error", "code": "no_valid_rows", "msg": "nenhuma linha válida"})
        return rep
    df = to_frame(pts)
    rep["machines"] = sorted(df["machine_id"].unique())
    rep["origins"] = dict(df["origin"].value_counts())
    rep["time_range"] = {"start": df["ts"].min().isoformat(), "end": df["ts"].max().isoformat()}
    if (df["origin"] == "unknown").any():
        flags.append({"level": "warn", "code": "origin_unknown", "msg": "origem simulated/real não declarada em parte das linhas"})
    dup = int(df.duplicated(["machine_id", "ts"]).sum())
    if dup:
        flags.append({"level": "warn", "code": "duplicates", "msg": f"{dup} linhas com (machine_id, ts) repetido"})
    # ordem original do arquivo
    ooo = 0
    for _, g in pd.DataFrame({"m": [p.machine_id for p in pts], "t": [p.ts for p in pts]}).groupby("m"):
        ooo += int((g["t"].diff().dt.total_seconds() < 0).sum())
    if ooo:
        flags.append({"level": "warn", "code": "out_of_order", "msg": f"{ooo} amostras fora de ordem cronológica no arquivo (reordenadas na análise)"})
    future = int((df["ts"] > pd.Timestamp(now + timedelta(minutes=5))).sum())
    if future:
        flags.append({"level": "error", "code": "future_timestamps", "msg": f"{future} timestamps no futuro"})
    old = int((df["ts"] < pd.Timestamp("2000-01-01", tz="UTC")).sum())
    if old:
        flags.append({"level": "error", "code": "ancient_timestamps", "msg": f"{old} timestamps anteriores a 2000 (epoch/unidade errada?)"})
    per = {}
    for m, g in df.groupby("machine_id"):
        g = g.drop_duplicates("ts").sort_values("ts")
        d = g["ts"].diff().dt.total_seconds().dropna()
        entry = {"n": int(len(g)), "median_dt_s": median_dt_s(g["ts"]), "p95_dt_s": float(d.quantile(0.95)) if len(d) else None,
                 "max_dt_s": float(d.max()) if len(d) else None, "gaps_over_limit": int((d > max_gap_s).sum()),
                 "gap_time_s": float(d[d > max_gap_s].sum()) if len(d) else 0.0}
        pos = g.dropna(subset=["lat", "lon"])
        entry["position_missing_pct"] = float(100 * (1 - len(pos) / len(g)))
        if len(pos) > 2:
            pr = Projector.for_points(pos["lon"], pos["lat"])
            x, y = pr.fwd(pos["lon"], pos["lat"])
            dt = pos["ts"].diff().dt.total_seconds().to_numpy()[1:]
            sp = np.hypot(np.diff(x), np.diff(y)) / np.where(dt > 0, dt, np.nan)
            entry["position_jumps"] = int(np.nansum(sp > max_jump_ms))
            entry["bbox"] = [float(pos["lon"].min()), float(pos["lat"].min()), float(pos["lon"].max()), float(pos["lat"].max())]
            if entry["position_jumps"]:
                flags.append({"level": "warn", "code": "position_jumps", "msg": f"{m}: {entry['position_jumps']} saltos de posição (> {max_jump_ms} m/s implícitos)"})
        per[m] = entry
        if entry["gaps_over_limit"]:
            flags.append({"level": "warn", "code": "gaps", "msg": f"{m}: {entry['gaps_over_limit']} lacunas > {max_gap_s:.0f} s ({entry['gap_time_s']:.0f} s no total)"})
    rep["per_machine"] = per
    rep["null_pct"] = {c: float(100 * df[c].isna().mean()) for c in FIELDS}
    rep["gnss_fix"] = dict(df["gnss_fix"].value_counts())
    rep["states"] = dict(df["state"].value_counts())
    rep["sensor_quality_declared"] = {c: dict(df[c].value_counts()) for c in df.columns if c.startswith("q_") and df[c].notna().any()}
    # sanidade de unidade/faixa (suspeitas, não correção)
    sus = []
    if df["speed_ms"].max() > max_speed_ms:
        sus.append(f"velocidade máxima {df['speed_ms'].max():.1f} m/s > {max_speed_ms} (unidade km/h declarada como m/s?)")
    if df["pressure_bar"].median() > 15:
        sus.append(f"pressão mediana {df['pressure_bar'].median():.1f} 'bar' é alta para pulverização (kPa/psi?)")
    if df["flow_lpm"].median() > 1500:
        sus.append(f"vazão mediana {df['flow_lpm'].median():.0f} L/min é muito alta (L/h?)")
    if df["working_width_m"].max() > 60:
        sus.append(f"largura de trabalho {df['working_width_m'].max():.0f} m é incomum (cm/ft?)")
    for s in sus:
        flags.append({"level": "warn", "code": "unit_suspect", "msg": s})
    c = df.dropna(subset=["consumption_l"])
    if len(c) > 2 and (c.groupby("machine_id")["consumption_l"].diff() < 0).any():
        flags.append({"level": "info", "code": "counter_reset", "msg": "contador de consumo decresce (reset); o volume é somado só nos incrementos"})
    if (df["gnss_fix"] == "none").any() and df.loc[df["gnss_fix"] == "none", "lat"].notna().any():
        flags.append({"level": "warn", "code": "fix_none_with_position", "msg": "há posição com gnss_fix=none (inconsistente)"})
    rep["verdict"] = "blocking" if any(f["level"] == "error" for f in flags) else ("warnings" if any(f["level"] == "warn" for f in flags) else "ok")
    return rep
