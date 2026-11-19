"""Etapa 5: alertas operacionais com estado (confirmação, histerese, deduplicação, portões de qualidade).

Sequências montadas à mão para a máquina de estados; cenários do simulador contra a verdade; API, caminho ao vivo e
vigia de conexão.
"""
from datetime import datetime, timedelta, timezone

import pytest

from agrofleet.analytics.frame import to_frame
from agrofleet.analytics.op_alerts import CRITICAL_NOTE, OpsAlertConfig, Snapshot, snapshot_from_data, step
from agrofleet.registry import MachineProfile
from agrofleet.simulator.fleet import DEFAULT_START
from agrofleet.simulator.operations import scenario_a, scenario_b, simulate_operation

T = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
CFG = OpsAlertConfig()                    # confirmação 60 s, saída 120 s, histerese 2 pontos / 5 de margem


def diesel(**kw):
    base = dict(machine_id="T-1", name="Trator", kind="tractor", energy_source="diesel", origin="simulated",
                fuel_tank_capacity_l=600, energy_reserve_pct=10, energy_warning_pct=25, energy_critical_pct=12,
                autonomy_margin_pct=15)
    base.update(kw)
    return MachineProfile(**base)


def aut(status="green", level=60.0, margin=50.0, pess=40.0, rate_source="fuel_rate_lph", reasons=()):
    return {"status": status, "level_pct": level, "level_amount": level * 6, "unit": "L", "margin_pct": margin,
            "margin_pessimistic_pct": pess, "required_margin_pct": 15.0, "autonomy_h": 5.0, "remaining_h": 4.0,
            "rate": 30.0, "rate_unit": "L/h", "rate_source": rate_source, "usable_amount": 200.0, "needed_amount": 120.0,
            "reasons": [{"code": c, "message": c} for c in reasons], "basis": "teste"}


def snap(t_s, prof=None, **kw):
    base = dict(machine_id="T-1", origin="simulated", ts=(T + timedelta(seconds=t_s)).isoformat(),
                profile=prof or diesel(), operation_id="OP-1", operation_active=True, data_age_s=0.0)
    base.update(kw)
    return Snapshot(**base)


def run(seq, state=None, fams=("energy_level", "autonomy_risk", "maintenance_due", "connection_lost")):
    """seq = [(t_s, kwargs do snapshot)]. Retorna (estado, [(t, ação, família, severidade)])."""
    log = []
    for t, kw in seq:
        state, tr, _ = step(state, snap(t, **kw), CFG, fams)
        log += [(t, x["action"], x["family"], x["alert"].get("severity")) for x in tr if x["action"] != "update"]
    return state, log


E = ("energy_level",)
A = ("autonomy_risk",)


def lvl(v, **kw):
    return {"autonomy": aut(level=v), **kw}


# --- nível de energia --------------------------------------------------------------------------------------------

def test_nivel_exige_confirmacao():
    _, log = run([(0, lvl(24)), (30, lvl(24)), (60, lvl(24))], fams=E)
    assert log == [(60, "raise", "energy_level", "warning")]


def test_pico_isolado_nao_abre_alerta():
    _, log = run([(0, lvl(24)), (30, lvl(26)), (60, lvl(24)), (90, lvl(24))], fams=E)
    assert log == []                                   # a confirmação recomeçou em 60 s


def test_histerese_na_saida():
    st, log = run([(0, lvl(24)), (60, lvl(24))], fams=E)
    st, log2 = run([(90, lvl(26.5)), (300, lvl(26.5))], st, fams=E)     # dentro de limite + 2: continua
    assert log2 == [] and st["energy_level"]["active"]["evidence"]["in_hysteresis_band"]
    st, log3 = run([(330, lvl(27.5)), (420, lvl(27.5)), (450, lvl(27.5))], st, fams=E)
    assert log3 == [(450, "resolve", "energy_level", "warning")]        # 120 s fora da faixa; resolvido guarda a severidade


def test_escalada_com_confirmacao_propria_e_sem_duplicar():
    st, log = run([(0, lvl(24)), (60, lvl(24)), (90, lvl(11)), (120, lvl(11)), (150, lvl(11))], fams=E)
    assert log == [(60, "raise", "energy_level", "warning"), (150, "escalate", "energy_level", "critical")]
    act = st["energy_level"]["active"]
    assert [h["action"] for h in act["history"]] == ["raise", "escalate"]          # mesmo alerta, mesma id


def test_critico_direto_espera_a_propria_confirmacao():
    _, log = run([(0, lvl(10)), (30, lvl(10)), (60, lvl(10))], fams=E)
    assert log == [(60, "raise", "energy_level", "critical")]


def test_desescalada_com_histerese():
    st, _ = run([(0, lvl(10)), (60, lvl(10))], fams=E)
    st, log = run([(90, lvl(13.5)), (400, lvl(13.5))], st, fams=E)      # <= 12 + 2: mantém crítico
    assert log == []
    st, log = run([(430, lvl(16)), (560, lvl(16))], st, fams=E)
    assert log == [(560, "deescalate", "energy_level", "warning")]


def test_dado_velho_congela_nao_abre_nem_resolve():
    _, log = run([(0, lvl(10, level_fresh=False)), (60, lvl(10, level_fresh=False))], fams=E)
    assert log == []
    st, _ = run([(0, lvl(10)), (60, lvl(10))], fams=E)
    st, log = run([(90, lvl(80, level_fresh=False)), (400, lvl(80, level_fresh=False))], st, fams=E)
    assert log == [] and st["energy_level"]["active"]["evidence"]["indeterminate"]


def test_sem_limites_cadastrados_a_regra_e_pulada():
    p = diesel(energy_warning_pct=None, energy_critical_pct=None)
    _, _, sk = step(None, snap(0, prof=p, autonomy=aut(level=1)), CFG, E)
    assert sk and "não cadastrados" in sk[0]["reason"]


# --- risco de não conclusão ----------------------------------------------------------------------------------------

def test_vermelho_vira_critico_com_nota():
    st, log = run([(0, {"autonomy": aut("red", margin=-30, pess=-40)}), (60, {"autonomy": aut("red", margin=-30, pess=-40)})], fams=A)
    assert log == [(60, "raise", "autonomy_risk", "critical")]
    a = st["autonomy_risk"]["active"]
    assert "Risco de não concluir" in a["message"]
    tr = step(st, snap(90, autonomy=aut("red", margin=-30, pess=-40)), CFG, A)[1]
    assert tr[0]["alert"]["note"] == CRITICAL_NOTE and tr[0]["alert"]["informational"] is True


@pytest.mark.parametrize("gate", [dict(rate_source="level_slope"), dict(reasons=("rate_sources_disagree",))])
def test_portao_de_qualidade_segura_o_critico(gate):
    a = aut("red", margin=-30, pess=-40, **gate)
    st, log = run([(0, {"autonomy": a}), (60, {"autonomy": a})], fams=A)
    assert log == [(60, "raise", "autonomy_risk", "warning")]
    assert st["autonomy_risk"]["active"]["evidence"]["critical_gates_failed"]


def test_lacunas_seguram_o_critico():
    a = aut("red", margin=-30, pess=-40)
    rel = [{"code": "data_gaps", "message": "x"}]
    _, log = run([(0, {"autonomy": a, "mission_reliability": rel}), (60, {"autonomy": a, "mission_reliability": rel})], fams=A)
    assert log == [(60, "raise", "autonomy_risk", "warning")]


def test_amarelo_aviso_e_saida_com_margem():
    y = {"autonomy": aut("yellow", margin=8, pess=2)}
    st, log = run([(0, y), (60, y)], fams=A)
    assert log == [(60, "raise", "autonomy_risk", "warning")]
    g_band = {"autonomy": aut("green", margin=17, pess=5)}            # verde, mas < 15 + 5: na faixa
    st, log = run([(90, g_band), (400, g_band)], st, fams=A)
    assert log == []
    g = {"autonomy": aut("green", margin=25, pess=5)}
    st, log = run([(430, g), (550, g)], st, fams=A)
    assert log == [(550, "resolve", "autonomy_risk", "warning")]


def test_cinza_congela_e_missao_concluida_resolve():
    r = {"autonomy": aut("red", margin=-30, pess=-40)}
    st, _ = run([(0, r), (60, r)], fams=A)
    gray = {"autonomy": aut("gray", margin=None, pess=None, reasons=("rate_unavailable",))}
    st, log = run([(90, gray), (900, gray)], st, fams=A)
    assert log == [] and st["autonomy_risk"]["active"]["severity"] == "critical"
    st, log = run([(930, {"autonomy": aut("green", reasons=("mission_done",)), "mission_done": True})], st, fams=A)
    assert log == [(930, "resolve", "autonomy_risk", "critical")]


def test_nivel_baixo_e_risco_sao_alertas_distintos():
    a = aut("green", level=20, margin=60, pess=50)          # nível baixo, mas sobra energia para concluir
    st, log = run([(0, {"autonomy": a}), (60, {"autonomy": a})], fams=E + A)
    assert log == [(60, "raise", "energy_level", "warning")] and st["autonomy_risk"]["active"] is None


def test_cadastro_incompleto_pula_autonomia():
    a = aut("gray", reasons=("reserve_not_registered",))
    _, _, sk = step(None, snap(0, autonomy=a), CFG, A)
    assert "reserve_not_registered" in sk[0]["reason"]


# --- manutenção e conexão --------------------------------------------------------------------------------------------

def test_manutencao_so_com_regra_cadastrada():
    _, _, sk = step(None, snap(0, engine_hours=9999), CFG, ("maintenance_due",))
    assert "não cadastrada" in sk[0]["reason"]
    p = diesel(maintenance_interval_h=250, last_maintenance_engine_h=4582)
    st, log = run([(0, {"prof": p, "engine_hours": 4831.9}), (30, {"prof": p, "engine_hours": 4832.0}),
                   (60, {"prof": p, "engine_hours": 4832.1})], fams=("maintenance_due",))
    assert log == [(30, "raise", "maintenance_due", "warning")]
    assert st["maintenance_due"]["active"]["evidence"]["due_at_h"] == 4832


def test_aviso_previo_de_manutencao_so_se_configurado():
    p = diesel(maintenance_interval_h=250, last_maintenance_engine_h=4582)
    cfg = OpsAlertConfig(maintenance_notice_h=10)
    _, tr, _ = step(None, snap(0, prof=p, engine_hours=4825), cfg, ("maintenance_due",))
    assert tr[0]["action"] == "raise" and tr[0]["alert"]["severity"] == "info"


def test_conexao_com_histerese_e_so_em_operacao_ativa():
    C = ("connection_lost",)
    _, log = run([(0, {"data_age_s": 900, "operation_active": False})], fams=C)
    assert log == []
    st, log = run([(0, {"data_age_s": 100}), (30, {"data_age_s": 400})], fams=C)
    assert log == [(30, "raise", "connection_lost", "warning")]
    st, log = run([(60, {"data_age_s": 100})], st, fams=C)            # entre 30 s e 300 s: mantém
    assert log == []
    st, log = run([(90, {"data_age_s": 5})], st, fams=C)
    assert log == [(90, "resolve", "connection_lost", "warning")]


# --- troca de operação (revisão do PR #2) ------------------------------------------------------------------------

def test_troca_de_operacao_encerra_o_alerta_da_anterior():
    st, log = run([(0, lvl(20)), (60, lvl(20))], fams=E)
    assert log == [(60, "raise", "energy_level", "warning")]
    st, log = run([(90, lvl(20, operation_id="OP-2"))], st, fams=E)
    assert log == [(90, "resolve", "energy_level", "warning")]            # o da OP-1 fecha; o da OP-2 ainda confirma
    st, log = run([(150, lvl(20, operation_id="OP-2"))], st, fams=E)
    assert log == [(150, "raise", "energy_level", "warning")]
    assert st["energy_level"]["active"]["operation_id"] == "OP-2"


# --- cenários do simulador -------------------------------------------------------------------------------------------

def _eval_at(r, df, minutes, state):
    t = DEFAULT_START + timedelta(minutes=minutes)
    s = snapshot_from_data(df[df["ts"] <= t], r.operation, r.machine, r.field_lonlat, t.isoformat(), 0.0)
    state, tr, sk = step(state, s, CFG)
    return state, [(x["action"], x["family"], x["alert"].get("severity")) for x in tr if x["action"] != "update"], sk


@pytest.fixture(scope="module")
def run_b():
    r = simulate_operation(scenario_b(), max_seconds=3.7 * 3600)   # o retorno à base é às 13:33
    return r, to_frame(r.points)


def test_cenario_b_risco_critico_antes_do_nivel_e_do_retorno(run_b):
    r, df = run_b
    st, log, sk = _eval_at(r, df, 40, None)
    assert log == [] and {x["rule"] for x in sk} == {"maintenance_due"}       # elétrico: sem regra de manutenção
    st, log, _ = _eval_at(r, df, 41, st)
    assert log == [("raise", "autonomy_risk", "critical")]                    # risco previsto às 10:41
    st, log, _ = _eval_at(r, df, 131, st)
    st, log, _ = _eval_at(r, df, 132, st)
    assert ("raise", "energy_level", "warning") in log                        # nível < 30% depois, alerta distinto
    ret = next(e for e in r.truth["events"] if e["type"] == "energy_return_to_base")
    assert datetime.fromisoformat(ret["at"]) - (DEFAULT_START + timedelta(minutes=41)) > timedelta(hours=2.5)  # antecedência


def test_cenario_a_sem_alertas_e_manutencao_por_regra():
    r = simulate_operation(scenario_a(), max_seconds=3 * 3600)
    df = to_frame(r.points)
    st, log, _ = _eval_at(r, df, 179, None)
    st, log2, _ = _eval_at(r, df, 180, st)
    assert log == log2 == []                                                 # verde, nível normal, manutenção longe
    r.machine = r.machine.model_copy(update={"last_maintenance_engine_h": 4570.0})   # vence aos 4820 h: já vencida
    _, log, _ = _eval_at(r, df, 180, None)
    assert log == [("raise", "maintenance_due", "warning")]


# --- API, caminho ao vivo e vigia ------------------------------------------------------------------------------------

USERS = {"admin": ("adm", "adm-pass"), "analyst": ("ana", "ana-pass"), "viewer": ("vie", "vie-pass")}


@pytest.fixture(scope="module")
def api():
    from fastapi.testclient import TestClient
    from backend.app.config import Settings
    from backend.app.main import create_app
    st = Settings(database_url="sqlite://", auth_secret="test-secret-test-secret-test-secret-0123", users=USERS, mqtt_host=None)
    app = create_app(st)
    with TestClient(app) as c:
        h = {k: {"Authorization": "Bearer " + c.post("/auth/login", json={"username": u, "password": p}).json()["access_token"]}
             for k, (u, p) in USERS.items()}
        r = c.post("/demo/operations/load", params={"scenarios": "A,B,C", "hours": 3, "start": DEFAULT_START.isoformat()},
                   headers=h["admin"])
        assert r.status_code == 200, r.text
        yield c, h, app, r.json()


def test_api_demo_abre_alertas_sustentados(api):
    c, h, _, load = api
    act = load["active_operational_alerts"]
    assert act["OP-SIM-B"] == {"autonomy_risk": "critical", "energy_level": "warning"}
    assert act["OP-SIM-A"] == {} and act["OP-SIM-C"] == {}
    al = c.get("/alerts", params={"category": "operational", "status": "active", "sort": "priority"}, headers=h["viewer"]).json()
    assert [(a["machine_id"], a["type"], a["severity"]) for a in al] == [("SIM-PE-07", "autonomy_risk", "critical"),
                                                                         ("SIM-PE-07", "energy_level", "warning")]
    assert al[0]["note"] == CRITICAL_NOTE and al[0]["label"] == "Risco de não conclusão" and al[0]["operation_id"] == "OP-SIM-B"


def test_api_status_lista_alertas_por_prioridade(api):
    c, h, _, _ = api
    s = c.get("/operations/OP-SIM-B/status", params={"as_of": (DEFAULT_START + timedelta(hours=3)).isoformat()},
              headers=h["viewer"]).json()
    sev = [a["severity"] for a in s["alerts"]]
    assert sev[0] == "critical" and sev == sorted(sev, key={"critical": 0, "warning": 1, "info": 2}.get)


def test_api_alertas_antigos_continuam_compativeis(api):
    c, h, _, _ = api
    al = c.get("/alerts", headers=h["viewer"]).json()
    assert al and all({"id", "machine_id", "type", "severity", "start", "end", "message", "evidence", "rule", "origin",
                       "informational", "note", "category", "status"} <= set(a) for a in al)


def test_recompute_de_eventos_nao_apaga_operacionais(api):
    c, h, _, _ = api
    r = c.post("/alerts/recompute", params={"operational": False}, headers=h["analyst"])
    assert r.status_code == 200
    assert len(c.get("/alerts", params={"category": "operational", "status": "active"}, headers=h["viewer"]).json()) == 2


def test_replay_permissoes_e_idempotencia(api):
    c, h, _, _ = api
    assert c.post("/operations/OP-SIM-B/alerts/replay", headers=h["viewer"]).status_code == 403
    assert c.post("/operations/NOPE/alerts/replay", headers=h["analyst"]).status_code == 404
    r1 = c.post("/operations/OP-SIM-B/alerts/replay", headers=h["analyst"]).json()
    r2 = c.post("/operations/OP-SIM-B/alerts/replay", headers=h["analyst"]).json()
    assert r1["active"] == r2["active"]
    assert len(c.get("/alerts", params={"operation_id": "OP-SIM-B", "category": "operational"}, headers=h["viewer"]).json()) == 2


def _live_refs(app, machine_id="SIM-PE-07"):
    from backend.app import op_alerts_service as OPS
    from backend.app.db import AlertRow
    with app.state.SF() as s:
        st = OPS.load_state(s, "simulated", machine_id) or {}
        refs = {f: v["active"]["id"] for f, v in st.items() if v.get("active")}
        rows = {i: s.get(AlertRow, i) for i in refs.values()}
        return refs, {i: (r.status, r.operation_id) if r else None for i, r in rows.items()}


def test_auditoria_de_janela_passada_nao_grava_nada(api):
    """Revisão do PR #2: reproduzir uma janela antiga não pode trocar o estado ao vivo nem os alertas gravados."""
    c, h, app, _ = api
    refs0, rows0 = _live_refs(app)
    n0 = len(c.get("/alerts", params={"category": "operational"}, headers=h["viewer"]).json())
    r = c.post("/operations/OP-SIM-B/alerts/replay", params={"end": (DEFAULT_START + timedelta(minutes=30)).isoformat()},
               headers=h["analyst"]).json()
    assert r["persisted"] is False and r["active"]["autonomy_risk"] is None      # às 10:30 ainda não havia risco
    assert _live_refs(app) == (refs0, rows0) and set(refs0) == {"autonomy_risk", "energy_level"}
    assert len(c.get("/alerts", params={"category": "operational"}, headers=h["viewer"]).json()) == n0


def test_reconstrucao_de_operacao_anterior_preserva_o_estado_ao_vivo(api):
    """Revisão do PR #2: reconstruir outra operação da mesma máquina não apaga nem substitui o estado da operação
    corrente; a avaliação seguinte continua no mesmo alerta (sem duplicar) e com a operação certa."""
    from backend.app import op_alerts_service as OPS
    from backend.app.registry_api import get_operation, upsert_operation
    c, h, app, _ = api
    with app.state.SF() as s:
        cur = get_operation(s, "OP-SIM-B")
        upsert_operation(s, cur.model_copy(update={"operation_id": "OP-SIM-B-ANT", "status": "completed",
                                                   "end": DEFAULT_START + timedelta(hours=1)}))
        s.commit()
    refs0, rows0 = _live_refs(app)
    r = c.post("/operations/OP-SIM-B-ANT/alerts/replay", headers=h["analyst"]).json()
    assert r["persisted"] is True
    assert _live_refs(app) == (refs0, rows0)
    assert all(v == ("active", "OP-SIM-B") for v in rows0.values())
    ant = c.get("/alerts", params={"operation_id": "OP-SIM-B-ANT", "category": "operational"}, headers=h["viewer"]).json()
    assert all(a["status"] == "resolved" for a in ant)                           # operação encerrada: nada fica aberto
    with app.state.SF() as s:
        tr, _sk, st = OPS.evaluate(s, "OP-SIM-B", app.state.live, app.state.settings,
                                   as_of=DEFAULT_START + timedelta(hours=3))
        s.commit()
    assert [t for t in tr if t["transition"] == "raise"] == []
    assert {f: v["active"]["id"] for f, v in st.items() if v.get("active")} == refs0
    act = c.get("/alerts", params={"operation_id": "OP-SIM-B", "category": "operational", "status": "active"},
                headers=h["viewer"]).json()
    assert len(act) == 2
    with app.state.SF() as s:                                                     # limpa para os testes seguintes
        from backend.app.db import AlertRow, OperationRow
        s.query(AlertRow).filter(AlertRow.operation_id == "OP-SIM-B-ANT").delete()
        s.delete(s.get(OperationRow, "OP-SIM-B-ANT"))
        s.commit()


def test_operacao_corrente_e_a_ativa_mais_recente(api):
    from backend.app import op_alerts_service as OPS
    from backend.app.db import OperationRow
    from backend.app.registry_api import get_operation, upsert_operation
    _, _, app, _ = api
    with app.state.SF() as s:
        cur = get_operation(s, "OP-SIM-B")
        upsert_operation(s, cur.model_copy(update={"operation_id": "OP-SIM-B-VELHA",
                                                   "start": cur.start - timedelta(days=1)}))
        s.commit()
        assert OPS.current_operations(s, "simulated", "SIM-PE-07")[("simulated", "SIM-PE-07")].id == "OP-SIM-B"
        s.delete(s.get(OperationRow, "OP-SIM-B-VELHA"))
        s.commit()


def test_config_operacional(api):
    c, h, _, _ = api
    assert c.get("/config/alerts/operational", headers=h["viewer"]).json()["confirm_s"] == 60
    assert c.put("/config/alerts/operational", json={"confirm_s": 30}, headers=h["analyst"]).status_code == 403
    assert c.put("/config/alerts/operational", json={"nope": 1}, headers=h["admin"]).status_code == 422
    assert c.put("/config/alerts/operational", json={"online_within_s": 600}, headers=h["admin"]).status_code == 422
    r = c.put("/config/alerts/operational", json={"maintenance_notice_h": 20}, headers=h["admin"])
    assert r.status_code == 200 and r.json()["maintenance_notice_h"] == 20 and r.json()["confirm_s"] == 60
    c.put("/config/alerts/operational", json={"maintenance_notice_h": None}, headers=h["admin"])


def test_vigia_de_conexao_sem_duplicar(api):
    from backend.app import op_alerts_service as OPS
    c, h, app, _ = api
    with app.state.SF() as s:
        first = OPS.watchdog(s, app.state.live, app.state.settings)
        again = OPS.watchdog(s, app.state.live, app.state.settings)
    # dado de março: as 3 operações ativas estão sem dado novo há meses
    assert sorted((a["machine_id"], a["transition"]) for a in first) == [
        ("SIM-PE-07", "raise"), ("SIM-TR-320", "raise"), ("SIM-TR-410", "raise")]
    assert again == []
    al = c.get("/alerts", params={"type": "connection_lost", "status": "active"}, headers=h["viewer"]).json()
    assert len(al) == 3 and all(a["severity"] == "warning" for a in al)


def test_caminho_ao_vivo_avalia_operacao_ativa(api):
    c, h, app, _ = api
    live = app.state.live
    r = simulate_operation(scenario_b(), max_seconds=3 * 3600 + 30)
    cut = DEFAULT_START + timedelta(hours=3)
    batch = [p for p in r.points if p.ts > cut]
    n0 = live.stats["ops_evaluations"]
    msgs = live.process(batch, [])
    assert live.stats["ops_evaluations"] == n0 + 1
    assert any(m["type"] == "telemetry" for m in msgs)
    # dado de março avaliado agora: velho → autonomia e nível congelam (não duplicam nem resolvem o crítico)
    al = c.get("/alerts", params={"machine_id": "SIM-PE-07", "category": "operational", "type": "autonomy_risk"},
               headers=h["viewer"]).json()
    assert len(al) == 1 and al[0]["status"] == "active" and al[0]["severity"] == "critical"
    live.process(batch[:1], [])                                  # dentro do intervalo mínimo: não reavalia
    assert live.stats["ops_evaluations"] == n0 + 1


def test_apagar_dados_remove_estado(api):
    from backend.app import op_alerts_service as OPS
    c, h, app, _ = api
    c.delete("/data", params={"origin": "simulated"}, headers=h["admin"])
    with app.state.SF() as s:
        assert OPS.load_state(s, "simulated", "SIM-PE-07") is None
    assert c.get("/alerts", params={"category": "operational"}, headers=h["viewer"]).json() == []
