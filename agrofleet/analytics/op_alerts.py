"""Alertas operacionais com estado (Etapa 5): nível de energia, risco de não conclusão, manutenção e perda de conexão.

Diferença em relação a ``anomalies.py``: lá cada alerta é um EVENTO detectado numa janela (início e fim). Aqui cada
família tem um ciclo de vida por máquina: pendente → ativo (com severidade que pode subir ou descer) → resolvido.

Famílias (uma ativa por máquina e origem, sem duplicar):
- ``energy_level``    nível de combustível/bateria vs. limites do CADASTRO: warning (<= aviso) ou critical (<= crítico).
- ``autonomy_risk``   viabilidade da Etapa 4: amarelo → warning; vermelho → critical (risco previsto de não concluir).
                      Nível baixo e risco de não concluir são alertas DISTINTOS: um pode existir sem o outro.
- ``maintenance_due`` horímetro >= última manutenção + intervalo, só com a regra cadastrada; aviso prévio opcional.
- ``connection_lost`` operação ativa sem dado novo há mais de ``offline_after_s``.

Anti-ruído:
- confirmação: a condição precisa se manter por ``confirm_s`` (tempo de avaliação) antes de abrir ou subir; cada
  severidade tem sua própria confirmação, então um ``critical`` nunca nasce de um único instante.
- histerese: para descer ou resolver, o valor precisa sair da faixa com folga (ex.: nível > limite + 2 pontos) e
  continuar fora por ``clear_s``.
- portões de qualidade: leitura com falha, velha ou ausente congela o estado (não abre, não resolve); ``critical``
  de autonomia exige taxa medida por sensor, fontes concordantes e janela sem lacunas, senão fica em warning e diz por quê.

A severidade crítica representa risco operacional ESTIMADO, não uma decisão automática de segurança. O sistema
permanece exclusivamente de monitoramento, sem comandos para máquinas.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from ..registry import MachineProfile

FAMILIES = ("energy_level", "autonomy_risk", "maintenance_due", "connection_lost")
RANK = {None: 0, "info": 1, "warning": 2, "critical": 3}
SEVERITIES = ("info", "warning", "critical")
CRITICAL_NOTE = ("A severidade crítica representa risco operacional estimado, não uma decisão automática de segurança. "
                 "O sistema permanece exclusivamente de monitoramento, sem comandos para máquinas.")
LABEL = {"energy_level": "Nível de energia", "autonomy_risk": "Risco de não conclusão",
         "maintenance_due": "Manutenção preventiva", "connection_lost": "Perda de conexão"}


@dataclass
class OpsAlertConfig:
    confirm_s: float = 60.0              # condição sustentada antes de abrir/subir (energia e autonomia)
    clear_s: float = 120.0               # condição fora da faixa antes de descer/resolver
    level_hysteresis_pct: float = 2.0    # pontos percentuais acima do limite para sair
    margin_hysteresis_pct: float = 5.0   # pontos de margem acima do limite para sair
    offline_after_s: float = 300.0       # sem dado novo → perda de conexão
    online_within_s: float = 30.0        # volta: dado com no máximo esta idade
    maintenance_notice_h: float | None = None   # aviso prévio (info) antes do vencimento; None = só no vencimento
    critical_requires_sensor_rate: bool = True
    eval_interval_s: float = 30.0        # (backend) intervalo mínimo entre avaliações ao vivo da mesma máquina


@dataclass
class Snapshot:
    """Entradas de uma avaliação, já calculadas pelas Etapas 4 (missão/autonomia) e pela conexão."""
    machine_id: str
    origin: str
    ts: str                              # instante da avaliação (ISO, UTC): agora ou ``as_of``
    profile: MachineProfile | None
    operation_id: str | None = None
    operation_active: bool = False
    autonomy: dict | None = None         # AutonomyResult.to_dict()
    mission_reliability: list = field(default_factory=list)
    mission_done: bool = False
    engine_hours: float | None = None
    data_age_s: float | None = None
    level_fresh: bool = True             # leitura de nível válida e recente


@dataclass
class Target:
    """Severidade desejada por uma regra num instante."""
    severity: str | None = None
    hold: bool = False                   # indeterminado: congela o estado
    clear_ok: bool = True                # histerese: pode descer/resolver
    message: str = ""
    evidence: dict = field(default_factory=dict)
    rule: str = ""
    confirm_s: float = 0.0
    clear_s: float = 0.0
    hold_reason: str | None = None


def _ts(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


def _fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}".replace(".", ",")


# ---------------------------------------------------------------------------------------------------------------
# regras
# ---------------------------------------------------------------------------------------------------------------

def rule_energy_level(s: Snapshot, cfg: OpsAlertConfig, cur: str | None) -> Target | str:
    p = s.profile
    if p is None:
        return "máquina sem cadastro da mesma origem"
    if p.energy_warning_pct is None and p.energy_critical_pct is None:
        return "limites de energia (energy_warning_pct/energy_critical_pct) não cadastrados"
    a = s.autonomy or {}
    lvl = a.get("level_pct")
    if lvl is None or not s.level_fresh:
        return Target(hold=True, hold_reason="sem leitura de nível válida e recente")
    w, c, h = p.energy_warning_pct, p.energy_critical_pct, cfg.level_hysteresis_pct
    sev = "critical" if c is not None and lvl <= c else "warning" if w is not None and lvl <= w else None
    # histerese: para sair da severidade atual, o nível precisa passar o limite dela + h
    thr_cur = {"critical": c, "warning": w}.get(cur)
    clear_ok = thr_cur is None or lvl > thr_cur + h
    what = "Combustível" if p.energy_source == "diesel" else "Bateria"
    amt = a.get("level_amount")
    unit = a.get("unit") or ""
    lim = c if sev == "critical" else w
    msg = (f"{what} em {_fmt(lvl)}%" + (f" ({_fmt(amt, 0)} {unit})" if amt is not None else "") +
           (f": abaixo do limite {'crítico' if sev == 'critical' else 'de aviso'} de {_fmt(lim, 0)}% do cadastro." if sev else "."))
    return Target(sev, clear_ok=clear_ok, message=msg, confirm_s=cfg.confirm_s, clear_s=cfg.clear_s,
                  rule="level_pct <= energy_critical_pct | energy_warning_pct (cadastro); saída > limite + histerese",
                  evidence={"level_pct": lvl, "level_amount": amt, "unit": unit, "warning_pct": w, "critical_pct": c,
                            "hysteresis_pct": h, "level_source": a.get("level_source"), "level_ts": a.get("level_ts")})


AUT_CONFIG_REASONS = {"capacity_not_registered", "reserve_not_registered", "margin_not_registered", "energy_source_unknown"}


def rule_autonomy(s: Snapshot, cfg: OpsAlertConfig, cur: str | None) -> Target | str:
    if s.profile is None:
        return "máquina sem cadastro da mesma origem"
    a = s.autonomy
    if a is None:
        return "autonomia não calculada"
    codes = {r["code"] for r in a.get("reasons", [])}
    if codes & AUT_CONFIG_REASONS:
        return "cadastro incompleto para autonomia: " + ", ".join(sorted(codes & AUT_CONFIG_REASONS))
    if s.mission_done or "mission_done" in codes:
        return Target(None, message="Área planejada coberta.", rule="mission_done")
    st = a.get("status")
    if st == "gray":
        return Target(hold=True, hold_reason="autonomia indeterminada: " + ", ".join(sorted(codes)))
    m, mp, req, h = a.get("margin_pct"), a.get("margin_pessimistic_pct"), a.get("required_margin_pct"), cfg.margin_hysteresis_pct
    gates = []
    if cfg.critical_requires_sensor_rate and a.get("rate_source") == "level_slope":
        gates.append("taxa estimada pela queda do nível (sem sensor de taxa)")
    if "rate_sources_disagree" in codes:
        gates.append("taxa do sensor e queda do nível discordam")
    if "data_gaps" in {r["code"] for r in s.mission_reliability}:
        gates.append("lacunas de dados na janela recente")
    sev = {"red": "critical", "yellow": "warning", "green": None}[st]
    if sev == "critical" and gates:
        sev = "warning"
    if cur == "critical":
        clear_ok = m is not None and m >= h
    elif cur == "warning":
        clear_ok = m is not None and req is not None and m >= req + h and mp is not None and mp >= 0
    else:
        clear_ok = True
    rem, aut = a.get("remaining_h"), a.get("autonomy_h")
    if st == "red":
        msg = (f"Risco de não concluir: autonomia estimada de {_fmt(aut)} h para {_fmt(rem)} h restantes "
               f"(margem {_fmt(m, 0)}%).")
    elif st == "yellow":
        msg = (f"Margem de autonomia apertada: {_fmt(m, 0)}% (exigida {_fmt(req, 0)}%)" +
               (f"; no pior caso, {_fmt(mp, 0)}%." if mp is not None else "; pior caso indeterminado."))
    else:
        msg = f"Autonomia suficiente: margem {_fmt(m, 0)}%."
    if gates and st == "red":
        msg += " Mantido em aviso: " + "; ".join(gates) + "."
    return Target(sev, clear_ok=clear_ok, message=msg, confirm_s=cfg.confirm_s, clear_s=cfg.clear_s,
                  rule="autonomia (Etapa 4): vermelho → critical, amarelo → warning; saída com margem + histerese",
                  evidence={"autonomy_status": st, "margin_pct": m, "margin_pessimistic_pct": mp, "required_margin_pct": req,
                            "autonomy_h": aut, "remaining_h": rem, "rate": a.get("rate"), "rate_unit": a.get("rate_unit"),
                            "rate_source": a.get("rate_source"), "usable_amount": a.get("usable_amount"),
                            "needed_amount": a.get("needed_amount"), "unit": a.get("unit"), "critical_gates_failed": gates,
                            "hysteresis_pct": h, "basis": a.get("basis")})


def rule_maintenance(s: Snapshot, cfg: OpsAlertConfig, cur: str | None) -> Target | str:
    p = s.profile
    if p is None or p.maintenance_interval_h is None or p.last_maintenance_engine_h is None:
        return "regra de manutenção não cadastrada (maintenance_interval_h + last_maintenance_engine_h)"
    if s.engine_hours is None:
        return Target(hold=True, hold_reason="horímetro sem leitura válida")
    due = p.last_maintenance_engine_h + p.maintenance_interval_h
    left = due - s.engine_hours
    n = cfg.maintenance_notice_h
    sev = "warning" if left <= 0 else "info" if n is not None and left <= n else None
    msg = (f"Manutenção preventiva vencida há {_fmt(-left)} h de motor (intervalo de {_fmt(p.maintenance_interval_h, 0)} h)."
           if left <= 0 else f"Manutenção preventiva em {_fmt(left)} h de motor.")
    return Target(sev, message=msg, rule="engine_hours >= last_maintenance_engine_h + maintenance_interval_h (cadastro)",
                  evidence={"engine_hours_h": s.engine_hours, "due_at_h": due, "hours_left": left,
                            "interval_h": p.maintenance_interval_h, "last_maintenance_h": p.last_maintenance_engine_h,
                            "notice_h": n})


def rule_connection(s: Snapshot, cfg: OpsAlertConfig, cur: str | None) -> Target | str:
    if not s.operation_active:
        return Target(None, message="Sem operação ativa.", rule="operation inactive")
    if s.data_age_s is None:
        return Target(hold=True, hold_reason="sem nenhum dado da máquina")
    age = s.data_age_s
    sev = "warning" if age > cfg.offline_after_s else None
    clear_ok = age <= cfg.online_within_s
    if sev is None and cur is not None and not clear_ok:
        sev = cur                                   # entre os limites: mantém (histerese)
    msg = (f"Sem dado novo há {_fmt(age / 60, 0)} min durante operação ativa. A situação atual da máquina é desconhecida."
           if age > cfg.offline_after_s else "Telemetria recebida novamente.")
    return Target(sev, clear_ok=clear_ok, message=msg, rule=f"idade do dado > {cfg.offline_after_s:.0f} s; volta <= {cfg.online_within_s:.0f} s",
                  evidence={"data_age_s": age, "offline_after_s": cfg.offline_after_s, "online_within_s": cfg.online_within_s})


RULES = {"energy_level": rule_energy_level, "autonomy_risk": rule_autonomy, "maintenance_due": rule_maintenance,
         "connection_lost": rule_connection}


# ---------------------------------------------------------------------------------------------------------------
# máquina de estados
# ---------------------------------------------------------------------------------------------------------------

def alert_id(machine_id: str, family: str, origin: str, raised_at: str) -> str:
    return hashlib.sha1(f"op|{machine_id}|{family}|{origin}|{raised_at}".encode()).hexdigest()[:16]


def new_state() -> dict:
    return {f: {"active": None, "pending": {}, "clearing": None} for f in FAMILIES}


def step(state: dict | None, s: Snapshot, cfg: OpsAlertConfig | None = None, families=FAMILIES) -> tuple[dict, list, list]:
    """Aplica uma avaliação. Retorna (novo estado, transições, regras puladas).

    Transição: {action: raise|escalate|deescalate|resolve|update, family, alert{...}}. ``update`` só renova a
    evidência e o fim de um alerta ativo estável (não precisa ser difundido)."""
    cfg = cfg or OpsAlertConfig()
    state = state or new_state()
    now = _ts(s.ts)
    out, skipped = [], []
    for fam in families:
        st = state.setdefault(fam, {"active": None, "pending": {}, "clearing": None})
        act = st["active"]
        cur = act["severity"] if act else None
        t = RULES[fam](s, cfg, cur)
        if isinstance(t, str):                      # regra não se aplica (configuração ausente)
            skipped.append({"machine_id": s.machine_id, "rule": fam, "reason": t})
            st["pending"], st["clearing"] = {}, None
            if act:                                 # regra removida do cadastro: encerra o que estava aberto
                out.append(_close(st, act, s, "resolve", f"Regra deixou de se aplicar: {t}."))
            continue
        if t.hold:
            st["pending"], st["clearing"] = {}, None
            if act:
                act["evidence"] = {**act["evidence"], "indeterminate": t.hold_reason, "indeterminate_at": s.ts}
                act["end"] = s.ts
            continue
        want = RANK[t.severity]
        if want > RANK[cur]:
            st["clearing"] = None
            pend = {k: v for k, v in st["pending"].items() if RANK[cur] < RANK[k] <= want}
            for sev in SEVERITIES:
                if RANK[cur] < RANK[sev] <= want:
                    pend.setdefault(sev, s.ts)
            st["pending"] = pend
            ready = [sev for sev, since in pend.items() if now - _ts(since) >= t.confirm_s - 1e-9]
            if ready:
                top = max(ready, key=RANK.get)
                action = "raise" if act is None else "escalate"
                if act is None:
                    act = {"id": alert_id(s.machine_id, fam, s.origin, pend[top]), "family": fam, "type": fam,
                           "machine_id": s.machine_id, "origin": s.origin, "operation_id": s.operation_id,
                           "start": pend[top], "history": []}
                    st["active"] = act
                act.update(severity=top, message=t.message, evidence=t.evidence, rule=t.rule, end=s.ts, status="active")
                act["history"].append({"ts": s.ts, "severity": top, "action": action})
                st["pending"] = {k: v for k, v in pend.items() if RANK[k] > RANK[top]}
                out.append({"action": action, "family": fam, "alert": _public(act)})
            elif act:
                act["end"] = s.ts
        elif want < RANK[cur]:
            st["pending"] = {}
            if not t.clear_ok:                      # dentro da faixa de histerese: mantém
                st["clearing"] = None
                act.update(end=s.ts, evidence={**t.evidence, "in_hysteresis_band": True})
                continue
            st["clearing"] = st["clearing"] or {"since": s.ts}
            if now - _ts(st["clearing"]["since"]) >= t.clear_s - 1e-9:
                st["clearing"] = None
                if t.severity is None:
                    out.append(_close(st, act, s, "resolve", t.message, t.evidence))
                else:
                    act.update(severity=t.severity, message=t.message, evidence=t.evidence, end=s.ts)
                    act["history"].append({"ts": s.ts, "severity": t.severity, "action": "deescalate"})
                    out.append({"action": "deescalate", "family": fam, "alert": _public(act)})
            else:
                act["end"] = s.ts
        else:
            st["pending"], st["clearing"] = {}, None
            if act:
                act.update(message=t.message, evidence=t.evidence, end=s.ts)
                out.append({"action": "update", "family": fam, "alert": _public(act)})
    return state, out, skipped


def _close(st, act, s, action, msg, ev=None):
    act.update(status="resolved", end=s.ts, resolved_at=s.ts, resolution=msg)
    if ev:
        act["evidence"] = {**act["evidence"], "at_resolution": ev}
    act["history"].append({"ts": s.ts, "severity": None, "action": action})
    st["active"] = None
    return {"action": action, "family": act["family"], "alert": _public(act)}


def _public(act: dict) -> dict:
    d = {k: v for k, v in act.items()}
    d["label"] = LABEL[act["family"]]
    d["category"] = "operational"
    d["informational"] = True
    if d.get("severity") == "critical" or any(h.get("severity") == "critical" for h in act.get("history", [])):
        d["note"] = CRITICAL_NOTE
    return d


def config_dict(cfg: OpsAlertConfig) -> dict:
    return asdict(cfg)


# ---------------------------------------------------------------------------------------------------------------
# montagem do Snapshot a partir dos dados (usa os cálculos da Etapa 4)
# ---------------------------------------------------------------------------------------------------------------

STALE_LEVEL = {"no_level_reading", "level_not_recent", "stale_data"}


def snapshot_from_data(df, op, prof: MachineProfile | None, field_lonlat: dict | None, ts: str,
                       data_age_s: float | None, mission_result=None) -> Snapshot:
    """``df``: telemetria da máquina (a missão filtra a janela e a origem da operação)."""
    from .autonomy import autonomy
    from .mission import mission, operation_frame

    m = mission_result or mission(df, op, field_lonlat, data_age_s=data_age_s)
    g = operation_frame(df, op)
    a = autonomy(g, prof, m.remaining(), data_age_s=data_age_s) if prof is not None else None
    eh = None
    if len(g) and "engine_hours_h" in g:
        ok = g["engine_hours_h"].notna().to_numpy().copy()
        if "q_engine" in g:
            ok &= g["q_engine"].astype(object).to_numpy() != "failed"
        if ok.any():
            # horímetro é contador acumulado (só sobe): vale a última leitura válida, não a mediana (que atrasaria)
            t_last = g["ts"][ok].iloc[-1]
            if (g["ts"].iloc[-1] - t_last).total_seconds() <= 300:
                eh = float(g["engine_hours_h"].to_numpy()[ok][-1])
    codes = {r["code"] for r in a.reasons} if a else set()
    return Snapshot(machine_id=op.machine_id, origin=op.origin, ts=ts, profile=prof, operation_id=op.operation_id,
                    operation_active=op.status == "active", autonomy=a.to_dict() if a else None,
                    mission_reliability=list(m.reliability), mission_done=m.remaining_ha == 0,
                    engine_hours=eh, data_age_s=data_age_s, level_fresh=not (codes & STALE_LEVEL))
