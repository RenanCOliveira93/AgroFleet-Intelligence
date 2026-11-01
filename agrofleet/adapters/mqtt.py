"""Adaptador MQTT: assina ``agrofleet/v1/<machine_id>/telemetry`` (envelope versionado) e publica (simulador).

- Mensagem inválida, de versão incompatível ou com machine_id diferente do tópico é REJEITADA e contada (não corrigida).
- TLS opcional via ``cafile`` (verificação de certificado sempre ligada; não há opção para desativá-la).
- Este módulo só RECEBE/PUBLICA telemetria. Não existe tópico nem função de comando para máquinas.
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Callable

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from ..schema import TOPIC_PREFIX, TelemetryPoint, telemetry_topic, unwrap, wrap

SUB_TOPIC = f"{TOPIC_PREFIX}/+/telemetry"


def _client(client_id: str, username, password, tls_cafile, tls: bool):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if username:
        c.username_pw_set(username, password)
    if tls or tls_cafile:
        c.tls_set(ca_certs=tls_cafile)  # exige certificado válido; sem tls_insecure_set
    return c


def parse_message(topic: str, payload: bytes) -> TelemetryPoint:
    """Valida tópico + envelope + ponto. Levanta ValueError com o motivo."""
    parts = topic.split("/")
    if len(parts) != 4 or "/".join(parts[:2]) != TOPIC_PREFIX or parts[3] != "telemetry":
        raise ValueError(f"tópico fora do padrão {TOPIC_PREFIX}/<machine_id>/telemetry: {topic}")
    try:
        msg = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"payload não é JSON: {e}")
    try:
        p = unwrap(msg)
    except ValidationError as e:
        raise ValueError("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())[:300])
    if p.machine_id != parts[2]:
        raise ValueError(f"machine_id do payload ({p.machine_id}) difere do tópico ({parts[2]})")
    return p


class MqttSource:
    def __init__(self, host: str, port: int = 1883, *, username=None, password=None, tls=False, cafile=None,
                 on_point: Callable[[TelemetryPoint], None], on_reject: Callable[[str, str], None] | None = None,
                 client_id: str | None = None, topic: str = SUB_TOPIC):
        self.host, self.port, self.topic = host, port, topic
        self.on_point, self.on_reject = on_point, on_reject
        self.stats = {"received": 0, "accepted": 0, "rejected": 0}
        self.connected = threading.Event()
        self._c = _client(client_id or f"agrofleet-ingest-{uuid.uuid4().hex[:8]}", username, password, cafile, tls)
        self._c.on_connect = self._on_connect
        self._c.on_disconnect = lambda *a, **k: self.connected.clear()
        self._c.on_message = self._on_message
        self._c.reconnect_delay_set(1, 30)

    def _on_connect(self, c, ud, flags, rc, props=None):
        if rc == 0:
            c.subscribe(self.topic, qos=1)
            self.connected.set()

    def _on_message(self, c, ud, m):
        self.stats["received"] += 1
        try:
            p = parse_message(m.topic, m.payload)
        except ValueError as e:
            self.stats["rejected"] += 1
            if self.on_reject:
                self.on_reject(m.topic, str(e))
            return
        self.stats["accepted"] += 1
        self.on_point(p)

    def start(self, wait_s: float = 10.0) -> bool:
        self._c.connect_async(self.host, self.port, keepalive=30)
        self._c.loop_start()
        return self.connected.wait(wait_s)

    def stop(self):
        self._c.loop_stop()
        self._c.disconnect()


class MqttPublisher:
    def __init__(self, host: str, port: int = 1883, *, username=None, password=None, tls=False, cafile=None, client_id=None):
        self._c = _client(client_id or f"agrofleet-pub-{uuid.uuid4().hex[:8]}", username, password, cafile, tls)
        self._c.connect(host, port, keepalive=30)
        self._c.loop_start()

    def publish_point(self, p: TelemetryPoint, qos: int = 1):
        info = self._c.publish(telemetry_topic(p.machine_id), json.dumps(wrap(p, uuid.uuid4().hex)), qos=qos)
        return info

    def publish_raw(self, topic: str, payload: str | bytes, qos: int = 1):
        return self._c.publish(topic, payload, qos=qos)

    def close(self):
        self._c.loop_stop()
        self._c.disconnect()
