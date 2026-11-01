"""Publica a frota SIMULADA no broker MQTT (agrofleet/v1/<machine>/telemetry), com timestamps a partir de AGORA.

Uso: python simulator/publish.py --host localhost --user $MQTT_DEVICE_USER --password-env MQTT_DEVICE_PASSWORD --speedup 20
--speedup N publica N vezes mais rápido que o tempo real (os timestamps continuam avançando 1 s por amostra, então
ficam adiante do relógio quando N > 1: é um relógio simulado). O processo só publica; não lê nem envia comandos.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agrofleet.adapters.mqtt import MqttPublisher  # noqa: E402
from agrofleet.simulator.fleet import simulate  # noqa: E402


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
    start = datetime.now(timezone.utc).replace(microsecond=0)
    while a.repeat == 0 or n_run < a.repeat:
        pts, _ = simulate(seed=a.seed + n_run, start=start, max_seconds=a.max_seconds)
        t0 = time.monotonic()
        base = pts[0].ts
        for i, p in enumerate(pts):
            due = (p.ts - base).total_seconds() / a.speedup
            delay = due - (time.monotonic() - t0)
            if delay > 0:
                time.sleep(delay)
            pub.publish_point(p)
            if i % 2000 == 0:
                print(f"[sim run {n_run}] {i}/{len(pts)} publicados", flush=True)
        n_run += 1
        start = pts[-1].ts + timedelta(seconds=60)
    time.sleep(1)
    pub.close()
    print("fim", flush=True)


if __name__ == "__main__":
    main()
