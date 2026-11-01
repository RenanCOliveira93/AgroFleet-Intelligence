"""Modelo canônico de telemetria (schema versionado).

Regras de projeto:
- Todo campo opcional é ``None`` quando ausente. NUNCA é preenchido com valor fictício (nem 0, nem último valor).
- Tempo sempre em UTC com fuso explícito. Timestamp "ingênuo" é rejeitado pelo modelo; o adaptador decide
  (e documenta) o fuso quando a fonte não informa.
- Unidades SI-derivadas e fixas: m/s, bar, L/min, L, m, L/ha. Conversões acontecem nos adaptadores.
- ``origin`` separa dado simulado de dado real em toda a pilha (banco, API, interface).
- Nada aqui descreve comando de controle: o modelo só descreve o que a máquina REPORTOU.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"


class State(str, Enum):
    working = "working"      # aplicando produto
    transport = "transport"  # deslocando sem aplicar
    idle = "idle"            # ligada, parada, sem aplicar
    refilling = "refilling"  # reabastecendo tanque
    fault = "fault"          # parada por falha reportada
    off = "off"
    unknown = "unknown"


class Gnss(str, Enum):
    none = "none"
    single = "single"
    dgps = "dgps"
    rtk_float = "rtk_float"
    rtk_fixed = "rtk_fixed"
    unknown = "unknown"


class SensorQ(str, Enum):
    ok = "ok"
    degraded = "degraded"
    failed = "failed"
    unknown = "unknown"


SENSORS = ("gnss", "speed", "flow", "pressure", "tank_level")
Origin = Literal["simulated", "real", "unknown"]


class TelemetryPoint(BaseModel):
    """Uma amostra de telemetria de uma máquina (o que a máquina reportou, não o que é verdade)."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    machine_id: str = Field(min_length=1, max_length=64)
    ts: datetime
    origin: Origin = "unknown"

    lat: float | None = None
    lon: float | None = None
    speed_ms: float | None = Field(default=None, ge=0)
    state: State = State.unknown
    gnss_fix: Gnss = Gnss.unknown
    accuracy_m: float | None = Field(default=None, ge=0)  # precisão horizontal reportada pelo receptor

    flow_lpm: float | None = Field(default=None, ge=0)
    pressure_bar: float | None = Field(default=None, ge=0)
    tank_level_l: float | None = Field(default=None, ge=0)
    consumption_l: float | None = Field(default=None, ge=0)  # contador acumulado reportado (pode zerar)
    working_width_m: float | None = Field(default=None, gt=0)
    application_rate_lha: float | None = Field(default=None, ge=0)  # taxa reportada pelo controlador
    error_codes: list[str] = Field(default_factory=list)
    sensor_quality: dict[str, SensorQ] = Field(default_factory=dict)

    # metadados opcionais para relatórios (nunca inferidos pelo sistema)
    property_id: str | None = None
    field_id: str | None = None
    operator_id: str | None = None

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("timestamp sem fuso horário (esperado UTC explícito)")
        return v.astimezone(timezone.utc)

    @field_validator("lat", "lon", "speed_ms", "accuracy_m", "flow_lpm", "pressure_bar", "tank_level_l",
                     "consumption_l", "working_width_m", "application_rate_lha")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("valor não finito (NaN/Inf)")
        return v

    @field_validator("sensor_quality")
    @classmethod
    def _sensors(cls, v: dict[str, SensorQ]) -> dict[str, SensorQ]:
        bad = set(v) - set(SENSORS)
        if bad:
            raise ValueError(f"sensor desconhecido em sensor_quality: {sorted(bad)}")
        return v

    @model_validator(mode="after")
    def _geo(self) -> "TelemetryPoint":
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat e lon devem vir juntos (ambos ou nenhum)")
        if self.lat is not None:
            if not -90 <= self.lat <= 90 or not -180 <= self.lon <= 180:
                raise ValueError("coordenada fora do intervalo válido")
            if self.lat == 0 and self.lon == 0:
                raise ValueError("coordenada (0,0) rejeitada: valor-padrão típico de receptor sem fix")
        return self

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None


# Envelope MQTT versionado -------------------------------------------------------------------------------------

TOPIC_PREFIX = f"agrofleet/v{SCHEMA_VERSION.split('.')[0]}"


def telemetry_topic(machine_id: str) -> str:
    return f"{TOPIC_PREFIX}/{machine_id}/telemetry"


class Envelope(BaseModel):
    """Envelope de evento MQTT. ``schema_version`` maior que o suportado é rejeitado (não adivinhamos)."""

    model_config = ConfigDict(extra="forbid")
    schema_version: str
    type: Literal["telemetry"]
    message_id: str
    sent_at: datetime
    data: dict

    @field_validator("schema_version")
    @classmethod
    def _major(cls, v: str) -> str:
        if v.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
            raise ValueError(f"versão de schema incompatível: {v} (suportado: {SCHEMA_VERSION}.x)")
        return v


def wrap(point: TelemetryPoint, message_id: str, sent_at: datetime | None = None) -> dict:
    return {"schema_version": SCHEMA_VERSION, "type": "telemetry", "message_id": message_id,
            "sent_at": (sent_at or point.ts).isoformat(), "data": point_to_dict(point)}


def point_to_dict(p: TelemetryPoint) -> dict:
    d = p.model_dump(mode="json")
    return d


def unwrap(msg: dict) -> TelemetryPoint:
    env = Envelope.model_validate(msg)
    return TelemetryPoint.model_validate(env.data)
