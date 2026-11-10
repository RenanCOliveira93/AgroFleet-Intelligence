"""Módulo de ROI com premissas explícitas e análise de sensibilidade.

NENHUM valor padrão aqui é uma estimativa de mercado. ``EXAMPLE_INPUTS`` é um cenário ILUSTRATIVO para demonstrar o
cálculo (``is_example=True`` aparece em toda saída). Cada premissa carrega sua origem: "assumed" (chute do usuário),
"user" (informado) ou "measured_telemetry" (vinda das métricas da frota, com a origem simulado/real da telemetria).

Modelo (ver docs/GUIA_TECNICO.md (seção 7.7)):
  custo fixo anual/máquina = (capex − residual)/vida + juros·(capex+residual)/2 + manutenção%·capex
  horas/ano exigidas       = área_ano·aplicações / produtividade_autônoma(ha/h)
  custo autônomo/ha        = (n·fixo + (energia+supervisão)·horas) / (área·aplicações) + produto/ha·(1+sobreposição_auto)
  custo base/ha            = custo_horário_base / produtividade_base + produto/ha·(1+sobreposição_base)
  benefício líquido anual  = (base − autônomo)/ha · área·aplicações
  payback (anos)           = capex_total / benefício líquido anual   (se benefício ≤ 0: indefinido)
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict, fields

import numpy as np


@dataclass
class RoiInputs:
    currency: str = "BRL"
    n_machines: float = 1
    capex_per_machine: float = 0.0
    useful_life_years: float = 8
    residual_pct: float = 20.0
    interest_rate_pct: float = 10.0
    maintenance_pct_of_capex_per_year: float = 5.0
    energy_cost_per_h: float = 0.0
    supervision_cost_per_h: float = 0.0
    area_ha_per_year: float = 0.0
    applications_per_year: float = 1
    product_cost_per_ha: float = 0.0
    auto_productivity_ha_per_h: float = 1.0
    auto_overlap_pct: float = 0.0
    baseline_hourly_cost: float = 0.0
    baseline_productivity_ha_per_h: float = 1.0
    baseline_overlap_pct: float = 0.0
    discount_rate_pct: float = 10.0
    provenance: dict = field(default_factory=dict)   # nome -> {"source": ..., "note": ...}
    is_example: bool = False


# Cenário ILUSTRATIVO (não é benchmark de mercado). Troque todos os valores pelos da sua operação.
EXAMPLE_INPUTS = RoiInputs(
    n_machines=4, capex_per_machine=900_000, useful_life_years=8, residual_pct=20, interest_rate_pct=10,
    maintenance_pct_of_capex_per_year=5, energy_cost_per_h=120, supervision_cost_per_h=40, area_ha_per_year=6000,
    applications_per_year=6, product_cost_per_ha=90, auto_productivity_ha_per_h=30, auto_overlap_pct=2,
    baseline_hourly_cost=450, baseline_productivity_ha_per_h=25, baseline_overlap_pct=8, discount_rate_pct=10,
    provenance={k: {"source": "assumed", "note": "valor ilustrativo; substitua"} for k in
                ["capex_per_machine", "energy_cost_per_h", "supervision_cost_per_h", "area_ha_per_year", "product_cost_per_ha",
                 "baseline_hourly_cost", "auto_productivity_ha_per_h", "baseline_productivity_ha_per_h", "auto_overlap_pct",
                 "baseline_overlap_pct"]},
    is_example=True)


def compute(i: RoiInputs) -> dict:
    treated = i.area_ha_per_year * i.applications_per_year
    if treated <= 0 or i.auto_productivity_ha_per_h <= 0 or i.baseline_productivity_ha_per_h <= 0:
        raise ValueError("área tratada e produtividades devem ser > 0")
    capex = i.capex_per_machine
    resid = capex * i.residual_pct / 100
    fixed_machine = (capex - resid) / i.useful_life_years + (i.interest_rate_pct / 100) * (capex + resid) / 2 \
        + capex * i.maintenance_pct_of_capex_per_year / 100
    hours = treated / i.auto_productivity_ha_per_h
    capacity_h_per_year_per_machine = hours / i.n_machines
    auto_fixed = i.n_machines * fixed_machine
    auto_var = (i.energy_cost_per_h + i.supervision_cost_per_h) * hours
    auto_machine_cost_ha = (auto_fixed + auto_var) / treated
    auto_product_ha = i.product_cost_per_ha * (1 + i.auto_overlap_pct / 100)
    base_machine_cost_ha = i.baseline_hourly_cost / i.baseline_productivity_ha_per_h
    base_product_ha = i.product_cost_per_ha * (1 + i.baseline_overlap_pct / 100)
    auto_ha, base_ha = auto_machine_cost_ha + auto_product_ha, base_machine_cost_ha + base_product_ha
    net = (base_ha - auto_ha) * treated
    capex_total = capex * i.n_machines
    payback = capex_total / net if net > 0 else None
    r = i.discount_rate_pct / 100
    npv = -capex_total + sum((net + (resid * i.n_machines if t == i.useful_life_years else 0)) / (1 + r) ** t
                             for t in range(1, int(round(i.useful_life_years)) + 1))
    return {
        "is_example": i.is_example, "currency": i.currency,
        "treated_ha_per_year": treated, "machine_hours_per_year": hours,
        "hours_per_machine_per_year": capacity_h_per_year_per_machine,
        "autonomous": {"fixed_cost_per_year": auto_fixed, "variable_cost_per_year": auto_var, "machine_cost_per_ha": auto_machine_cost_ha,
                       "product_cost_per_ha": auto_product_ha, "total_cost_per_ha": auto_ha},
        "baseline": {"machine_cost_per_ha": base_machine_cost_ha, "product_cost_per_ha": base_product_ha, "total_cost_per_ha": base_ha},
        "savings": {"product_from_overlap_per_year": (base_product_ha - auto_product_ha) * treated,
                    "machine_cost_per_year": (base_machine_cost_ha - auto_machine_cost_ha) * treated},
        "net_benefit_per_year": net, "capex_total": capex_total, "payback_years": payback,
        "payback_within_useful_life": bool(payback is not None and payback <= i.useful_life_years),
        "npv_over_useful_life": npv,
        "assumptions": {k: v for k, v in asdict(i).items() if k not in ("provenance",)},
        "provenance": i.provenance,
        "caveats": ["Resultado depende integralmente das premissas informadas.",
                    "Não inclui impostos, financiamento, custo de falha/parada, benefícios agronômicos (produtividade da cultura) nem risco regulatório.",
                    "Produtividade e sobreposição só são 'medidas' se a origem for measured_telemetry com telemetria real."]}


def sensitivity(i: RoiInputs, pct: float = 20.0) -> dict:
    """Tornado one-at-a-time (cada premissa ±pct%, demais fixas) sobre benefício líquido anual e payback."""
    base = compute(i)
    rows = []
    for name in ["capex_per_machine", "useful_life_years", "residual_pct", "interest_rate_pct", "maintenance_pct_of_capex_per_year",
                 "energy_cost_per_h", "supervision_cost_per_h", "area_ha_per_year", "applications_per_year", "product_cost_per_ha",
                 "auto_productivity_ha_per_h", "auto_overlap_pct", "baseline_hourly_cost", "baseline_productivity_ha_per_h",
                 "baseline_overlap_pct", "n_machines"]:
        v0 = getattr(i, name)
        if v0 == 0:
            continue
        out = {}
        for lab, f in (("low", 1 - pct / 100), ("high", 1 + pct / 100)):
            j = copy.deepcopy(i); setattr(j, name, v0 * f)
            try:
                r = compute(j); out[lab] = {"net_benefit_per_year": r["net_benefit_per_year"], "payback_years": r["payback_years"]}
            except ValueError:
                out[lab] = {"net_benefit_per_year": None, "payback_years": None}
        swing = abs((out["high"]["net_benefit_per_year"] or 0) - (out["low"]["net_benefit_per_year"] or 0))
        rows.append({"input": name, "value": v0, "low": out["low"], "high": out["high"], "swing_net_benefit": swing})
    rows.sort(key=lambda r: -r["swing_net_benefit"])
    return {"pct": pct, "base_net_benefit_per_year": base["net_benefit_per_year"], "base_payback_years": base["payback_years"], "tornado": rows}


def grid(i: RoiInputs, x: str, y: str, xs: list[float], ys: list[float]) -> dict:
    """Benefício líquido anual numa grade 2-D de duas premissas (valores absolutos)."""
    z = []
    for yv in ys:
        row = []
        for xv in xs:
            j = copy.deepcopy(i); setattr(j, x, xv); setattr(j, y, yv)
            try:
                row.append(compute(j)["net_benefit_per_year"])
            except ValueError:
                row.append(None)
        z.append(row)
    return {"x": x, "y": y, "xs": xs, "ys": ys, "net_benefit_per_year": z}


def break_even(i: RoiInputs, name: str, lo: float, hi: float, tol: float = 1e-6) -> float | None:
    """Valor da premissa ``name`` em que o benefício líquido anual é zero (bissecção); None se não houver mudança de sinal."""
    def f(v):
        j = copy.deepcopy(i); setattr(j, name, v); return compute(j)["net_benefit_per_year"]
    a, b = f(lo), f(hi)
    if a is None or b is None or a * b > 0:
        return None
    for _ in range(100):
        m = (lo + hi) / 2
        fm = f(m)
        if abs(fm) < tol:
            return m
        if a * fm < 0:
            hi = m
        else:
            lo, a = m, fm
    return (lo + hi) / 2


def operational_cost_per_ha(metrics: dict, hourly_cost: float, product_cost_per_l: float | None = None) -> dict:
    """Custo operacional/ha de uma janela de telemetria: horas engajadas × custo horário (+ produto aplicado se preço dado)
    dividido pela área ÚNICA coberta. Retorna None quando falta área ou horas (nunca 0 inventado)."""
    cov = metrics.get("coverage") or {}
    unique = cov.get("unique_ha")
    th = metrics.get("time_h") or {}
    eng = sum(th.get(s, 0) for s in ("working", "transport", "idle", "refilling", "fault"))
    if not unique or eng <= 0:
        return {"cost_per_ha": None, "reason": "sem área única ou sem horas"}
    machine = hourly_cost * eng
    vol = metrics.get("volume_from_flow_l") if metrics.get("volume_from_flow_l") is not None else metrics.get("volume_from_counter_l")
    prod = product_cost_per_l * vol if product_cost_per_l is not None and vol is not None else None
    return {"cost_per_ha": (machine + (prod or 0)) / unique, "machine_cost_per_ha": machine / unique,
            "product_cost_per_ha": prod / unique if prod is not None else None, "engaged_h": eng, "unique_ha": unique}
