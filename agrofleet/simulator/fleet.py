"""Simulador reprodutível de uma frota de 4 pulverizadores terrestres autônomos.

TUDO O QUE ESTE MÓDULO PRODUZ É SIMULADO (origin="simulated"). Local, talhões, códigos de erro e números são fictícios;
não representam nenhuma propriedade, máquina ou fabricante reais. O objetivo é dar entrada controlada (com verdade
conhecida) para testar ingestão, geometria, métricas e detecção de anomalias.

Modelo (cada máquina, 1 talhão retangular, passadas paralelas em boustrophedon):
- passada de comprimento L com espaçamento ``spacing`` entre passadas e barra de largura ``width`` (spacing <= width
  gera sobreposição conhecida analiticamente: 1 - spacing/width);
- velocidade de trabalho ~ N(v, 0.15) m/s suavizada; vazão = taxa * v(km/h) * width / 600 (+ ruído); pressão ~ base + ruído;
- tanque finito: ao esvaziar até o limite vai ao depósito (transport), reabastece (refilling) e volta;
- ruído GNSS conforme o tipo de fix (rtk_fixed ~ 2 cm, rtk_float ~ 0,3 m, single ~ 1,5 m).
Cenários injetados (registrados na verdade-terreno): normal, stop, gnss_loss, abnormal_flow, missing_data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from ..geo import Projector
from ..schema import Gnss, SensorQ, State, TelemetryPoint

ORIGIN_LAT, ORIGIN_LON = -16.70, -47.60  # ponto arbitrário (fictício) no Brasil central
DEFAULT_START = datetime(2026, 3, 10, 10, 0, 0, tzinfo=timezone.utc)


@dataclass
class MachineSpec:
    machine_id: str
    scenario: str                  # normal | stop | gnss_loss | abnormal_flow
    field_id: str = ""
    operator_id: str = ""
    property_id: str = "PROP-SIM-1"
    origin_xy: tuple[float, float] = (0.0, 0.0)   # canto SW do talhão (m, relativo à origem local)
    field_w: float = 400.0         # leste-oeste (m): direção do espaçamento
    field_l: float = 500.0         # norte-sul (m): comprimento da passada
    width_m: float = 24.0
    spacing_m: float = 22.0
    speed_ms: float = 5.0
    rate_lha: float = 100.0
    tank_l: float = 1200.0
    refill_threshold_l: float = 120.0
    depot_xy: tuple[float, float] = (-60.0, -60.0)
    base_pressure_bar: float = 3.0


def default_fleet() -> list[MachineSpec]:
    return [
        MachineSpec("SIM-01", "normal", "TALHAO-A", "OP-1", origin_xy=(0, 0), spacing_m=22.0),
        MachineSpec("SIM-02", "stop", "TALHAO-B", "OP-1", origin_xy=(500, 0), spacing_m=23.0),
        MachineSpec("SIM-03", "gnss_loss", "TALHAO-C", "OP-2", origin_xy=(0, 600), spacing_m=24.0),
        MachineSpec("SIM-04", "abnormal_flow", "TALHAO-D", "OP-2", origin_xy=(500, 600), spacing_m=22.0),
    ]


@dataclass
class Truth:
    """Verdade-terreno do simulador (nunca visível à detecção; só para avaliação)."""
    events: list[dict] = field(default_factory=list)       # {machine_id, type, start, end}
    fields: dict = field(default_factory=dict)             # field_id -> polygon GeoJSON (lon/lat)
    areas: dict = field(default_factory=dict)              # machine_id -> {planned_unique_ha, gross_ha, overlap_pct, ...}
    meta: dict = field(default_factory=dict)


def _path(spec: MachineSpec):
    """Waypoints do trabalho: lista de (x0,y0,x1,y1,working)."""
    x0, y0 = spec.origin_xy
    n_pass = int(np.ceil((spec.field_w - spec.width_m) / spec.spacing_m)) + 1
    legs = []
    for k in range(n_pass):
        x = x0 + spec.width_m / 2 + k * spec.spacing_m
        a, b = (y0, y0 + spec.field_l) if k % 2 == 0 else (y0 + spec.field_l, y0)
        legs.append((x, a, x, b, True))
    return legs, n_pass


def simulate(specs: list[MachineSpec] | None = None, seed: int = 7, start: datetime = DEFAULT_START,
             dt_s: float = 1.0, max_seconds: int = 3 * 3600):
    """Retorna (pontos ordenados por (ts, máquina), Truth). Determinístico para (specs, seed, start, dt_s)."""
    specs = specs or default_fleet()
    proj = Projector.for_points([ORIGIN_LON], [ORIGIN_LAT])
    _x, _y = proj.fwd([ORIGIN_LON], [ORIGIN_LAT])
    ox, oy = float(_x[0]), float(_y[0])
    truth = Truth(meta={"seed": seed, "start": start.isoformat(), "dt_s": dt_s, "origin": "simulated",
                        "note": "Todos os dados e eventos são simulados."})
    all_pts: list[TelemetryPoint] = []
    rng_master = np.random.SeedSequence(seed)
    for spec, ss in zip(specs, rng_master.spawn(len(specs))):
        rng = np.random.default_rng(ss)
        pts, ev, ar = _simulate_machine(spec, rng, proj, (ox, oy), start, dt_s, max_seconds)
        all_pts += pts
        truth.events += ev
        truth.areas[spec.machine_id] = ar
        x0, y0 = spec.origin_xy
        ring = [(x0, y0), (x0 + spec.field_w, y0), (x0 + spec.field_w, y0 + spec.field_l), (x0, y0 + spec.field_l), (x0, y0)]
        lon, lat = proj.inv([ox + p[0] for p in ring], [oy + p[1] for p in ring])
        truth.fields[spec.field_id] = {"type": "Polygon", "coordinates": [[[float(a), float(b)] for a, b in zip(lon, lat)]]}
    all_pts.sort(key=lambda p: (p.ts, p.machine_id))
    return all_pts, truth


def _simulate_machine(spec, rng, proj, org, start, dt_s, max_seconds):
    ox, oy = org
    legs, n_pass = _path(spec)
    events: list[dict] = []
    # --- plano de cenário (em segundos desde o início) ---
    total_work_s_est = n_pass * spec.field_l / spec.speed_ms
    sc = spec.scenario
    stop_win = gnss_win = flow_win = miss_win = None
    if sc == "stop":
        stop_win = (total_work_s_est * 0.35, total_work_s_est * 0.35 + 420)  # 7 min parada (falha)
    if sc == "gnss_loss":
        gnss_win = (total_work_s_est * 0.30, total_work_s_est * 0.30 + 240)  # 4 min sem GNSS
        miss_win = (total_work_s_est * 0.12, total_work_s_est * 0.12 + 150)  # 2,5 min sem flow/pressão (sensor)
    if sc == "abnormal_flow":
        flow_win = (total_work_s_est * 0.45, total_work_s_est * 0.45 + 300)  # 5 min vazão -35%
    # --- itinerário: fila de tarefas ("move", alvo, modo) | ("refill", segundos) ---
    from collections import deque
    tasks: deque = deque()
    for (xa, ya, xb, yb, _w) in legs:
        tasks.append(("move", np.array([xa, ya], float), "transport"))
        tasks.append(("move", np.array([xb, yb], float), "working"))
    t = 0.0
    pos = np.array(spec.depot_xy, float)
    tank, counter, speed = spec.tank_l, 0.0, 0.0
    pts: list[TelemetryPoint] = []
    sec_to_ts = lambda s: start + timedelta(seconds=s)
    nominal_flow = spec.rate_lha * spec.speed_ms * 3.6 * spec.width_m / 600.0
    while t < max_seconds and tasks:
        kind, arg, *rest = tasks[0]
        mode = "transport"
        fault_code = None
        v_cmd = 0.0
        if kind == "refill":
            mode = "refilling"
            tasks[0] = ("refill", arg - dt_s)
            if arg - dt_s <= 0:
                tasks.popleft()
                tank = spec.tank_l
        else:
            mode = rest[0]
            if mode == "working" and stop_win and stop_win[0] <= t < stop_win[1]:
                mode, fault_code = "fault", "SIM-E042"      # parada por falha simulada (código fictício)
            elif mode == "working" and tank <= spec.refill_threshold_l:
                tasks.appendleft(("move", pos.copy(), "transport"))  # volta ao ponto onde parou
                tasks.appendleft(("refill", 180.0))
                tasks.appendleft(("move", np.array(spec.depot_xy, float), "transport"))
                continue
            else:
                v_cmd = spec.speed_ms if mode == "working" else spec.speed_ms * 1.5
        speed = 0.8 * speed + 0.2 * (max(v_cmd + rng.normal(0, 0.15), 0) if v_cmd > 0 else 0.0)
        if kind == "move" and mode != "fault":
            d = arg - pos
            dist = float(np.hypot(*d))
            step = speed * dt_s
            if dist <= max(step, 1e-9):
                pos = arg.copy()
                tasks.popleft()
            else:
                pos = pos + d / dist * step
        # --- consumo ---
        flow = 0.0
        if mode == "working":
            flow = spec.rate_lha * speed * 3.6 * spec.width_m / 600.0
            if flow_win and flow_win[0] <= t < flow_win[1]:
                flow *= 0.65                                   # vazão -35% (causa NÃO modelada)
            flow = max(flow * (1 + rng.normal(0, 0.02)), 0.0)
            used = flow * dt_s / 60.0
            tank -= used
            counter += used
        press = (spec.base_pressure_bar * (0.4 + 0.6 * min(flow / nominal_flow, 1.3)) + rng.normal(0, 0.04)
                 if mode == "working" else 0.2 + abs(rng.normal(0, 0.02)))
        # --- GNSS ---
        fix, acc, noise = Gnss.rtk_fixed, 0.02, 0.02
        if gnss_win:
            if gnss_win[0] <= t < gnss_win[1]:
                fix, acc, noise = Gnss.none, None, None
            elif gnss_win[1] <= t < gnss_win[1] + 120:
                fix, acc, noise = Gnss.rtk_float, 0.35, 0.35    # reconvergência
        lat = lon = None
        if noise is not None:
            nx, ny = rng.normal(0, noise, 2)
            lo, la = proj.inv([ox + pos[0] + nx], [oy + pos[1] + ny])
            lat, lon = float(la[0]), float(lo[0])
        q = {"gnss": SensorQ.ok if fix == Gnss.rtk_fixed else (SensorQ.degraded if fix == Gnss.rtk_float else SensorQ.failed),
             "flow": SensorQ.ok, "pressure": SensorQ.ok, "tank_level": SensorQ.ok, "speed": SensorQ.ok}
        flow_v, press_v = flow, press
        if miss_win and miss_win[0] <= t < miss_win[1]:
            flow_v = press_v = None
            q["flow"] = q["pressure"] = SensorQ.failed
        st = {"working": State.working, "transport": State.transport, "refilling": State.refilling, "fault": State.fault}[mode]
        pts.append(TelemetryPoint(
            machine_id=spec.machine_id, ts=sec_to_ts(t), origin="simulated", lat=lat, lon=lon,
            speed_ms=float(max(speed + rng.normal(0, 0.02), 0)), state=st, gnss_fix=fix, accuracy_m=acc,
            flow_lpm=None if flow_v is None else float(flow_v), pressure_bar=None if press_v is None else float(max(press_v, 0)),
            tank_level_l=float(max(tank, 0)), consumption_l=float(counter), working_width_m=spec.width_m,
            application_rate_lha=spec.rate_lha if mode == "working" else None,
            error_codes=[fault_code] if fault_code else [], sensor_quality=q,
            property_id=spec.property_id, field_id=spec.field_id, operator_id=spec.operator_id))
        t += dt_s
    leg_i = n_pass if not tasks else 0
    # eventos injetados (apenas os que realmente ocorreram dentro do período simulado)
    tmax = t
    for typ, win in (("stop", stop_win), ("gnss_loss", gnss_win), ("abnormal_flow", flow_win), ("missing_data", miss_win)):
        if win and win[0] < tmax:
            events.append({"machine_id": spec.machine_id, "type": typ, "start": sec_to_ts(win[0]).isoformat(),
                           "end": sec_to_ts(min(win[1], tmax)).isoformat(), "origin": "simulated"})
    # verdade de área: faixas reais (sem ruído) unidas analiticamente
    from shapely.geometry import box
    from shapely.ops import unary_union
    x0, y0 = spec.origin_xy
    rects = [box(x0 + k * spec.spacing_m, y0, x0 + k * spec.spacing_m + spec.width_m, y0 + spec.field_l) for k in range(n_pass)]
    uni = unary_union(rects)
    # área bruta/única planejadas COM recorte no talhão (a última faixa pode passar do limite leste)
    fld = box(x0, y0, x0 + spec.field_w, y0 + spec.field_l)
    ar = {"planned_gross_ha": sum(r.area for r in rects) / 1e4, "planned_unique_ha": uni.area / 1e4,
          "planned_unique_inside_field_ha": uni.intersection(fld).area / 1e4, "field_ha": fld.area / 1e4,
          "nominal_overlap_pct_between_adjacent_passes": 100 * (1 - spec.spacing_m / spec.width_m) if spec.spacing_m < spec.width_m else 0.0,
          "executed_seconds": float(tmax), "completed": not tasks}
    return pts, events, ar
