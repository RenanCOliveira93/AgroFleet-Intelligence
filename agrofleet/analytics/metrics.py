"""Métricas operacionais por máquina e relatórios agregados.

Contabilidade de tempo: o intervalo [t_i, t_{i+1}] é atribuído ao estado da amostra i, SE dt <= limite; caso contrário
o intervalo inteiro vai para ``no_data`` (não sabemos o que aconteceu; não assumimos nada). Distância idem: só soma
trechos consecutivos com posição nos dois extremos e dt <= limite; o trecho em lacuna é reportado à parte.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..geo import Projector, ha
from .coverage import CoverageConfig, build_segments, coverage_from_segments
from .frame import median_dt_s

STATES = ["working", "transport", "idle", "refilling", "fault", "off", "unknown"]


@dataclass
class MetricsConfig:
    max_gap_s: float = 10.0
    max_jump_ms: float = 25.0   # velocidade implícita acima disso entre duas posições = salto (descartado da distância)


def _intervals(g: pd.DataFrame, cfg: MetricsConfig):
    dt = g["ts"].diff().shift(-1).dt.total_seconds().to_numpy()
    thr = max(cfg.max_gap_s, 3 * (median_dt_s(g["ts"]) if len(g) > 2 else 0) or 0)
    valid = (dt > 0) & (dt <= thr)
    return dt, valid, thr


def machine_metrics(g: pd.DataFrame, cfg: MetricsConfig | None = None, cov: CoverageConfig | None = None,
                    proj: Projector | None = None, with_coverage: bool = True) -> dict:
    """Métricas de UMA máquina (DataFrame já ordenado e filtrado pelo período)."""
    cfg = cfg or MetricsConfig()
    g = g.sort_values("ts").reset_index(drop=True)
    out: dict = {"machine_id": g["machine_id"].iloc[0] if len(g) else None, "n_samples": int(len(g)),
                 "origins": sorted(set(g["origin"].dropna())) if len(g) else []}
    if len(g) < 2:
        return {**out, "note": "amostras insuficientes"}
    dt, valid, thr = _intervals(g, cfg)
    n = len(g) - 1
    dt, valid = dt[:n], valid[:n]
    states = g["state"].to_numpy()[:n]
    span = float((g["ts"].iloc[-1] - g["ts"].iloc[0]).total_seconds())
    t_state = {s: float(dt[valid & (states == s)].sum()) for s in STATES}
    gap_mask = (dt > thr)
    t_state["no_data"] = float(dt[gap_mask].sum())
    out.update(start=g["ts"].iloc[0].isoformat(), end=g["ts"].iloc[-1].isoformat(), span_h=span / 3600,
               median_dt_s=median_dt_s(g["ts"]), gap_threshold_s=thr, gap_count=int(gap_mask.sum()),
               time_h={k: v / 3600 for k, v in t_state.items()})
    engaged = sum(t_state[s] for s in ("working", "transport", "idle", "refilling", "fault"))
    out["idle_pct_of_engaged"] = 100 * t_state["idle"] / engaged if engaged > 0 else None
    out["working_pct_of_engaged"] = 100 * t_state["working"] / engaged if engaged > 0 else None

    # distância
    pos = g[["lat", "lon"]].notna().all(axis=1).to_numpy()
    if pos.any():
        proj = proj or Projector.for_points(g.loc[pos, "lon"], g.loc[pos, "lat"])
        x = np.full(len(g), np.nan); y = np.full(len(g), np.nan)
        xx, yy = proj.fwd(g.loc[pos, "lon"], g.loc[pos, "lat"]); x[pos] = xx; y[pos] = yy
        d = np.hypot(np.diff(x), np.diff(y))
        both = pos[:-1] & pos[1:]
        speed_impl = np.where(dt > 0, d / np.where(dt > 0, dt, 1), np.nan)
        jump = both & valid & (speed_impl > cfg.max_jump_ms)
        use = both & valid & ~jump
        out["distance_km"] = float(d[use].sum() / 1000)
        out["distance_working_km"] = float(d[use & (states == "working")].sum() / 1000)
        # trechos NÃO medidos: lacuna de tempo (reta entre amostras) e perda de posição (reta entre a última e a próxima posição válida)
        vi = np.flatnonzero(pos)
        dv = np.hypot(np.diff(x[vi]), np.diff(y[vi])) if len(vi) > 1 else np.array([])
        across_loss = float(dv[np.diff(vi) > 1].sum()) if len(vi) > 1 else 0.0
        out["distance_across_gaps_km_straight_line"] = float((d[both & gap_mask].sum() + across_loss) / 1000)
        out["position_jumps_discarded"] = int(jump.sum())
    else:
        out.update(distance_km=None, distance_working_km=None, distance_across_gaps_km_straight_line=None, position_jumps_discarded=0)

    # volume aplicado (duas fontes independentes, quando existirem)
    fl = g["flow_lpm"].to_numpy()[:n]
    m = valid & (states == "working") & ~np.isnan(fl)
    out["volume_from_flow_l"] = float(np.sum(fl[m] * dt[m] / 60)) if m.any() else None
    cons = g["consumption_l"].to_numpy()
    c = cons[~np.isnan(cons)]
    if len(c) >= 2:
        dc = np.diff(c)
        out["volume_from_counter_l"] = float(dc[dc >= 0].sum())
        out["counter_resets"] = int((dc < 0).sum())
    else:
        out["volume_from_counter_l"] = None
        out["counter_resets"] = 0
    a, b = out["volume_from_flow_l"], out["volume_from_counter_l"]
    out["volume_source_discrepancy_pct"] = 100 * (a - b) / b if a is not None and b else None

    # GNSS / qualidade
    out["gnss_fix_pct"] = {k: float(100 * v) for k, v in g["gnss_fix"].value_counts(normalize=True).items()}
    out["position_missing_pct"] = float(100 * (~pos).mean())
    out["accuracy_m_median"] = float(g["accuracy_m"].median()) if g["accuracy_m"].notna().any() else None
    out["null_pct"] = {c: float(100 * g[c].isna().mean()) for c in
                       ["speed_ms", "flow_lpm", "pressure_bar", "tank_level_l", "working_width_m", "application_rate_lha"]}
    codes = sorted({c for lst in g["error_codes"] for c in (lst or [])})
    out["error_codes"] = codes

    if with_coverage:
        seg = build_segments(g, cov or CoverageConfig(max_gap_s=cfg.max_gap_s), proj)
        cr = coverage_from_segments(seg, with_geojson=False)
        out["coverage"] = cr.summary()
        wh = t_state["working"] / 3600
        out["productivity"] = {
            "unique_ha_per_working_h": ha(cr.unique_m2) / wh if wh > 0 and cr.unique_m2 > 0 else None,
            "gross_ha_per_working_h": ha(cr.gross_m2) / wh if wh > 0 and cr.gross_m2 > 0 else None,
            "unique_ha_per_engaged_h": ha(cr.unique_m2) / (engaged / 3600) if engaged > 0 and cr.unique_m2 > 0 else None}
        v = out["volume_from_flow_l"] if out["volume_from_flow_l"] is not None else out["volume_from_counter_l"]
        out["realized_rate_lha_on_unique_area"] = v / ha(cr.unique_m2) if v and cr.unique_m2 > 0 else None
    return out


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def filter_period(df: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    if start is not None:
        df = df[df["ts"] >= _utc(start)]
    if end is not None:
        df = df[df["ts"] <= _utc(end)]
    return df


GROUPS = {"machine": "machine_id", "property": "property_id", "field": "field_id", "operator": "operator_id"}


def report(df: pd.DataFrame, by: str = "machine", start=None, end=None, cfg: MetricsConfig | None = None) -> dict:
    """Relatório agregado. Para property/field/operator a área única é a união entre MÁQUINAS do grupo (duas
    máquinas no mesmo talhão sobrepostas contam uma vez). Amostras sem a chave do grupo vão para ``sem_valor``
    (reportadas, nunca atribuídas a um grupo inventado)."""
    if by not in GROUPS:
        raise ValueError(f"group_by inválido: {by} (use {list(GROUPS)})")
    cfg = cfg or MetricsConfig()
    df = filter_period(df, start, end)
    key = GROUPS[by]
    rows = []
    for val, grp in df.groupby(df[key].fillna("__sem_valor__"), sort=True):
        per_machine = [machine_metrics(m, cfg, with_coverage=False) for _, m in grp.groupby("machine_id")]
        seg = build_segments(grp, CoverageConfig(max_gap_s=cfg.max_gap_s))
        cr = coverage_from_segments(seg, with_geojson=False)
        th = {s: sum(pm.get("time_h", {}).get(s, 0) for pm in per_machine) for s in STATES + ["no_data"]}
        eng = sum(th[s] for s in ("working", "transport", "idle", "refilling", "fault"))
        vols = [pm["volume_from_flow_l"] if pm.get("volume_from_flow_l") is not None else pm.get("volume_from_counter_l")
                for pm in per_machine if pm.get("n_samples", 0) >= 2]
        vol = sum(v for v in vols if v is not None) if any(v is not None for v in vols) else None
        rows.append({key: None if val == "__sem_valor__" else val, "sem_valor": val == "__sem_valor__",
                     "machines": sorted(grp["machine_id"].unique()), "origins": sorted(set(grp["origin"].dropna())),
                     "n_samples": int(len(grp)), "time_h": th, "engaged_h": eng,
                     "idle_pct_of_engaged": 100 * th["idle"] / eng if eng > 0 else None,
                     "distance_km": sum(pm.get("distance_km") or 0 for pm in per_machine),
                     "volume_l": vol, "coverage": cr.summary(),
                     "unique_ha_per_working_h": ha(cr.unique_m2) / th["working"] if th["working"] > 0 and cr.unique_m2 > 0 else None})
    return {"group_by": by, "period": {"start": str(start) if start else None, "end": str(end) if end else None}, "rows": rows}
