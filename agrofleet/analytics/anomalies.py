"""Detecção de anomalias e eventos de comportamento.

PRINCÍPIOS
- Alertas são INFORMATIVOS (``informational=True``). Nada aqui gera, sugere ou envia comando para a máquina.
- Regra só roda se o sensor existe (fração mínima de valores não nulos no período). Regras puladas são listadas em
  ``skipped_rules`` com o motivo; nunca se preenche valor ausente.
- Pressão é medida GLOBAL (linha/barra): o sistema não diagnostica bico entupido a partir dela. A mensagem de alerta
  sempre diz isso. A causa de qualquer desvio é deixada à verificação humana.
- A parte de ML (IsolationForest) é não supervisionada, experimental e rotulada como tal.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..geo import expected_flow_lpm
from .frame import median_dt_s

INFO_NOTE = "Alerta informativo; decisões críticas permanecem com sistemas certificados e pessoas."
PRESSURE_NOTE = "A pressão é medida global: não permite identificar bico, filtro ou mangueira específicos."


@dataclass
class AnomalyConfig:
    min_sensor_coverage: float = 0.20      # fração mínima de não-nulos para considerar que o sensor existe
    min_duration_s: float = 20.0           # evento sustentado
    flow_tol_pct: float = 15.0
    target_rate_lha: float | None = None   # se None usa application_rate_lha reportada
    pressure_band_bar: tuple[float, float] | None = None
    pressure_z: float = 6.0                # |z| robusto (MAD) sobre pressão em trabalho
    speed_band_ms: tuple[float, float] | None = None
    speed_rel_dev: float = 0.35            # sem banda: desvio relativo à mediana de trabalho da própria máquina
    min_working_speed_ms: float = 0.5
    stopped_speed_ms: float = 0.2
    gnss_min_s: float = 10.0
    expected_fix: str | None = "rtk_fixed"  # None = não alertar degradação
    max_accuracy_m: float | None = 0.10
    gap_alert_s: float = 30.0
    flatline_s: float = 60.0
    use_iforest: bool = True
    iforest_contamination: float = 0.02
    iforest_min_samples: int = 300
    seed: int = 0


@dataclass
class Alert:
    machine_id: str
    type: str
    start: pd.Timestamp
    end: pd.Timestamp
    message: str
    evidence: dict
    rule: str
    severity: str = "info"        # info | warning (nunca "critical": o sistema não decide criticidade operacional)
    origin: str = "unknown"
    informational: bool = True
    experimental: bool = False

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.machine_id}|{self.type}|{self.rule}|{self.start.isoformat()}".encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {"id": self.id, "machine_id": self.machine_id, "type": self.type, "start": self.start.isoformat(),
                "end": self.end.isoformat(), "duration_s": (self.end - self.start).total_seconds(), "severity": self.severity,
                "message": self.message, "evidence": self.evidence, "rule": self.rule, "origin": self.origin,
                "informational": True, "experimental": self.experimental, "note": INFO_NOTE}


@dataclass
class AnomalyResult:
    alerts: list[Alert] = field(default_factory=list)
    skipped_rules: list[dict] = field(default_factory=list)


def runs(mask: np.ndarray, t: np.ndarray, min_s: float, max_hole: float = 0.0):
    """Trechos consecutivos de True com duração >= min_s. ``t`` em segundos. Buracos de até max_hole s são fechados."""
    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    out, s, p = [], idx[0], idx[0]
    for i in idx[1:]:
        if t[i] - t[p] > max_hole + (t[p + 1] - t[p]) + 1e-9:   # buraco maior que o tolerado: fecha o trecho
            out.append((s, p)); s = i
        p = i
    out.append((s, p))
    return [(a, b) for a, b in out if t[b] - t[a] >= min_s]


def _robust_z(x: np.ndarray) -> np.ndarray:
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med))
    scale = 1.4826 * mad if mad > 1e-9 else np.nanstd(x) or 1.0
    return (x - med) / scale


def has_sensor(g: pd.DataFrame, col: str, cfg: AnomalyConfig) -> bool:
    return bool(len(g)) and g[col].notna().mean() >= cfg.min_sensor_coverage


def detect(df: pd.DataFrame, cfg: AnomalyConfig | None = None) -> AnomalyResult:
    cfg = cfg or AnomalyConfig()
    res = AnomalyResult()
    for mid, g in df.groupby("machine_id", sort=False):
        _detect_machine(g.sort_values("ts").reset_index(drop=True), cfg, res)
    res.alerts.sort(key=lambda a: (a.start, a.machine_id, a.type))
    return res


def _mk(res, g, cfg, i0, i1, typ, msg, ev, rule, sev="info", exp=False):
    t0, t1 = g["ts"].iloc[i0], g["ts"].iloc[i1]
    origins = sorted(set(g["origin"].iloc[i0:i1 + 1].dropna())) or ["unknown"]
    res.alerts.append(Alert(g["machine_id"].iloc[0], typ, t0, t1, msg, ev, rule, sev, origins[0] if len(origins) == 1 else "mixed", True, exp))


def _skip(res, mid, rule, why):
    res.skipped_rules.append({"machine_id": mid, "rule": rule, "reason": why})


def _detect_machine(g: pd.DataFrame, cfg: AnomalyConfig, res: AnomalyResult):
    mid = g["machine_id"].iloc[0]
    if len(g) < 3:
        return
    t = (g["ts"] - g["ts"].iloc[0]).dt.total_seconds().to_numpy()
    dt_med = median_dt_s(g["ts"])
    hole = 2 * dt_med if np.isfinite(dt_med) else 2.0
    st = g["state"].to_numpy()
    working = st == "working"
    speed = g["speed_ms"].to_numpy()
    flow = g["flow_lpm"].to_numpy()
    press = g["pressure_bar"].to_numpy()
    width = g["working_width_m"].to_numpy()
    rate = g["application_rate_lha"].to_numpy() if cfg.target_rate_lha is None else np.full(len(g), cfg.target_rate_lha)

    # --- desvio de vazão vs. esperado (precisa flow, speed, width e taxa) ---
    need = {"flow_lpm": has_sensor(g, "flow_lpm", cfg), "speed_ms": has_sensor(g, "speed_ms", cfg),
            "working_width_m": has_sensor(g, "working_width_m", cfg),
            "application_rate_lha": cfg.target_rate_lha is not None or has_sensor(g, "application_rate_lha", cfg)}
    if all(need.values()):
        with np.errstate(invalid="ignore", divide="ignore"):
            exp = rate * speed * 3.6 * width / 600.0
            dev = (flow - exp) / exp * 100
        ok = working & (speed >= cfg.min_working_speed_ms) & np.isfinite(dev)
        bad = ok & (np.abs(dev) > cfg.flow_tol_pct)
        for a, b in runs(bad, t, cfg.min_duration_s, hole):
            seg = slice(a, b + 1)
            md = float(np.nanmedian(dev[seg]))
            _mk(res, g, cfg, a, b, "flow_deviation",
                f"Vazão medida {abs(md):.0f}% {'abaixo' if md < 0 else 'acima'} do esperado para a taxa, velocidade e largura reportadas "
                f"(tolerância ±{cfg.flow_tol_pct:.0f}%). A causa não é identificada pelo sistema; requer verificação humana.",
                {"median_deviation_pct": md, "median_flow_lpm": float(np.nanmedian(flow[seg])),
                 "median_expected_flow_lpm": float(np.nanmedian(exp[seg])), "n_samples": int(b - a + 1),
                 "tolerance_pct": cfg.flow_tol_pct}, "flow_vs_expected(rate*speed*width/600)", "warning")
    else:
        _skip(res, mid, "flow_deviation", f"faltam dados: {[k for k, v in need.items() if not v]}")

    # --- pressão ---
    if has_sensor(g, "pressure_bar", cfg):
        okp = working & np.isfinite(press)
        if cfg.pressure_band_bar:
            lo, hi = cfg.pressure_band_bar
            bad = okp & ((press < lo) | (press > hi))
            for a, b in runs(bad, t, cfg.min_duration_s, hole):
                _mk(res, g, cfg, a, b, "pressure_out_of_band",
                    f"Pressão global fora da faixa configurada [{lo}, {hi}] bar. {PRESSURE_NOTE}",
                    {"median_bar": float(np.nanmedian(press[a:b + 1])), "band_bar": [lo, hi], "n_samples": int(b - a + 1)},
                    "pressure_band", "warning")
        if okp.sum() >= 30:
            z = np.full(len(g), np.nan)
            z[okp] = _robust_z(press[okp])
            bad = okp & (np.abs(z) > cfg.pressure_z)
            for a, b in runs(bad, t, cfg.min_duration_s, hole):
                _mk(res, g, cfg, a, b, "pressure_outlier",
                    f"Pressão global atípica em relação ao comportamento da própria máquina (|z robusto| > {cfg.pressure_z}). {PRESSURE_NOTE}",
                    {"median_bar": float(np.nanmedian(press[a:b + 1])), "machine_working_median_bar": float(np.nanmedian(press[okp])),
                     "median_abs_z": float(np.nanmedian(np.abs(z[a:b + 1]))), "n_samples": int(b - a + 1)}, "robust_z_mad")
    else:
        _skip(res, mid, "pressure_*", "sensor de pressão ausente ou quase todo nulo")

    # --- velocidade ---
    if has_sensor(g, "speed_ms", cfg):
        okv = working & np.isfinite(speed) & (speed >= cfg.min_working_speed_ms)
        if cfg.speed_band_ms:
            lo, hi = cfg.speed_band_ms
            bad = okv & ((speed < lo) | (speed > hi))
            rule = "speed_band"
        elif okv.sum() >= 30:
            med = float(np.nanmedian(speed[okv]))
            bad = okv & (np.abs(speed - med) / med > cfg.speed_rel_dev)
            rule = f"speed_vs_own_median(±{cfg.speed_rel_dev:.0%})"
        else:
            bad, rule = np.zeros(len(g), bool), ""
        for a, b in runs(bad, t, cfg.min_duration_s, hole):
            _mk(res, g, cfg, a, b, "speed_out_of_range",
                "Velocidade de trabalho fora do esperado para esta máquina.",
                {"median_speed_ms": float(np.nanmedian(speed[a:b + 1])), "n_samples": int(b - a + 1)}, rule)
        # parada em estado de trabalho / aplicação parada
        stopped = np.isfinite(speed) & (speed < cfg.stopped_speed_ms)
        for a, b in runs(working & stopped, t, cfg.min_duration_s, hole):
            _mk(res, g, cfg, a, b, "unexpected_stop", "Máquina em estado 'working' com velocidade ~0.",
                {"n_samples": int(b - a + 1)}, "state==working & speed<limite", "warning")
        if has_sensor(g, "flow_lpm", cfg):
            for a, b in runs(stopped & np.isfinite(flow) & (flow > 0.5), t, 10.0, hole):
                _mk(res, g, cfg, a, b, "flow_while_stopped", "Vazão positiva com a máquina parada (possível aplicação concentrada).",
                    {"median_flow_lpm": float(np.nanmedian(flow[a:b + 1]))}, "flow>0 & speed~0", "warning")
    else:
        _skip(res, mid, "speed_*", "sensor de velocidade ausente")

    # --- falhas reportadas ---
    codes = g["error_codes"].to_numpy()
    has_code = np.array([bool(c) for c in codes])
    faulty = (st == "fault") | has_code
    for a, b in runs(faulty, t, 0.0, hole):
        cs = sorted({c for lst in codes[a:b + 1] for c in (lst or [])})
        _mk(res, g, cfg, a, b, "fault_stop", f"Máquina reportou falha/parada (códigos: {', '.join(cs) or 'nenhum'}). Significado dos códigos depende do fabricante; não interpretado.",
            {"error_codes": cs, "n_samples": int(b - a + 1)}, "state==fault | error_codes", "warning")

    # --- GNSS ---
    nopos = g[["lat", "lon"]].isna().any(axis=1).to_numpy() | (g["gnss_fix"].to_numpy() == "none")
    for a, b in runs(nopos, t, cfg.gnss_min_s, hole):
        _mk(res, g, cfg, a, b, "gnss_loss", "Sem posição GNSS válida; cobertura e distância não são calculadas neste trecho (não interpolamos).",
            {"n_samples": int(b - a + 1)}, "no position | fix==none", "warning")
    if cfg.expected_fix:
        degraded = ~nopos & (g["gnss_fix"].to_numpy() != cfg.expected_fix) & (g["gnss_fix"].to_numpy() != "unknown")
        for a, b in runs(degraded, t, cfg.gnss_min_s, hole):
            fx = sorted(set(g["gnss_fix"].iloc[a:b + 1]))
            _mk(res, g, cfg, a, b, "gnss_degraded", f"Qualidade GNSS abaixo da esperada ({cfg.expected_fix}): {fx}.",
                {"fix_types": fx, "median_accuracy_m": float(np.nanmedian(g['accuracy_m'].iloc[a:b + 1])) if g['accuracy_m'].iloc[a:b + 1].notna().any() else None},
                "fix!=expected")
    if cfg.max_accuracy_m is not None and has_sensor(g, "accuracy_m", cfg):
        acc = g["accuracy_m"].to_numpy()
        bad = np.isfinite(acc) & (acc > cfg.max_accuracy_m) & ~nopos
        for a, b in runs(bad, t, cfg.gnss_min_s, hole):
            _mk(res, g, cfg, a, b, "gnss_accuracy_low", f"Precisão horizontal reportada pior que {cfg.max_accuracy_m} m.",
                {"median_accuracy_m": float(np.nanmedian(acc[a:b + 1]))}, "accuracy>limite")

    # --- lacunas de dados ---
    dts = np.diff(t)
    for i in np.flatnonzero(dts > max(cfg.gap_alert_s, 3 * (dt_med if np.isfinite(dt_med) else 0))):
        _mk(res, g, cfg, i, i + 1, "data_gap", f"Sem telemetria por {dts[i]:.0f} s.", {"gap_s": float(dts[i])}, "dt>limite")

    # --- sensor sem dado em trabalho / congelado ---
    for col, nome in (("flow_lpm", "vazão"), ("pressure_bar", "pressão")):
        if g[col].notna().any() and g[col].notna().mean() >= cfg.min_sensor_coverage:
            miss = working & np.isnan(g[col].to_numpy())
            for a, b in runs(miss, t, cfg.min_duration_s, hole):
                _mk(res, g, cfg, a, b, "sensor_missing", f"Sensor de {nome} sem dado durante a operação.",
                    {"sensor": col, "n_samples": int(b - a + 1)}, f"null while working [{col}]", "warning")
            v = g[col].to_numpy()
            same = np.r_[False, (np.diff(v) == 0) & np.isfinite(v[1:]) & np.isfinite(v[:-1])]
            mov = np.isfinite(speed) & (speed > cfg.min_working_speed_ms)
            for a, b in runs(same & working & mov & (v > 0), t, cfg.flatline_s, hole):
                _mk(res, g, cfg, a, b, "sensor_flatline", f"Leitura de {nome} idêntica por {t[b]-t[a]:.0f} s com a máquina em movimento (possível sensor travado).",
                    {"sensor": col, "value": float(v[a])}, f"diff==0 sustained [{col}]", "warning")

    # --- ML multivariado (experimental) ---
    if cfg.use_iforest:
        _iforest(g, cfg, res, t, hole, working)


def _iforest(g, cfg, res, t, hole, working):
    from sklearn.ensemble import IsolationForest
    mid = g["machine_id"].iloc[0]
    cols = ["flow_lpm", "pressure_bar", "speed_ms", "working_width_m"]
    if not all(has_sensor(g, c, cfg) for c in cols):
        _skip(res, mid, "iforest", "requer flow, pressão, velocidade e largura")
        return
    sp = g["speed_ms"].to_numpy(); fl = g["flow_lpm"].to_numpy(); w = g["working_width_m"].to_numpy(); pr = g["pressure_bar"].to_numpy()
    ok = working & (sp >= cfg.min_working_speed_ms) & np.isfinite(fl) & np.isfinite(pr) & np.isfinite(w)
    if ok.sum() < cfg.iforest_min_samples:
        _skip(res, mid, "iforest", f"amostras de trabalho insuficientes ({int(ok.sum())} < {cfg.iforest_min_samples})")
        return
    rate_real = fl[ok] * 600.0 / (sp[ok] * 3.6 * w[ok])   # L/ha realizado
    X = np.column_stack([rate_real, pr[ok], sp[ok]])
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1
    clf = IsolationForest(n_estimators=200, contamination=cfg.iforest_contamination, random_state=cfg.seed).fit((X - mu) / sd)
    flag = np.zeros(len(g), bool)
    flag[np.flatnonzero(ok)] = clf.predict((X - mu) / sd) == -1
    for a, b in runs(flag, t, cfg.min_duration_s, hole * 5):
        seg = ok.copy(); seg[:a] = False; seg[b + 1:] = False
        r = fl[seg] * 600.0 / (sp[seg] * 3.6 * w[seg])
        _mk(res, g, cfg, a, b, "ml_multivariate_outlier",
            "Combinação atípica de taxa realizada (L/ha), pressão e velocidade para esta máquina (IsolationForest, experimental; "
            "pontos 'atípicos' não são necessariamente defeito).",
            {"median_realized_rate_lha": float(np.median(r)), "machine_median_realized_rate_lha": float(np.median(rate_real)),
             "contamination": cfg.iforest_contamination, "n_samples": int(seg.sum())}, "isolation_forest", "info", True)


# --- avaliação contra verdade simulada ---------------------------------------------------------------------------

ACCEPT = {"stop": {"fault_stop", "unexpected_stop"}, "gnss_loss": {"gnss_loss"},
          "abnormal_flow": {"flow_deviation", "ml_multivariate_outlier", "pressure_outlier"}, "missing_data": {"sensor_missing"}}


def evaluate(alerts: list[Alert], truth_events: list[dict], tol_s: float = 30.0) -> dict:
    """Compara alertas com eventos injetados. Só vale para dado SIMULADO com verdade conhecida."""
    rows = []
    used = set()
    for ev in truth_events:
        s, e = pd.Timestamp(ev["start"]), pd.Timestamp(ev["end"])
        hit = [a for a in alerts if a.machine_id == ev["machine_id"] and a.type in ACCEPT[ev["type"]]
               and a.start <= e + pd.Timedelta(seconds=tol_s) and a.end >= s - pd.Timedelta(seconds=tol_s)]
        for a in hit:
            used.add(a.id)
        rows.append({**ev, "detected": bool(hit), "by": sorted({a.type for a in hit}),
                     "delay_s": float(min((a.start - s).total_seconds() for a in hit)) if hit else None})
    extra = [a for a in alerts if a.id not in used]
    return {"events": rows, "recall": float(np.mean([r["detected"] for r in rows])) if rows else None,
            "unmatched_alerts": [{"machine_id": a.machine_id, "type": a.type, "start": a.start.isoformat(), "end": a.end.isoformat()} for a in extra]}
