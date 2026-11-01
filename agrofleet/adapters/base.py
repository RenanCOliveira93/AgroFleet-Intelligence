from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import TelemetryPoint


class AdapterError(Exception):
    """Falha que impede a leitura da fonte inteira (formato irreconhecível, versão incompatível...)."""


class NotImplementedAdapter(AdapterError):
    """Adaptador deliberadamente não implementado (sem amostra válida/documentação para validar)."""


@dataclass
class AdapterResult:
    points: list[TelemetryPoint] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)     # amostra limitada das linhas rejeitadas
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    n_input: int = 0
    n_rejected_total: int = 0
    reject_cap: int = 200

    def reject(self, row, reason, raw=None):
        self.n_rejected_total += 1
        if len(self.rejected) < self.reject_cap:
            self.rejected.append({"row": row, "reason": reason, "raw": raw})
