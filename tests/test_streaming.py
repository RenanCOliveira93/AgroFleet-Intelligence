"""Etapa 6: cache por operação no backend e mensagem WebSocket ``operation_status``.

O cache nunca pode servir resultado diferente do cálculo sem cache: cada teste compara com ``operation_state`` sem
``ops_cache`` sobre o mesmo banco.
"""
from datetime import timedelta

import pytest

from agrofleet.simulator.fleet import DEFAULT_START
from agrofleet.simulator.operations import scenario_a, simulate_operation
from tests.test_incremental import same

USERS = {"admin": ("adm", "adm-pass"), "viewer": ("vie", "vie-pass")}
# dependem do relógio de parede, não do dado; "alerts" vem do banco (não do cache) e o vigia em segundo plano do app
# pode abrir "perda de conexão" entre duas leituras
VOLATILE = {"evaluated_at", "connection", "alerts"}


class FakeWS:
    """Cliente WebSocket conectado (o laço do app em segundo plano também difunde para ele)."""

    def __init__(self):
        self.got = []

    async def send_json(self, m):
        self.got.append(m)


class NoCache:
    """``live`` sem cache: mesmo ``is_live``, cálculo completo."""
    ops_cache = None

    def __init__(self, live):
        self.is_live = live.is_live


@pytest.fixture(scope="module")
def api():
    from fastapi.testclient import TestClient
    from backend.app.config import Settings
    from backend.app.main import create_app
    st = Settings(database_url="sqlite://", auth_secret="test-secret-test-secret-test-secret-0123", users=USERS,
                  mqtt_host=None, status_interval_s=0.0)
    app = create_app(st)
    with TestClient(app) as c:
        h = {k: {"Authorization": "Bearer " + c.post("/auth/login", json={"username": u, "password": p}).json()["access_token"]}
             for k, (u, p) in USERS.items()}
        r = c.post("/demo/operations/load", params={"scenarios": "A", "hours": 2, "start": DEFAULT_START.isoformat()},
                   headers=h["admin"])
        assert r.status_code == 200, r.text
        sim = simulate_operation(scenario_a(), max_seconds=2.6 * 3600)
        yield c, h, app, sim


def _status(app, as_of=None, cached=True):
    from backend.app.operations_api import operation_state, status_payload
    from backend.app.main import clean
    live = app.state.live if cached else NoCache(app.state.live)
    with app.state.SF() as s:
        st = operation_state(s, "OP-SIM-A", as_of, live, app.state.settings)
        return clean({k: v for k, v in status_payload(s, st, app.state.settings).items() if k not in VOLATILE})


def _feed(app, sim, t0_min, t1_min, batch=60):
    t0, t1 = DEFAULT_START + timedelta(minutes=t0_min), DEFAULT_START + timedelta(minutes=t1_min)
    pts = [p for p in sim.points if t0 < p.ts <= t1]
    msgs = []
    for i in range(0, len(pts), batch):
        msgs += app.state.live.process(pts[i:i + batch], [])
    return msgs


def test_status_com_cache_igual_ao_calculo_completo_enquanto_chega_dado(api):
    c, h, app, sim = api
    store = app.state.live.ops_cache
    assert not same(_status(app), _status(app, cached=False))
    full0 = store.stats["frame_full"]
    for a, b in ((120, 126), (126, 133), (133, 141)):
        _feed(app, sim, a, b)
        assert not same(_status(app), _status(app, cached=False))
    assert store.stats["frame_full"] == full0 and store.stats["frame_deltas"] >= 3      # só leu as linhas novas
    e = store.entry("OP-SIM-A")
    assert e.live.stats["resets"] == 0 and e.live.stats["frozen_buckets"] >= 12


def test_status_de_instante_passado_nao_descarta_o_cache_ao_vivo(api):
    c, h, app, sim = api
    e = app.state.live.ops_cache.entry("OP-SIM-A")
    adv = e.live.stats["advances"]
    t = DEFAULT_START + timedelta(minutes=50)
    assert not same(_status(app, as_of=t), _status(app, as_of=t, cached=False))
    assert _status(app, as_of=t)["n_samples"] < _status(app)["n_samples"]
    _feed(app, sim, 141, 148)
    assert not same(_status(app), _status(app, cached=False))
    assert e.live.stats["resets"] == 0 and e.live.stats["advances"] > adv


def test_chegada_tardia_no_banco_e_reconhecida(api):
    """Uma amostra antiga chega agora (reenvio de dispositivo): o quadro a incorpora e a missão recalcula do zero."""
    from backend.app.db import TelemetryRow
    c, h, app, sim = api
    t = DEFAULT_START + timedelta(minutes=20)
    with app.state.SF() as s:
        s.query(TelemetryRow).filter(TelemetryRow.machine_id == "SIM-TR-320", TelemetryRow.ts == t).delete()
        s.commit()
    assert not same(_status(app), _status(app, cached=False))
    e = app.state.live.ops_cache.entry("OP-SIM-A")
    r0 = e.live.stats["resets"]
    app.state.live.process([p for p in sim.points if p.ts == t], [])
    assert not same(_status(app), _status(app, cached=False))
    assert e.live.stats["resets"] == r0 + 1 and "tardia" in e.live.stats["last_reset"]


def test_mensagem_operation_status_com_cliente_conectado(api):
    c, h, app, sim = api
    live = app.state.live
    assert not [m for m in _feed(app, sim, 148, 149) if m["type"] == "operation_status"]   # sem cliente: não monta
    live.clients.add(FakeWS())
    try:
        msgs = _feed(app, sim, 149, 150)
    finally:
        live.clients.clear()
    st = [m for m in msgs if m["type"] == "operation_status"]
    assert st and {"telemetry"} <= {m["type"] for m in msgs}                                # mensagens antigas inalteradas
    m = st[-1]
    assert (m["operation_id"], m["machine_id"], m["origin"]) == ("OP-SIM-A", "SIM-TR-320", "simulated")
    body = {k: v for k, v in m["status"].items() if k not in VOLATILE}
    assert not same(body, _status(app, cached=False))
    assert m["status"]["mode_label"] == "MODO SIMULAÇÃO"


def test_vigia_envia_status_mesmo_sem_dado_novo(api):
    c, h, app, sim = api
    live = app.state.live
    live.clients.add(FakeWS())
    try:
        msgs = live.run_watchdog()
    finally:
        live.clients.clear()
    st = [m for m in msgs if m["type"] == "operation_status"]
    assert [m["operation_id"] for m in st] == ["OP-SIM-A"]
    assert st[0]["status"]["connection"]["state"] in ("online", "delayed", "offline")


def test_geometria_guardada_enquanto_o_dado_nao_muda(api):
    c, h, app, sim = api
    store = app.state.live.ops_cache
    g1 = c.get("/operations/OP-SIM-A/geometry", headers=h["viewer"]).json()
    hits = store.stats["geometry_hits"]
    g2 = c.get("/operations/OP-SIM-A/geometry", headers=h["viewer"]).json()
    assert g1 == g2 and store.stats["geometry_hits"] == hits + 1
    g3 = c.get("/operations/OP-SIM-A/geometry", params={"simplify_m": 2}, headers=h["viewer"]).json()
    assert store.stats["geometry_hits"] == hits + 1 and g3["display"]["simplify_m"] == 2
    _feed(app, sim, 150, 151)
    g4 = c.get("/operations/OP-SIM-A/geometry", headers=h["viewer"]).json()
    assert g4["last"]["ts"] > g1["last"]["ts"]


def test_health_expoe_o_cache(api):
    c, h, app, sim = api
    oc = c.get("/health").json()["ops_cache"]
    assert {"frame_hits", "frame_deltas", "frame_full", "evictions", "geometry_hits"} <= set(oc)


def test_lru_limita_operacoes_em_memoria():
    from backend.app.ops_cache import OperationCache
    oc = OperationCache(max_ops=2)
    a = oc.entry("a"); oc.entry("b"); oc.entry("a"); oc.entry("c")
    assert oc.entry("a") is a and oc.stats["evictions"] == 1 and set(oc._d) == {"a", "c"}
