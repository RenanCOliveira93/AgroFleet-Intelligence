"""Cache por operação no processo da API (Etapa 6).

Três camadas, todas verificadas contra o dado (nunca servem resultado velho):
- **quadro de telemetria**: guarda as amostras da operação e, a cada chamada, lê do banco só as linhas gravadas desde
  a última leitura (``ingested_at``). Confere pela contagem de linhas da operação: se não bater (dado apagado ou
  gravação concorrente com horário anterior), relê tudo. As linhas de telemetria nunca são alteradas depois de
  gravadas (``ON CONFLICT DO NOTHING``), então ler só as novas basta.
- **missão incremental** (``agrofleet.analytics.incremental.MissionCache``): uma para o presente e outra para
  avaliações em instantes passados (``as_of``, reprodução de alertas), para que uma auditoria não descarte o cache ao vivo.
- **geometria para o mapa**: a resposta de ``/geometry`` é guardada enquanto o dado e os parâmetros forem os mesmos.

Memória limitada a ``max_ops`` operações (LRU). O cache é do processo: com várias réplicas da API cada uma tem o seu
(implantação atual: uma réplica).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import timedelta

import pandas as pd
from sqlalchemy import func, select

from agrofleet.analytics.incremental import MissionCache

from .db import TelemetryRow, load_frame

SKEW_S = 5.0          # folga da leitura incremental: lotes gravados quase ao mesmo tempo, relógio do servidor


class OpEntry:
    def __init__(self):
        self.lock = threading.RLock()
        self.fkey = None
        self.df: pd.DataFrame | None = None
        self.n = 0
        self.max_ing = None
        self.live = MissionCache()
        self.past = MissionCache()
        self.geom: tuple | None = None


class OperationCache:
    def __init__(self, max_ops: int = 32):
        self.max_ops = max(int(max_ops), 1)
        self._d: OrderedDict[str, OpEntry] = OrderedDict()
        self._lock = threading.Lock()
        self.stats = {"frame_hits": 0, "frame_deltas": 0, "frame_full": 0, "evictions": 0, "geometry_hits": 0}

    def entry(self, operation_id: str) -> OpEntry:
        with self._lock:
            e = self._d.get(operation_id)
            if e is None:
                e = self._d[operation_id] = OpEntry()
                while len(self._d) > self.max_ops:
                    self._d.popitem(last=False)
                    self.stats["evictions"] += 1
            else:
                self._d.move_to_end(operation_id)
            return e

    def clear(self):
        with self._lock:
            self._d.clear()

    def summary(self) -> dict:
        with self._lock:
            ops = {k: {"rows": e.n, "mission_live": dict(e.live.stats), "mission_past": dict(e.past.stats)}
                   for k, e in self._d.items()}
        return {**self.stats, "operations": ops, "max_ops": self.max_ops}

    def frame(self, s, op) -> pd.DataFrame:
        """Telemetria da operação inteira (início → fim cadastrado, mesma máquina e origem)."""
        e = self.entry(op.operation_id)
        key = (op.machine_id, op.origin, op.start, op.end)
        w = [TelemetryRow.machine_id == op.machine_id, TelemetryRow.origin == op.origin, TelemetryRow.ts >= op.start]
        if op.end is not None:
            w.append(TelemetryRow.ts <= op.end)
        n, mx = s.execute(select(func.count(), func.max(TelemetryRow.ingested_at)).where(*w)).one()
        with e.lock:
            if e.fkey == key and e.df is not None and e.n == n and e.max_ing == mx:
                self.stats["frame_hits"] += 1
                return e.df
            if e.fkey == key and e.df is not None and mx is not None and e.max_ing is not None and n > e.n:
                d = load_frame(s, [op.machine_id], op.start, op.end, op.origin, ingested_since=e.max_ing - timedelta(seconds=SKEW_S))
                merged = (pd.concat([e.df, d], ignore_index=True) if len(d) else e.df) \
                    .drop_duplicates(subset=["ts"], keep="first").sort_values("ts", kind="stable").reset_index(drop=True)
                if len(merged) == n:
                    e.df, e.n, e.max_ing = merged, n, mx
                    self.stats["frame_deltas"] += 1
                    return merged
            df = load_frame(s, [op.machine_id], op.start, op.end, op.origin)
            e.fkey, e.df, e.n, e.max_ing, e.geom = key, df, len(df), mx, None
            self.stats["frame_full"] += 1
            return df
