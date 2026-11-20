"""Simulador v2: operações georreferenciadas com modelo de energia (Etapa 3).

TUDO O QUE ESTE MÓDULO PRODUZ É SIMULADO (origin="simulated"). Propriedade, talhões, máquinas e números são fictícios;
nomes como "Fazenda Santa Clara (simulada)" não designam nenhuma propriedade real. Os modelos de consumo são
SINTÉTICOS e NÃO CALIBRADOS com nenhuma máquina: servem para exercitar ingestão, cobertura, autonomia e alertas com
verdade-terreno conhecida, nunca como estimativa de desempenho real.

Cenários
- A: trator diesel em preparo de solo no Talhão 03 (~120 ha). Consumo de combustível varia com a carga do motor,
     que varia com a resistência do solo (campo espacial suave) e com o modo (trabalho, manobra, ocioso).
- B: pulverizador elétrico AUTÔNOMO no Talhão 05 (~40 ha). Potência varia com velocidade, massa de calda e bomba;
     reabastece calda no depósito; ao atingir o SOC de retorno (comportamento simulado da máquina) volta à base,
     deixando a operação incompleta: é o caso de autonomia insuficiente.
- C: trator diesel no Talhão 04 (~60 ha) com FALHAS DE ENLACE aplicadas à entrega das mensagens (perda,
     armazenamento e reenvio, latência com reordenação, duplicação). O que a máquina "mediu" não muda; muda o que chega.

Geometria: o talhão é um polígono convexo irregular, determinístico pela semente, escalado para a área-alvo. As passadas
são retas paralelas (boustrophedon) recortadas ao polígono; a manobra entre passadas é um segmento curto fora de
trabalho. Não há cabeceira trabalhada, então a cobertura planejada fica um pouco abaixo de 100% do talhão (o valor
exato vem na verdade-terreno).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiPoint, Point, Polygon, mapping
from shapely.ops import unary_union

from ..geo import Projector
from ..registry import MachineProfile, OperationPlan, PropertyInfo
from ..schema import Gnss, SensorQ, State, TelemetryPoint
from .fleet import DEFAULT_START, ORIGIN_LAT, ORIGIN_LON

PROPERTY_ID = "PROP-SIM-SC"
PROPERTY_NAME = "Fazenda Santa Clara (simulada)"
PROPERTY_IBGE = "5206206"     # Cristalina-GO, município que contém ORIGIN_LAT/ORIGIN_LON


# ----------------------------------------------------------------------------------------------------------------
# especificações
# ----------------------------------------------------------------------------------------------------------------

@dataclass
class FieldSpec:
    field_id: str
    name: str
    area_ha: float
    center_xy: tuple[float, float]       # metros, relativo à origem local do simulador
    elongation: float = 1.35
    angle_deg: float = 35.0              # orientação do eixo maior (graus a partir do leste, anti-horário)
    seed: int = 0


@dataclass
class DieselModel:
    tank_l: float = 600.0
    fuel_start_pct: float = 95.0
    idle_lph: float = 6.0                # consumo a 0% de carga (sintético)
    full_load_lph: float = 44.0          # consumo a 100% de carga (sintético)
    work_load_pct: float = 74.0
    load_spatial_amp_pct: float = 9.0    # variação de resistência do solo ao longo do talhão
    turn_load_pct: float = 32.0
    idle_load_pct: float = 12.0
    rpm_work: float = 1950.0
    rpm_turn: float = 1700.0
    rpm_idle: float = 850.0
    engine_hours_start: float = 4820.0


@dataclass
class ElectricModel:
    capacity_kwh: float = 60.0
    soc_start_pct: float = 55.0
    base_kw: float = 1.4                 # eletrônica, sensores, computador de bordo
    traction_kw_per_ms: float = 1.6
    mass_kw_per_ms_per_l: float = 0.0015 # efeito da massa de calda na tração
    pump_kw_per_lpm: float = 0.08
    return_soc_pct: float = 12.0         # comportamento SIMULADO do autônomo: abaixo disso volta à base


@dataclass
class SprayModel:
    tank_l: float = 1000.0
    rate_lha: float = 100.0
    refill_threshold_l: float = 60.0
    refill_s: float = 300.0
    base_pressure_bar: float = 2.8


@dataclass
class Scenario:
    key: str
    machine_id: str
    machine_name: str
    kind: Literal["tractor", "sprayer"]
    field: FieldSpec
    operation_id: str
    operation_name: str
    operation_type: Literal["soil_preparation", "spraying"]
    width_m: float
    work_speed_ms: float
    turn_speed_ms: float = 1.4
    transport_speed_ms: float = 3.0
    diesel: DieselModel | None = None
    electric: ElectricModel | None = None
    spray: SprayModel | None = None
    autonomous: bool = False
    operator_id: str | None = "OP-SIM-1"
    profile_limits: dict = field(default_factory=dict)   # vai para o MachineProfile (cadastro)


def scenario_a() -> Scenario:
    return Scenario("A", "SIM-TR-320", "Trator TR-320 (simulado)", "tractor",
                    FieldSpec("SIM-TALHAO-03", "Talhão 03", 120.0, (3500.0, 500.0), 1.35, 35.0, seed=3),
                    "OP-SIM-A", "Preparo de solo — Talhão 03", "soil_preparation",
                    width_m=12.0, work_speed_ms=8.5 / 3.6, transport_speed_ms=3.5, diesel=DieselModel(),
                    profile_limits=dict(fuel_tank_capacity_l=600.0, energy_reserve_pct=10.0, energy_warning_pct=25.0,
                                        energy_critical_pct=12.0, autonomy_margin_pct=15.0,
                                        maintenance_interval_h=250.0, last_maintenance_engine_h=4582.0))


def scenario_b() -> Scenario:
    return Scenario("B", "SIM-PE-07", "Pulverizador elétrico PE-07 (simulado)", "sprayer",
                    FieldSpec("SIM-TALHAO-05", "Talhão 05", 40.0, (3500.0, -800.0), 1.4, -20.0, seed=5),
                    "OP-SIM-B", "Pulverização — Talhão 05", "spraying",
                    width_m=12.0, work_speed_ms=2.0, transport_speed_ms=3.0, electric=ElectricModel(), spray=SprayModel(),
                    autonomous=True, operator_id=None,
                    profile_limits=dict(battery_capacity_kwh=60.0, energy_reserve_pct=10.0, energy_warning_pct=30.0,
                                        energy_critical_pct=15.0, autonomy_margin_pct=15.0))


def scenario_c() -> Scenario:
    return Scenario("C", "SIM-TR-410", "Trator TR-410 (simulado)", "tractor",
                    FieldSpec("SIM-TALHAO-04", "Talhão 04", 60.0, (5300.0, 500.0), 1.25, 60.0, seed=4),
                    "OP-SIM-C", "Preparo de solo — Talhão 04", "soil_preparation",
                    width_m=12.0, work_speed_ms=8.5 / 3.6, transport_speed_ms=3.5,
                    diesel=DieselModel(fuel_start_pct=80.0, engine_hours_start=2310.0),
                    profile_limits=dict(fuel_tank_capacity_l=600.0, energy_reserve_pct=10.0, energy_warning_pct=25.0,
                                        energy_critical_pct=12.0, autonomy_margin_pct=15.0))


SCENARIOS = {"A": scenario_a, "B": scenario_b, "C": scenario_c}


# ----------------------------------------------------------------------------------------------------------------
# geometria
# ----------------------------------------------------------------------------------------------------------------

def make_field(spec: FieldSpec) -> Polygon:
    """Polígono convexo irregular (metros locais), determinístico, com área = ``area_ha``."""
    rng = np.random.default_rng(spec.seed)
    phi = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    a, b = rng.uniform(0, 2 * np.pi, 2)
    r = 1 + 0.10 * np.sin(2 * phi + a) + 0.06 * np.sin(3 * phi + b) + rng.normal(0, 0.025, len(phi))
    poly = MultiPoint(list(zip(r * np.cos(phi) * spec.elongation, r * np.sin(phi)))).convex_hull
    poly = affinity.rotate(poly, spec.angle_deg, origin=(0, 0))
    k = math.sqrt(spec.area_ha * 1e4 / poly.area)
    poly = affinity.scale(poly, k, k, origin=(0, 0))
    return affinity.translate(poly, *spec.center_xy)


def plan_passes(poly: Polygon, width_m: float, pass_angle_deg: float) -> list[tuple[np.ndarray, np.ndarray]]:
    """Passadas paralelas na direção ``pass_angle_deg``, recortadas ao polígono, em boustrophedon."""
    c = poly.centroid
    rot = affinity.rotate(poly, 90.0 - pass_angle_deg, origin=c)    # direção das passadas -> vertical
    minx, miny, maxx, maxy = rot.bounds
    out, k, x = [], 0, minx + width_m / 2
    while x < maxx:
        inter = LineString([(x, miny - 10), (x, maxy + 10)]).intersection(rot)
        segs = [inter] if isinstance(inter, LineString) else [g for g in getattr(inter, "geoms", []) if isinstance(g, LineString)]
        for seg in segs:
            if seg.length < width_m:
                continue
            (x0, y0), (x1, y1) = seg.coords[0], seg.coords[-1]
            a, b = ((x0, min(y0, y1)), (x1, max(y0, y1)))
            if k % 2:
                a, b = b, a
            back = affinity.rotate(LineString([a, b]), -(90.0 - pass_angle_deg), origin=c)
            out.append((np.array(back.coords[0]), np.array(back.coords[-1])))
            k += 1
        x += width_m
    return out


def planned_coverage_ha(poly: Polygon, passes, width_m: float) -> float:
    """Área (ha) do talhão coberta pelas faixas PLANEJADAS (sem ruído): o teto de cobertura deste plano."""
    sw = [LineString([a, b]).buffer(width_m / 2, cap_style="flat") for a, b in passes]
    return unary_union(sw).intersection(poly).area / 1e4


# ----------------------------------------------------------------------------------------------------------------
# simulação
# ----------------------------------------------------------------------------------------------------------------

def _q(v: float, step: float) -> float:
    """Quantização à resolução do sensor (ex.: J1939 SPN 96 tem resolução de 0,4%)."""
    return round(v / step) * step


@dataclass
class SimResult:
    scenario: Scenario
    points: list[TelemetryPoint]
    truth: dict
    field_lonlat: dict
    property: PropertyInfo
    machine: MachineProfile
    operation: OperationPlan


def simulate_operation(sc: Scenario, *, seed: int = 7, start: datetime = DEFAULT_START, dt_s: float = 1.0,
                       max_seconds: float = 16 * 3600) -> SimResult:
    """Simula uma operação. Determinístico para (cenário, seed, start, dt_s, max_seconds)."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, ord(sc.key)]))
    proj = Projector.for_points([ORIGIN_LON], [ORIGIN_LAT])
    _x, _y = proj.fwd([ORIGIN_LON], [ORIGIN_LAT])
    ox, oy = float(_x[0]), float(_y[0])

    poly = make_field(sc.field)
    passes = plan_passes(poly, sc.width_m, sc.field.angle_deg)
    minx, miny, _, _ = poly.bounds
    depot = np.array([minx - 40.0, miny - 40.0])

    tasks: deque = deque([("move", passes[0][0], "transport", sc.transport_speed_ms)])
    for i, (a, b) in enumerate(passes):
        tasks.append(("move", b, "working", sc.work_speed_ms))
        if i + 1 < len(passes):
            tasks.append(("move", passes[i + 1][0], "transport", sc.turn_speed_ms))   # manobra de cabeceira
    tasks.append(("move", depot, "transport", sc.transport_speed_ms))
    tasks.append(("park", 120.0))

    d, e, sp = sc.diesel, sc.electric, sc.spray
    fuel_l = d.tank_l * d.fuel_start_pct / 100 if d else None
    eng_h = d.engine_hours_start if d else None
    energy = e.capacity_kwh * e.soc_start_pct / 100 if e else None
    tank = sp.tank_l if sp else None
    counter = 0.0
    pos = depot.copy()
    speed, t = 0.0, 0.0
    events: list[dict] = []
    fuel_used_work = work_s = 0.0
    energy_used = 0.0
    pts: list[TelemetryPoint] = []
    passes_done = 0
    returned = False
    lim_q = {"gnss": SensorQ.ok}

    def ts(s):
        return start + timedelta(seconds=s)

    while t <= max_seconds and tasks:
        kind = tasks[0][0]
        if kind == "park":
            mode, v_cmd = "idle", 0.0
            tasks[0] = ("park", tasks[0][1] - dt_s)
            if tasks[0][1] <= 0:
                tasks.popleft()
        elif kind == "refill":
            mode, v_cmd = "refilling", 0.0
            tasks[0] = ("refill", tasks[0][1] - dt_s)
            if tasks[0][1] <= 0:
                tasks.popleft()
                tank = sp.tank_l
        else:
            _, target, mode, v_cmd = tasks[0]
            if mode == "working" and sp and tank <= sp.refill_threshold_l:
                tasks.appendleft(("move", pos.copy(), "transport", sc.transport_speed_ms))
                tasks.appendleft(("refill", sp.refill_s))
                tasks.appendleft(("move", depot.copy(), "transport", sc.transport_speed_ms))
                events.append({"type": "spray_refill", "at": ts(t).isoformat()})
                continue
            if e and not returned and energy / e.capacity_kwh * 100 <= e.return_soc_pct:
                returned = True
                tasks.clear()
                tasks.extend([("move", depot.copy(), "transport", sc.transport_speed_ms), ("park", 120.0)])
                events.append({"type": "energy_return_to_base", "at": ts(t).isoformat(),
                               "soc_pct": energy / e.capacity_kwh * 100, "note": "comportamento simulado do autônomo"})
                continue

        speed = 0.8 * speed + 0.2 * (max(v_cmd + rng.normal(0, 0.08), 0.0) if v_cmd > 0 else 0.0)
        prev = pos.copy()
        if kind == "move":
            dvec = target - pos
            dist = float(np.hypot(*dvec))
            step = speed * dt_s
            if dist <= max(step, 1e-9):
                pos = target.copy()
                tasks.popleft()
                if mode == "working":
                    passes_done += 1
            else:
                pos = pos + dvec / dist * step
        moved = pos - prev

        # --- calda (pulverizador) ---
        flow = None
        if sp:
            flow = sp.rate_lha * speed * 3.6 * sc.width_m / 600.0 * (1 + rng.normal(0, 0.02)) if mode == "working" else 0.0
            flow = max(flow, 0.0)
            used = flow * dt_s / 60.0
            tank = max(tank - used, 0.0)
            counter += used

        # --- energia ---
        rpm = load = rate = None
        if d:
            if mode == "working":
                soil = math.sin(pos[0] / 170.0) * math.cos(pos[1] / 230.0)
                load = d.work_load_pct + d.load_spatial_amp_pct * soil + rng.normal(0, 2.0)
                rpm = d.rpm_work - 1.5 * (load - d.work_load_pct) + rng.normal(0, 12)
            elif mode == "transport":
                load = d.turn_load_pct + rng.normal(0, 2.0)
                rpm = d.rpm_turn + rng.normal(0, 15)
            else:
                load = d.idle_load_pct + rng.normal(0, 1.0)
                rpm = d.rpm_idle + rng.normal(0, 8)
            load = float(np.clip(load, 0, 100))
            rate = d.idle_lph + (d.full_load_lph - d.idle_lph) * load / 100.0
            burn = rate * dt_s / 3600.0
            fuel_l -= burn
            eng_h += dt_s / 3600.0
            if mode == "working":
                fuel_used_work += burn
                work_s += dt_s
            if fuel_l <= 0:
                fuel_l = 0.0
                events.append({"type": "fuel_empty", "at": ts(t).isoformat()})
                tasks.clear()
        power = None
        if e:
            liquid = tank if tank is not None else 0.0
            power = e.base_kw + speed * (e.traction_kw_per_ms + e.mass_kw_per_ms_per_l * liquid) + e.pump_kw_per_lpm * (flow or 0.0)
            power = max(power + rng.normal(0, 0.25), 0.0)
            used_kwh = power * dt_s / 3600.0
            energy = max(energy - used_kwh, 0.0)
            energy_used += used_kwh
            if mode == "working":
                work_s += dt_s

        # --- GNSS, rumo ---
        nx, ny = rng.normal(0, 0.02, 2)
        lo, la = proj.inv([ox + pos[0] + nx], [oy + pos[1] + ny])
        heading = None
        if speed > 0.3 and np.hypot(*moved) > 1e-6:
            heading = (math.degrees(math.atan2(moved[0], moved[1])) + rng.normal(0, 0.8)) % 360.0

        q = dict(lim_q)
        kw: dict = {}
        if d:
            moving = speed > 0.3
            lvl = fuel_l / d.tank_l * 100 + (rng.normal(0, 0.35) if moving else 0.0)   # oscilação de combustível no tanque
            kw.update(engine_rpm=float(round(max(rpm, 0))), engine_load_pct=float(round(load)),
                      engine_hours_h=float(_q(eng_h, 0.05)), fuel_level_pct=float(np.clip(_q(lvl, 0.4), 0, 100)),
                      fuel_rate_lph=float(_q(max(rate * (1 + rng.normal(0, 0.02)), 0), 0.05)))
            q.update(engine=SensorQ.ok, fuel_level=SensorQ.ok, fuel_rate=SensorQ.ok)
        if e:
            kw.update(battery_soc_pct=float(_q(energy / e.capacity_kwh * 100, 0.5)), battery_energy_kwh=float(_q(energy, 0.1)),
                      battery_power_kw=float(_q(power, 0.1)))
            q["battery"] = SensorQ.ok
        if sp:
            press = (sp.base_pressure_bar * (0.4 + 0.6 * min(flow / (sp.rate_lha * sc.work_speed_ms * 3.6 * sc.width_m / 600.0), 1.3))
                     + rng.normal(0, 0.04)) if mode == "working" else 0.2 + abs(rng.normal(0, 0.02))
            kw.update(flow_lpm=float(flow), pressure_bar=float(max(press, 0.0)), tank_level_l=float(tank), consumption_l=float(counter),
                      application_rate_lha=sp.rate_lha if mode == "working" else None)
            q.update(flow=SensorQ.ok, pressure=SensorQ.ok, tank_level=SensorQ.ok)
        if heading is not None:
            kw["heading_deg"] = float(heading)
            q["heading"] = SensorQ.ok
        st = {"working": State.working, "transport": State.transport, "refilling": State.refilling, "idle": State.idle}[mode]
        if d and fuel_l <= 0:
            st = State.off
        pts.append(TelemetryPoint(
            machine_id=sc.machine_id, ts=ts(t), origin="simulated", lat=float(la[0]), lon=float(lo[0]),
            speed_ms=float(max(speed + rng.normal(0, 0.02), 0.0)), state=st, gnss_fix=Gnss.rtk_fixed, accuracy_m=0.02,
            working_width_m=sc.width_m, sensor_quality=q, property_id=PROPERTY_ID, field_id=sc.field.field_id,
            operator_id=sc.operator_id, **kw))
        t += dt_s

    completed = passes_done >= len(passes) and not returned
    field_ll = mapping(_to_lonlat(poly, proj, ox, oy))
    truth = {
        "scenario": sc.key, "origin": "simulated", "seed": seed, "start": start.isoformat(), "dt_s": dt_s,
        "note": "Valores e eventos simulados; modelo de energia sintético e não calibrado.",
        "field_id": sc.field.field_id, "field_ha": poly.area / 1e4, "passes_planned": len(passes), "passes_done": passes_done,
        "planned_coverage_in_field_ha": planned_coverage_ha(poly, passes, sc.width_m),
        "executed_seconds": t, "completed": completed, "events": events,
    }
    if d:
        truth["fuel"] = {"start_l": d.tank_l * d.fuel_start_pct / 100, "end_l": fuel_l,
                         "consumed_l": d.tank_l * d.fuel_start_pct / 100 - fuel_l,
                         "mean_working_rate_lph": fuel_used_work / (work_s / 3600) if work_s else None}
    if e:
        truth["energy"] = {"start_kwh": e.capacity_kwh * e.soc_start_pct / 100, "end_kwh": energy, "consumed_kwh": energy_used,
                           "returned_to_base": returned}

    # a origem fictícia (-16,70; -47,60) cai em Cristalina-GO (malha municipal do IBGE): o clima consultado é o REAL
    # desse ponto, não de um talhão real
    prop = PropertyInfo(property_id=PROPERTY_ID, name=PROPERTY_NAME, origin="simulated", ibge_municipality=PROPERTY_IBGE)
    machine = MachineProfile(machine_id=sc.machine_id, name=sc.machine_name, kind=sc.kind,
                             energy_source="diesel" if d else "electric", autonomous=sc.autonomous,
                             property_id=PROPERTY_ID, origin="simulated", **sc.profile_limits)
    op = OperationPlan(operation_id=sc.operation_id, name=sc.operation_name, operation_type=sc.operation_type,
                       machine_id=sc.machine_id, field_id=sc.field.field_id, property_id=PROPERTY_ID,
                       implement_width_m=sc.width_m, start=start, status="completed" if completed else "active",
                       end=ts(t) if completed else None, origin="simulated")
    return SimResult(sc, pts, truth, field_ll, prop, machine, op)


def _to_lonlat(poly: Polygon, proj: Projector, ox: float, oy: float) -> Polygon:
    xs, ys = poly.exterior.coords.xy
    lon, lat = proj.inv([ox + x for x in xs], [oy + y for y in ys])
    return Polygon([(float(a), float(b)) for a, b in zip(lon, lat)])


# ----------------------------------------------------------------------------------------------------------------
# falhas de enlace (cenário C)
# ----------------------------------------------------------------------------------------------------------------

FaultKind = Literal["outage_lost", "outage_buffered", "latency", "duplicate"]


@dataclass
class LinkFault:
    kind: FaultKind
    start_s: float
    end_s: float
    latency_s: float = 0.0
    dup_prob: float = 0.0


def default_link_faults() -> list[LinkFault]:
    return [LinkFault("outage_lost", 1200, 1290),                     # 90 s sem sinal; dados perdidos
            LinkFault("outage_buffered", 2100, 2340),                 # 4 min sem sinal; dispositivo guarda e reenvia
            LinkFault("latency", 3000, 3300, latency_s=25.0),         # rede lenta: chega atrasado e fora de ordem
            LinkFault("duplicate", 3600, 4200, dup_prob=0.05)]        # reentrega QoS 1


@dataclass
class Delivery:
    deliver_s: float          # instante de ENTREGA relativo ao 1º ponto (a amostra mantém o seu ts de medição)
    point: TelemetryPoint
    duplicate: bool = False


def apply_link_faults(points: list[TelemetryPoint], faults: list[LinkFault], seed: int = 7) -> tuple[list[Delivery], list[dict]]:
    """Agenda de entrega com falhas. Não altera nenhuma amostra: só se, quando e quantas vezes ela chega."""
    if not points:
        return [], []
    rng = np.random.default_rng(seed)
    t0 = points[0].ts
    out: list[Delivery] = []
    for i, p in enumerate(points):
        s = (p.ts - t0).total_seconds()
        f = next((f for f in faults if f.start_s <= s < f.end_s), None)
        if f is None:
            out.append(Delivery(s, p))
        elif f.kind == "outage_lost":
            continue
        elif f.kind == "outage_buffered":
            out.append(Delivery(f.end_s + 1e-3 * (s - f.start_s), p))
        elif f.kind == "latency":
            out.append(Delivery(s + f.latency_s, p))
        elif f.kind == "duplicate":
            out.append(Delivery(s, p))
            if rng.random() < f.dup_prob:
                out.append(Delivery(s + 1.5, p, duplicate=True))
    out.sort(key=lambda d: d.deliver_s)
    ev = [{"type": f"link_{f.kind}", "start": (t0 + timedelta(seconds=f.start_s)).isoformat(),
           "end": (t0 + timedelta(seconds=f.end_s)).isoformat(), "origin": "simulated",
           **({"latency_s": f.latency_s} if f.kind == "latency" else {}), **({"dup_prob": f.dup_prob} if f.kind == "duplicate" else {})}
          for f in faults]
    return out, ev


def run_scenarios(keys=("A", "B"), *, seed: int = 7, start: datetime = DEFAULT_START, dt_s: float = 1.0,
                  max_seconds: float = 16 * 3600) -> list[SimResult]:
    out = []
    for k in keys:
        if k not in SCENARIOS:
            raise ValueError(f"cenário desconhecido: {k!r} (use {sorted(SCENARIOS)})")
        r = simulate_operation(SCENARIOS[k](), seed=seed, start=start, dt_s=dt_s, max_seconds=max_seconds)
        if k == "C":
            deliveries, ev = apply_link_faults(r.points, default_link_faults(), seed)
            r.truth["link_events"] = ev
            r.truth["deliveries"] = len(deliveries)
            r.truth["delivered_unique"] = len({id(dv.point) for dv in deliveries})
            r.truth["_deliveries"] = deliveries       # uso interno (publicador); não serializar
        out.append(r)
    return out
