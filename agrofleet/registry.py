"""Cadastro: propriedades, perfis de máquina e operações (missões).

Cadastro é CONFIGURAÇÃO informada por pessoas, não telemetria: capacidade do tanque, reserva operacional, área planejada
e limites de alerta não são medidos pela máquina. Por isso ficam aqui, separados do ``TelemetryPoint``.

Regras:
- Campo não informado fica ``None`` e o cálculo que depende dele é marcado como indisponível (nunca há default oculto).
  Ex.: sem ``fuel_tank_capacity_l`` não existem "litros restantes"; sem limites de energia, o alerta de nível baixo
  não roda (e isso é relatado), em vez de usar um limite inventado.
- ``origin`` também vale para o cadastro: uma operação só pode ligar máquina e talhão da MESMA origem, para que dado
  simulado nunca se misture a dado real.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schema import Origin

MachineKind = Literal["tractor", "sprayer", "harvester", "other"]
EnergySource = Literal["diesel", "electric", "unknown"]
OperationType = Literal["soil_preparation", "planting", "spraying", "harvesting", "other"]
OperationStatus = Literal["planned", "active", "completed", "cancelled"]

KIND_LABEL = {"tractor": "Trator", "sprayer": "Pulverizador", "harvester": "Colheitadeira", "other": "Outro"}
ENERGY_LABEL = {"diesel": "Diesel", "electric": "Elétrico", "unknown": "Não informado"}
OPERATION_LABEL = {"soil_preparation": "Preparo de solo", "planting": "Plantio", "spraying": "Pulverização",
                   "harvesting": "Colheita", "other": "Outra"}
STATUS_LABEL = {"planned": "Planejada", "active": "Em andamento", "completed": "Concluída", "cancelled": "Cancelada"}

_ID = dict(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PropertyInfo(_Base):
    property_id: str = Field(**_ID)
    name: str = Field(min_length=1, max_length=128)
    timezone: str = "America/Sao_Paulo"     # usado só para EXIBIR horários; o armazenamento é sempre UTC
    origin: Origin = "unknown"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"fuso horário desconhecido: {v!r} (use um nome IANA, ex.: America/Sao_Paulo)")
        return v


class MachineProfile(_Base):
    machine_id: str = Field(**_ID)
    name: str = Field(min_length=1, max_length=128)
    kind: MachineKind = "other"
    energy_source: EnergySource = "unknown"
    autonomous: bool = False
    property_id: str | None = Field(default=None, **_ID)
    origin: Origin = "unknown"
    # energia (somente a da fonte declarada)
    fuel_tank_capacity_l: float | None = Field(default=None, gt=0, le=5000)
    battery_capacity_kwh: float | None = Field(default=None, gt=0, le=2000)
    # reserva operacional: fração da capacidade que NÃO conta como disponível para a missão
    energy_reserve_pct: float | None = Field(default=None, ge=0, le=50)
    # limites de alerta de nível (Etapa 5); critical < warning
    energy_warning_pct: float | None = Field(default=None, ge=0, le=100)
    energy_critical_pct: float | None = Field(default=None, ge=0, le=100)
    # margem exigida da energia disponível sobre a necessidade estimada para concluir (Etapa 4)
    autonomy_margin_pct: float | None = Field(default=None, ge=0, le=200)
    # manutenção preventiva: só gera alerta com AMBOS informados (regra configurada + horímetro de referência)
    maintenance_interval_h: float | None = Field(default=None, gt=0, le=10000)
    last_maintenance_engine_h: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _coherence(self) -> "MachineProfile":
        if self.energy_source == "diesel" and self.battery_capacity_kwh is not None:
            raise ValueError("máquina diesel não aceita battery_capacity_kwh")
        if self.energy_source == "electric" and self.fuel_tank_capacity_l is not None:
            raise ValueError("máquina elétrica não aceita fuel_tank_capacity_l")
        if self.energy_source == "electric" and (self.maintenance_interval_h is not None or self.last_maintenance_engine_h is not None):
            raise ValueError("manutenção por horímetro de motor não se aplica a máquina elétrica")
        if self.energy_source == "unknown" and (self.fuel_tank_capacity_l is not None or self.battery_capacity_kwh is not None):
            raise ValueError("informe energy_source (diesel|electric) antes da capacidade")
        w, c = self.energy_warning_pct, self.energy_critical_pct
        if w is not None and c is not None and not c < w:
            raise ValueError("energy_critical_pct deve ser menor que energy_warning_pct")
        r = self.energy_reserve_pct
        if r is not None and c is not None and c < r:
            raise ValueError("energy_critical_pct não pode ficar abaixo da reserva operacional (energy_reserve_pct)")
        if (self.maintenance_interval_h is None) != (self.last_maintenance_engine_h is None):
            raise ValueError("manutenção: informe maintenance_interval_h E last_maintenance_engine_h (ou nenhum)")
        return self

    @property
    def energy_capacity(self) -> tuple[float | None, str | None]:
        """(capacidade, unidade) da fonte declarada; (None, None) se não cadastrada."""
        if self.energy_source == "diesel":
            return self.fuel_tank_capacity_l, "L"
        if self.energy_source == "electric":
            return self.battery_capacity_kwh, "kWh"
        return None, None


class OperationPlan(_Base):
    operation_id: str = Field(**_ID)
    name: str = Field(min_length=1, max_length=128)
    operation_type: OperationType = "other"
    machine_id: str = Field(**_ID)
    field_id: str = Field(**_ID)
    property_id: str | None = Field(default=None, **_ID)
    # área a trabalhar; None = a área do talhão (a API informa a fonte usada, nunca a esconde)
    planned_area_ha: float | None = Field(default=None, gt=0, le=100000)
    # largura do implemento cadastrada; usada só se a telemetria não reportar working_width_m (com proveniência)
    implement_width_m: float | None = Field(default=None, gt=0, le=60)
    start: datetime
    end: datetime | None = None
    status: OperationStatus = "planned"
    origin: Origin = "unknown"

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return v
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("horário sem fuso (esperado UTC explícito)")
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _times(self) -> "OperationPlan":
        if self.end is not None and self.end <= self.start:
            raise ValueError("end deve ser posterior a start")
        if self.status == "completed" and self.end is None:
            raise ValueError("operação concluída exige end")
        return self


def check_operation_links(op: OperationPlan, machine: MachineProfile | None, field_origin: str | None) -> None:
    """Valida vínculos de uma operação. Levanta ValueError com o motivo."""
    if machine is None:
        raise ValueError(f"máquina {op.machine_id!r} não cadastrada")
    if field_origin is None:
        raise ValueError(f"talhão {op.field_id!r} não cadastrado")
    if not (op.origin == machine.origin == field_origin):
        raise ValueError(f"origens diferentes (operação={op.origin}, máquina={machine.origin}, talhão={field_origin}): "
                         "dado simulado e real não se misturam")
    if op.property_id and machine.property_id and op.property_id != machine.property_id:
        raise ValueError("operação e máquina pertencem a propriedades diferentes")
