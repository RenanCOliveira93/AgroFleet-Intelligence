"""Modelo canônico de telemetria (schema versionado).

Regras de projeto:
- Todo campo opcional é ``None`` quando ausente. NUNCA é preenchido com valor fictício (nem 0, nem último valor).
- Tempo sempre em UTC com fuso explícito. Timestamp "ingênuo" é rejeitado pelo modelo; o adaptador decide
  (e documenta) o fuso quando a fonte não informa.
- Unidades SI-derivadas e fixas: m/s, bar, L/min, L, m, L/ha; e (v1.1) rpm, %, h, L/h, kWh, kW, graus.
  Conversões acontecem nos adaptadores.
- ``origin`` separa dado simulado de dado real em toda a pilha (banco, API, interface).
- Nada aqui descreve comando de controle: o modelo só descreve o que a máquina REPORTOU.

Histórico do schema (mesmo major => mesmo tópico ``agrofleet/v1``):
- 1.0: posição, estado, GNSS, pulverização (vazão, pressão, tanque de calda, largura, taxa), erros, qualidade.
- 1.1: campos OPCIONAIS de motor e energia (``ENGINE_ENERGY_FIELDS``). Payload 1.0 continua válido sem mudança.
  Atenção: ``tank_level_l`` é o tanque de CALDA (produto), não combustível. Combustível só em ``fuel_level_pct``;
  litros de combustível dependem da capacidade do tanque, que é cadastro da máquina (não telemetria).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.1"


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


SENSORS = ("gnss", "speed", "flow", "pressure", "tank_level",
           "engine", "fuel_level", "fuel_rate", "battery", "heading")   # 1.1: motor/energia/rumo
Origin = Literal["simulated", "real", "unknown"]

# Campos 1.1 (motor e energia). Unidade canônica e referência de origem típica. As referências J1939 indicam de onde
# um dispositivo futuro poderia ler o valor; a disponibilidade varia por fabricante e NÃO foi validada com máquina real.
ENGINE_ENERGY_FIELDS: dict[str, dict] = {
    "engine_rpm":         {"unit": "rpm", "range": (0, None), "ref": "J1939 SPN 190 (Engine Speed)"},
    "engine_load_pct":    {"unit": "%", "range": (0, 250), "ref": "J1939 SPN 92 (Percent Load At Current Speed; faixa 0-250%)"},
    "engine_hours_h":     {"unit": "h", "range": (0, None), "ref": "J1939 SPN 247 (Engine Total Hours); horímetro acumulado"},
    "fuel_level_pct":     {"unit": "%", "range": (0, 100), "ref": "J1939 SPN 96 (Fuel Level 1)"},
    "fuel_rate_lph":      {"unit": "L/h", "range": (0, None), "ref": "J1939 SPN 183 (Engine Fuel Rate)"},
    "battery_soc_pct":    {"unit": "%", "range": (0, 100), "ref": "BMS: estado de carga"},
    "battery_energy_kwh": {"unit": "kWh", "range": (0, None), "ref": "BMS: energia remanescente disponível"},
    "battery_power_kw":   {"unit": "kW", "range": (None, None), "ref": "BMS: potência na bateria (+ descarga, - recarga/regeneração)"},
    "heading_deg":        {"unit": "°", "range": (0, 360), "ref": "GNSS: rumo verdadeiro sobre o solo (COG), [0, 360)"},
}


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

    # 1.1 — motor e energia (ver ENGINE_ENERGY_FIELDS). Ausente = None; nunca estimado pelo sistema.
    engine_rpm: float | None = Field(default=None, ge=0)
    engine_load_pct: float | None = Field(default=None, ge=0, le=250)
    engine_hours_h: float | None = Field(default=None, ge=0)
    fuel_level_pct: float | None = Field(default=None, ge=0, le=100)
    fuel_rate_lph: float | None = Field(default=None, ge=0)
    battery_soc_pct: float | None = Field(default=None, ge=0, le=100)
    battery_energy_kwh: float | None = Field(default=None, ge=0)
    battery_power_kw: float | None = None
    heading_deg: float | None = Field(default=None, ge=0, lt=360)

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
                     "consumption_l", "working_width_m", "application_rate_lha", *ENGINE_ENERGY_FIELDS)
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
