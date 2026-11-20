"""Publica telemetria SIMULADA no broker MQTT (agrofleet/v1/<machine>/telemetry), com timestamps a partir de AGORA.

Uso: python simulator/publish.py --host localhost --user $MQTT_DEVICE_USER --password-env MQTT_DEVICE_PASSWORD --speedup 20
--speedup N publica N vezes mais rápido que o tempo real (os timestamps continuam avançando 1 s por amostra, então
ficam adiante do relógio quando N > 1: é um relógio simulado). O processo só publica; não lê nem envia comandos.

--scenario fleet (padrão) publica a frota original de 4 pulverizadores. --scenario A,B,C publica as operações da
Etapa 3; no cenário C a entrega segue a agenda de falhas de enlace (perda, reenvio em rajada, latência, duplicação).
Com --start no PASSADO (e --speedup 1), o que já "aconteceu" até agora é publicado de imediato (atraso acumulado) e o
restante segue o relógio real: a operação aparece em andamento desde o primeiro minuto.
Para que as operações apareçam com cadastro, registre-as antes com o MESMO início:
  POST /demo/operations/load?scenarios=A,B&with_telemetry=false&start=<início impresso por este script>
"""
import argparse
import heapq
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agrofleet.adapters.mqtt import MqttPublisher  # noqa: E402
from agrofleet.simulator.fleet import simulate  # noqa: E402
from agrofleet.simulator.operations import run_scenarios  # noqa: E402


def schedule(scenario: str, seed: int, start: datetime, max_seconds: int):
    """Lista ordenada de (atraso_de_entrega_s, ponto)."""
    if scenario == "fleet":
        pts, _ = simulate(seed=seed, start=start, max_seconds=max_seconds)
        base = pts[0].ts
        return [((p.ts - base).total_seconds(), p) for p in pts]
    streams = []
    for r in run_scenarios([k.strip().upper() for k in scenario.split(",")], seed=seed, start=start, max_seconds=max_seconds):
        if "_deliveries" in r.truth:
            streams.append([(d.deliver_s, d.point) for d in r.truth["_deliveries"]])
        else:
            streams.append([((p.ts - start).total_seconds(), p) for p in r.points])
    return list(heapq.merge(*streams, key=lambda x: x[0]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    ap.add_argument("--user", default=os.environ.get("MQTT_USER"))
    ap.add_argument("--password-env", default="MQTT_PASSWORD")
    ap.add_argument("--cafile", default=os.environ.get("MQTT_CAFILE"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--speedup", type=float, default=1.0)
    ap.add_argument("--repeat", type=int, default=1, help="repete com sementes seed, seed+1... (0 = infinito)")
    ap.add_argument("--max-seconds", type=int, default=3 * 3600)
    ap.add_argument("--scenario", default="fleet", help="fleet | lista de A,B,C")
    ap.add_argument("--start", default=None, help="início ISO-8601 com fuso (padrão: agora)")
    a = ap.parse_args()
    pub = None
    for attempt in range(30):     # broker pode estar subindo
        try:
            pub = MqttPublisher(a.host, a.port, username=a.user, password=os.environ.get(a.password_env), cafile=a.cafile)
            break
        except OSError as e:
            print(f"broker indisponível ({e}); tentando de novo...", flush=True)
            time.sleep(2)
    if pub is None:
        sys.exit("não foi possível conectar ao broker")
    n_run = 0
    start = datetime.fromisoformat(a.start) if a.start else datetime.now(timezone.utc).replace(microsecond=0)
    if start.tzinfo is None:
        sys.exit("--start precisa de fuso (ex.: 2026-05-01T12:00:00+00:00)")
    print(f"cenário={a.scenario} início={start.isoformat()} (dados SIMULADOS)", flush=True)
    while a.repeat == 0 or n_run < a.repeat:
        plan = schedule(a.scenario, a.seed + n_run, start, a.max_seconds)
        # início no passado (só em tempo real): o atraso acumulado sai de imediato, o resto segue o relógio
        behind_s = max((datetime.now(timezone.utc) - start).total_seconds(), 0.0) if a.speedup == 1 else 0.0
        if behind_s > 0:
            print(f"início {behind_s / 60:.0f} min no passado: publicando o atraso acumulado de imediato", flush=True)
        t0 = time.monotonic() - behind_s
        for i, (due_s, p) in enumerate(plan):
            delay = due_s / a.speedup - (time.monotonic() - t0)
            if delay > 0:
                time.sleep(delay)
            pub.publish_point(p)
            if i % 2000 == 0:
                print(f"[sim run {n_run}] {i}/{len(plan)} publicados", flush=True)
        n_run += 1
        start = max(p.ts for _, p in plan) + timedelta(seconds=60)
    time.sleep(1)
    pub.close()
    print("fim", flush=True)


if __name__ == "__main__":
    main()
