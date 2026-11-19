"""Autonomia e viabilidade de conclusão (Etapa 4). Cálculo puro: DataFrame + cadastro + tempo restante → resultado.

Definições (unidade U = L para diesel, kWh para elétrico):
- nível atual      = mediana das leituras válidas dos últimos ``level_window_s`` (filtra a quantização e o ruído do
                     sensor). Diesel: % (SPN 96) × capacidade cadastrada. Elétrico: ``battery_energy_kwh`` quando
                     reportado; senão SOC × capacidade cadastrada (a fonte usada é informada).
- reserva          = ``energy_reserve_pct`` × capacidade. Não conta como disponível para a missão.
- utilizável       = max(nível − reserva, 0).
- consumo (taxa)   = média ponderada pelo tempo de ``fuel_rate_lph`` (SPN 183) ou ``battery_power_kw`` na janela
                     ``rate_window_s``, em tempo de relógio (inclui manobras e paradas, na mesma base do ha/h efetivo
                     que gera o tempo restante). Sem o sensor de taxa: inclinação do nível na janela (``rate_source``
                     = ``level_slope``), mais grosseira e sinalizada.
- autonomia        = utilizável / taxa.
- necessidade      = taxa × tempo restante (central e pessimista, vindos da missão).
- margem           = (utilizável − necessidade) / necessidade.

Viabilidade (``status``):
- green  (Viável)            margem central >= ``autonomy_margin_pct`` E margem no pior caso >= 0
- yellow (Margem apertada)   margem central >= 0, mas abaixo da exigida, ou pior caso negativo/indeterminado
- red    (Insuficiente)      margem central < 0: na taxa atual, a energia utilizável acaba antes da conclusão
- gray   (Indeterminado)     falta entrada (cadastro, leitura, taxa, tempo restante) ou o dado está desatualizado;
                             ``reasons`` diz qual. Nunca se completa a entrada com um valor presumido.

O nível de energia (``level_class``: normal/low/critical) é OUTRA coisa: compara o nível atual com os limites de
alerta do cadastro, independentemente da missão. Histerese e geração de alertas ficam na Etapa 5.

São estimativas por extrapolação da taxa recente, não previsões validadas: a taxa futura pode mudar com solo,
relevo, carga e operador. O sistema só monitora; nada aqui comanda a máquina.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from ..registry import MachineProfile
from .frame import median_dt_s

STATUS_LABEL = {"green": "Viável", "yellow": "Margem apertada", "red": "Insuficiente", "gray": "Indeterminado"}
LEVEL_LABEL = {"normal": "Normal", "low": "Baixo", "critical": "Crítico", "not_configured": "Limites não cadastrados",
               "unknown": "Sem leitura"}

SOURCES = {
    "diesel": dict(level="fuel_level_pct", amount=None, rate="fuel_rate_lph", q_level="q_fuel_level",
                   q_amount=None, q_rate="q_fuel_rate", unit="L", rate_unit="L/h"),
    "electric": dict(level="battery_soc_pct", amount="battery_energy_kwh", rate="battery_power_kw", q_level="q_battery",
                     q_amount="q_battery", q_rate="q_battery", unit="kWh", rate_unit="kW"),
}


@dataclass
class AutonomyConfig:
    level_window_s: float = 300.0       # janela da mediana do nível atual
    rate_window_s: float = 3600.0       # janela da taxa de consumo
    min_rate_cover_s: float = 900.0     # tempo mínimo com leitura válida na janela para aceitar a taxa
    refill_jump_pct: float = 3.0        # subida de nível acima disso = abastecimento/recarga (a janela recomeça depois)
    max_gap_s: float = 10.0
    stale_after_s: float = 300.0        # dado mais velho que isso → status cinza


@dataclass
class Remaining:
    """Tempo restante da missão em horas (da Etapa 4, ``mission``). ``pessimistic_h`` None = indeterminado."""
    central_h: float | None
    pessimistic_h: float | None = None
    optimistic_h: float | None = None


def reason(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def sample_weights(ts: pd.Series, max_gap_s: float = 10.0) -> np.ndarray:
    """Peso (s) de cada amostra = intervalo até a próxima, se <= limite de lacuna; senão 0 (lacuna não é atribuída)."""
    if len(ts) < 2:
        return np.zeros(len(ts))
    dt = ts.diff().shift(-1).dt.total_seconds().to_numpy()
    thr = max(max_gap_s, 3 * (median_dt_s(ts) if len(ts) > 2 else 0) or 0)
    w = np.where((dt > 0) & (dt <= thr), dt, 0.0)
    w[-1] = 0.0
    return np.nan_to_num(w)


def _valid(g: pd.DataFrame, col: str, qcol: str | None) -> np.ndarray:
    v = g[col].notna().to_numpy().copy() if col in g else np.zeros(len(g), bool)
    if qcol and qcol in g:
        v &= (g[qcol].astype(object).to_numpy() != "failed")
    return v


def time_weighted_mean(g: pd.DataFrame, col: str, qcol: str | None = None, max_gap_s: float = 10.0) -> tuple[float | None, float]:
    """(média ponderada pelo tempo, segundos cobertos por leituras válidas). Amostras inválidas não entram."""
    if col not in g or not len(g):
        return None, 0.0
    w = sample_weights(g["ts"], max_gap_s)
    ok = _valid(g, col, qcol) & (w > 0)
    cover = float(w[ok].sum())
    if cover <= 0:
        return None, 0.0
    return float(np.sum(g[col].to_numpy()[ok] * w[ok]) / cover), cover


@dataclass
class AutonomyResult:
    machine_id: str
    energy_source: str
    unit: str | None
    rate_unit: str | None
    status: str
    status_label: str
    reasons: list = field(default_factory=list)       # por que cinza / o que limita a confiança
    capacity: float | None = None
    level_pct: float | None = None
    level_amount: float | None = None
    level_source: str | None = None
    level_ts: str | None = None
    reserve_pct: float | None = None
    reserve_amount: float | None = None
    usable_amount: float | None = None
    rate: float | None = None
    rate_source: str | None = None
    rate_window_h: float | None = None
    rate_cross_check_pct: float | None = None         # (taxa do sensor − taxa pela inclinação) / inclinação
    autonomy_h: float | None = None
    remaining_h: float | None = None
    remaining_pessimistic_h: float | None = None
    needed_amount: float | None = None
    needed_pessimistic_amount: float | None = None
    margin_pct: float | None = None
    margin_pessimistic_pct: float | None = None
    required_margin_pct: float | None = None
    projected_end_amount: float | None = None
    projected_end_pct: float | None = None
    level_class: str = "unknown"
    level_class_label: str = LEVEL_LABEL["unknown"]
    warning_pct: float | None = None
    critical_pct: float | None = None
    basis: str = "Extrapolação da taxa recente; estimativa, não previsão validada."

    def to_dict(self) -> dict:
        return asdict(self)


def _slope_rate(t_h: np.ndarray, amount: np.ndarray) -> float | None:
    if len(t_h) < 3 or np.ptp(t_h) <= 0:
        return None
    b = np.polyfit(t_h, amount, 1)[0]
    return float(-b)


def level_class(level_pct: float | None, prof: MachineProfile) -> str:
    if level_pct is None:
        return "unknown"
    w, c = prof.energy_warning_pct, prof.energy_critical_pct
    if w is None and c is None:
        return "not_configured"
    if c is not None and level_pct <= c:
        return "critical"
    if w is not None and level_pct <= w:
        return "low"
    return "normal"


def autonomy(df: pd.DataFrame, prof: MachineProfile, remaining: Remaining | None,
             cfg: AutonomyConfig | None = None, data_age_s: float | None = None) -> AutonomyResult:
    """Autonomia de UMA máquina. ``df``: amostras da máquina no período da operação (qualquer ordem)."""
    cfg = cfg or AutonomyConfig()
    src = prof.energy_source
    spec = SOURCES.get(src)
    cap, unit = prof.energy_capacity
    res = AutonomyResult(prof.machine_id, src, unit, spec["rate_unit"] if spec else None, "gray", STATUS_LABEL["gray"],
                         capacity=cap, reserve_pct=prof.energy_reserve_pct, required_margin_pct=prof.autonomy_margin_pct,
                         warning_pct=prof.energy_warning_pct, critical_pct=prof.energy_critical_pct)
    R = res.reasons
    if spec is None:
        R.append(reason("energy_source_unknown", "Fonte de energia não cadastrada (diesel/elétrico)."))
        return res
    g = df[df["machine_id"] == prof.machine_id].sort_values("ts").reset_index(drop=True) if len(df) else df
    if not len(g):
        R.append(reason("no_data", "Sem telemetria da máquina no período da operação."))
        return res
    last_ts = g["ts"].iloc[-1]

    # ---- nível atual -----------------------------------------------------------------------------------------
    lv_ok = _valid(g, spec["level"], spec["q_level"])
    am_ok = _valid(g, spec["amount"], spec["q_amount"]) if spec["amount"] else np.zeros(len(g), bool)
    any_ok = lv_ok | am_ok
    if any_ok.any():
        t_last = g["ts"][any_ok].iloc[-1]
        recent = (g["ts"] >= t_last - pd.Timedelta(seconds=cfg.level_window_s)).to_numpy()
        res.level_ts = t_last.isoformat()
        if (last_ts - t_last).total_seconds() > cfg.level_window_s:
            R.append(reason("level_not_recent", "Sensor de nível sem leitura recente; o último valor é antigo."))
        if am_ok.any():                                               # elétrico com kWh reportado
            res.level_amount = float(np.median(g[spec["amount"]].to_numpy()[am_ok & recent]))
            res.level_source = spec["amount"]
        if lv_ok.any() and (lv_ok & recent).any():
            res.level_pct = float(np.median(g[spec["level"]].to_numpy()[lv_ok & recent]))
        if res.level_amount is None and res.level_pct is not None and cap is not None:
            res.level_amount = res.level_pct / 100 * cap
            res.level_source = f"{spec['level']} × capacidade cadastrada"
        if res.level_pct is None and res.level_amount is not None and cap is not None:
            res.level_pct = 100 * res.level_amount / cap
    else:
        R.append(reason("no_level_reading", f"Sem leitura válida de {spec['level']}" +
                        (f" nem {spec['amount']}" if spec["amount"] else "") + "."))
    res.level_class = level_class(res.level_pct, prof)
    res.level_class_label = LEVEL_LABEL[res.level_class]
    if cap is None:
        R.append(reason("capacity_not_registered",
                        "Capacidade do tanque não cadastrada." if src == "diesel" else "Capacidade da bateria não cadastrada."))
    if prof.energy_reserve_pct is None:
        R.append(reason("reserve_not_registered", "Reserva operacional não cadastrada."))
    elif cap is not None:
        res.reserve_amount = prof.energy_reserve_pct / 100 * cap
    if res.level_amount is not None and res.reserve_amount is not None:
        res.usable_amount = max(res.level_amount - res.reserve_amount, 0.0)

    # ---- taxa de consumo -------------------------------------------------------------------------------------
    w0 = max(last_ts - pd.Timedelta(seconds=cfg.rate_window_s), g["ts"].iloc[0])
    win = g[g["ts"] >= w0]
    # abastecimento/recarga na janela: recomeça depois do último salto de nível para cima
    lvl = win[spec["level"]] if spec["level"] in win else pd.Series(dtype=float)
    if lvl.notna().sum() > 2:
        sm = lvl.rolling(30, min_periods=1).median()
        jump = sm.diff() > cfg.refill_jump_pct
        if jump.any():
            win = win.loc[jump[jump].index[-1]:]
            R.append(reason("refill_in_window", "Abastecimento ou recarga na janela; a taxa usa só o trecho posterior."))
    res.rate_window_h = float((win["ts"].iloc[-1] - win["ts"].iloc[0]).total_seconds() / 3600) if len(win) else 0.0
    rate_sensor, cover = time_weighted_mean(win, spec["rate"], spec["q_rate"], cfg.max_gap_s)
    rate_slope = None
    if cap is not None or spec["amount"]:
        if spec["amount"] and win[spec["amount"]].notna().sum() > 2:
            m = _valid(win, spec["amount"], spec["q_amount"])
            amt = win[spec["amount"]].to_numpy()[m]
        else:
            m = _valid(win, spec["level"], spec["q_level"])
            amt = win[spec["level"]].to_numpy()[m] / 100 * cap if cap is not None else np.array([])
        th = ((win["ts"][m] - win["ts"].iloc[0]).dt.total_seconds() / 3600).to_numpy() if len(amt) else np.array([])
        if len(th) and np.ptp(th) * 3600 >= cfg.min_rate_cover_s:
            rate_slope = _slope_rate(th, amt)
    if rate_sensor is not None and cover >= cfg.min_rate_cover_s:
        res.rate, res.rate_source = rate_sensor, spec["rate"]
        if rate_slope and rate_slope > 0:
            res.rate_cross_check_pct = 100 * (rate_sensor - rate_slope) / rate_slope
            if abs(res.rate_cross_check_pct) > 25:
                R.append(reason("rate_sources_disagree",
                                f"Taxa do sensor difere {res.rate_cross_check_pct:+.0f}% da queda do nível; verificar sensores."))
    elif rate_slope is not None:
        res.rate, res.rate_source = rate_slope, "level_slope"
        R.append(reason("rate_from_level_slope", "Sem sensor de taxa: taxa estimada pela queda do nível (mais grosseira)."))
    else:
        R.append(reason("rate_unavailable", "Taxa de consumo indisponível: leituras insuficientes na janela."))
    if res.rate is not None and res.rate <= 0:
        R.append(reason("rate_not_positive", "Consumo líquido não positivo na janela (parada, recarga ou regeneração)."))

    if res.rate is not None and res.rate > 0 and res.usable_amount is not None:
        res.autonomy_h = res.usable_amount / res.rate

    # ---- necessidade e margem --------------------------------------------------------------------------------
    if remaining is None or remaining.central_h is None:
        R.append(reason("remaining_unknown", "Tempo restante da missão indeterminado."))
    else:
        res.remaining_h, res.remaining_pessimistic_h = remaining.central_h, remaining.pessimistic_h
        if res.rate is not None and res.rate > 0:
            res.needed_amount = res.rate * remaining.central_h
            if remaining.pessimistic_h is not None:
                res.needed_pessimistic_amount = res.rate * remaining.pessimistic_h
            if res.level_amount is not None:
                res.projected_end_amount = res.level_amount - res.needed_amount
                if cap:
                    res.projected_end_pct = 100 * res.projected_end_amount / cap

            def margin(need):
                if need is None or need <= 0 or res.usable_amount is None:
                    return None
                return 100 * (res.usable_amount - need) / need
            res.margin_pct = margin(res.needed_amount)
            res.margin_pessimistic_pct = margin(res.needed_pessimistic_amount)
    if prof.autonomy_margin_pct is None:
        R.append(reason("margin_not_registered", "Margem de autonomia exigida não cadastrada."))
    if data_age_s is not None and data_age_s > cfg.stale_after_s:
        R.append(reason("stale_data", f"Último dado há {data_age_s / 60:.0f} min; situação atual desconhecida."))

    # ---- status ----------------------------------------------------------------------------------------------
    blocking = {"energy_source_unknown", "no_data", "no_level_reading", "capacity_not_registered", "reserve_not_registered",
                "rate_unavailable", "rate_not_positive", "remaining_unknown", "margin_not_registered", "stale_data",
                "level_not_recent"}
    done = remaining is not None and remaining.central_h == 0
    if done and not any(r["code"] in blocking - {"rate_unavailable", "rate_not_positive"} for r in R):
        res.status = "green"
        R.append(reason("mission_done", "Área planejada coberta; não há necessidade adicional de energia."))
    elif any(r["code"] in blocking for r in R) or res.margin_pct is None:
        res.status = "gray"
    elif res.margin_pct < 0:
        res.status = "red"
    elif res.margin_pct >= prof.autonomy_margin_pct and res.margin_pessimistic_pct is not None and res.margin_pessimistic_pct >= 0:
        res.status = "green"
    else:
        res.status = "yellow"
        if res.margin_pessimistic_pct is None:
            R.append(reason("worst_case_unknown", "Pior caso indeterminado (ritmo mínimo observado nulo ou poucos intervalos)."))
    res.status_label = STATUS_LABEL[res.status]
    return res
