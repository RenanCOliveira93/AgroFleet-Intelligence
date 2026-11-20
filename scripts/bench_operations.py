"""Teste de carga da Etapa 6: operação do cenário A (talhão de 120 ha) ingerida pelo caminho ao vivo.

Toda a telemetria passa por ``Live.process`` (gravação, regras de evento, alertas operacionais e ``operation_status``)
em lotes de ``--batch-s`` segundos de dado. Com ``eval_interval_s = 0`` e um cliente WebSocket simulado, CADA lote
avalia os alertas e monta o status: com lotes de 30 s, é a mesma cadência do tempo real (avaliação a cada 30 s).

Mede, ao longo da operação: tempo de ``process`` por lote, ``GET /status`` com o cache quente, o mesmo status sem cache
(cálculo completo, para comparar) e, no fim, chamadas frias/quentes de status e geometria, tamanho da mensagem e a
igualdade entre o resultado com e sem cache.

Uso:
    PYTHONPATH=. python scripts/bench_operations.py --hours 5            # SQLite em arquivo temporário
    DATABASE_URL=postgresql+psycopg://... PYTHONPATH=. python scripts/bench_operations.py --hours 5
Saída: experiments/bench_operations.json (ou ``--out``).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agrofleet.simulator.fleet import DEFAULT_START  # noqa: E402
from agrofleet.simulator.operations import scenario_a, simulate_operation  # noqa: E402

USERS = {"admin": ("bench-admin", "bench-pass")}
VOLATILE = {"evaluated_at", "connection", "alerts"}


def pct(v):
    v = np.asarray(v, float)
    return {"n": int(len(v)), "p50_s": round(float(np.percentile(v, 50)), 4), "p95_s": round(float(np.percentile(v, 95)), 4),
            "max_s": round(float(v.max()), 4)} if len(v) else None


def timed(f):
    t = time.perf_counter()
    x = f()
    return x, time.perf_counter() - t


class FakeWS:
    """Cliente WebSocket simulado (o laço do app em segundo plano também difunde para ele)."""

    async def send_json(self, m):
        pass


class NoCache:
    ops_cache = None

    def __init__(self, live):
        self.is_live = live.is_live


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=5.0)
    ap.add_argument("--batch-s", type=float, default=30.0)
    ap.add_argument("--full-every-min", type=float, default=30.0, help="cálculo sem cache a cada N min de dado")
    ap.add_argument("--out", default="experiments/bench_operations.json")
    a = ap.parse_args()

    from fastapi.testclient import TestClient
    from backend.app.config import Settings
    from backend.app.main import clean, create_app
    from backend.app.operations_api import operation_state, status_payload
    from tests.test_incremental import same

    url = os.environ.get("DATABASE_URL") or f"sqlite:///{tempfile.mkdtemp()}/bench.db"
    st = Settings(database_url=url, auth_secret="bench-secret-bench-secret-bench-secret-01", users=USERS, mqtt_host=None,
                  status_interval_s=0.0)
    app = create_app(st)
    with TestClient(app) as c:
        h = {"Authorization": "Bearer " + c.post("/auth/login", json={"username": USERS["admin"][0],
                                                                       "password": USERS["admin"][1]}).json()["access_token"]}
        c.delete("/data", params={"origin": "simulated"}, headers=h)
        r = c.post("/demo/operations/load", params={"scenarios": "A", "hours": a.hours, "start": DEFAULT_START.isoformat(),
                                                    "with_telemetry": False}, headers=h)
        assert r.status_code == 200, r.text
        assert c.put("/config/alerts/operational", json={"eval_interval_s": 0}, headers=h).status_code == 200
        live = app.state.live
        live.clients.add(FakeWS())                 # cliente simulado: cada lote monta operation_status

        def status(cached=True):
            lv = live if cached else NoCache(live)
            with app.state.SF() as s:
                stt = operation_state(s, "OP-SIM-A", None, lv, st)
                return clean({k: v for k, v in status_payload(s, stt, st).items() if k not in VOLATILE})

        sim, t_sim = timed(lambda: simulate_operation(scenario_a(), max_seconds=a.hours * 3600))
        pts = sim.points
        print(f"{len(pts)} amostras simuladas em {t_sim:.1f} s; banco: {url.split('://')[0]}")
        proc, warm, full, rows = [], [], [], []
        msg_bytes = []
        n_per = max(int(a.batch_s), 1)
        next_full = a.full_every_min * 60
        for i in range(0, len(pts), n_per):
            batch = pts[i:i + n_per]
            msgs, dt = timed(lambda: live.process(batch, []))
            proc.append(dt)
            msg_bytes += [len(json.dumps(m)) for m in msgs if m["type"] == "operation_status"]
            elapsed = (batch[-1].ts - DEFAULT_START).total_seconds()
            if i % (20 * n_per) == 0:
                _, w = timed(lambda: c.get("/operations/OP-SIM-A/status", headers=h))
                warm.append(w)
            if elapsed >= next_full:
                _, f = timed(lambda: status(cached=False))
                full.append(f)
                rows.append({"data_h": round(elapsed / 3600, 2), "process_s": round(dt, 4), "full_status_s": round(f, 4)})
                print(f"  {elapsed / 3600:4.1f} h de dado: process {dt:.3f} s | status sem cache {f:.2f} s")
                next_full += a.full_every_min * 60
        live.clients.clear()

        # fim: frio x quente, geometria, igualdade
        eq = same(status(), status(cached=False))
        live.ops_cache.clear()
        _, cold = timed(lambda: c.get("/operations/OP-SIM-A/status", headers=h))
        _, warm_end = timed(lambda: c.get("/operations/OP-SIM-A/status", headers=h))
        _, gcold = timed(lambda: c.get("/operations/OP-SIM-A/geometry", headers=h))
        _, gwarm = timed(lambda: c.get("/operations/OP-SIM-A/geometry", headers=h))
        body = c.get("/operations/OP-SIM-A/status", headers=h).json()
        out = {
            "scenario": "A (talhão 120 ha, trator diesel, 1 Hz)", "hours": a.hours, "samples": len(pts),
            "batch_data_s": a.batch_s, "db": url.split("://")[0],
            "machine": {"python": platform.python_version(), "platform": platform.platform(), "cpu": platform.processor()},
            "process_per_batch": pct(proc), "status_warm_http": pct(warm), "status_full_no_cache": pct(full),
            "end": {"status_cold_http_s": round(cold, 3), "status_warm_http_s": round(warm_end, 3),
                    "geometry_cold_http_s": round(gcold, 3), "geometry_warm_http_s": round(gwarm, 3),
                    "worked_ha": body["mission"]["worked_ha"], "progress_pct": body["mission"]["progress_pct"]},
            "operation_status_msg_bytes": pct(msg_bytes) and {"n": len(msg_bytes), "max": int(max(msg_bytes)),
                                                             "p50": int(np.median(msg_bytes))},
            "cached_equals_full": not eq, "differences": eq[:5],
            "ops_cache": live.ops_cache.stats, "live": {k: v for k, v in live.stats.items() if k != "last_error"},
            "timeline": rows,
        }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps({k: out[k] for k in ("process_per_batch", "status_warm_http", "status_full_no_cache", "end",
                                         "operation_status_msg_bytes", "cached_equals_full")}, indent=2, ensure_ascii=False))
    return 0 if out["cached_equals_full"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
