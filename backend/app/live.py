"""Caminho de streaming: MQTT -> fila -> lote -> banco -> regras de alerta -> WebSocket.

Etapa 5: além das regras de evento, avalia os alertas operacionais (energia, autonomia, manutenção) das máquinas com
operação ativa, no máximo a cada ``eval_interval_s`` por máquina, e roda um vigia de conexão a cada
``WATCHDOG_S`` segundos mesmo sem mensagens (uma máquina que para de enviar não dispara nada pela ingestão).

Etapa 6: com cliente WebSocket conectado, envia ``operation_status`` (o corpo de ``GET /operations/{id}/status``) da
operação corrente de cada máquina, no máximo a cada ``status_interval_s``; também no ciclo do vigia, porque o estado
da conexão muda com o tempo sem dado novo. Status, alertas e WebSocket usam o mesmo cálculo incremental (``ops_cache``).

Fila em memória (queue.Queue): se o processo cair, mensagens ainda não gravadas se perdem; o QoS 1 do MQTT só protege
até o ACK do cliente, não até o commit no banco (limitação documentada em docs/GUIA_TECNICO.md, seção 10).
"""
from __future__ import annotations

import asyncio
import queue
import time
from dataclasses import fields as dc_fields
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from agrofleet.analytics import anomalies as A
from agrofleet.analytics.frame import to_frame
from agrofleet.schema import ENGINE_ENERGY_FIELDS, TelemetryPoint

from .db import AlertRow, RejectRow, get_setting, insert_points, load_frame
from .ops_cache import OperationCache

KEEP_REJECTS = 500
WATCHDOG_S = 30.0


def alert_config(session) -> A.AnomalyConfig:
    saved = get_setting(session, "alert_config", {}) or {}
    ok = {f.name for f in dc_fields(A.AnomalyConfig)}
    kw = {k: (tuple(v) if k in ("pressure_band_bar", "speed_band_ms") and v else v) for k, v in saved.items() if k in ok}
    return A.AnomalyConfig(**kw)


def upsert_alert(session, a: A.Alert, tol_s: float = 15.0) -> tuple[str, bool]:
    """Funde com alerta existente da mesma máquina/tipo que se sobreponha (evita duplicar evento em andamento)."""
    d = a.to_dict()
    tol = timedelta(seconds=tol_s)
    ex = session.execute(select(AlertRow).where(AlertRow.machine_id == a.machine_id, AlertRow.type == a.type, AlertRow.rule == a.rule,
                                                 AlertRow.end >= a.start.to_pydatetime() - tol,
                                                 AlertRow.start <= a.end.to_pydatetime() + tol)).scalars().first()
    if ex:
        changed = a.end.to_pydatetime() > ex.end or a.start.to_pydatetime() < ex.start
        ex.start = min(ex.start, a.start.to_pydatetime()); ex.end = max(ex.end, a.end.to_pydatetime())
        if changed:
            ex.message, ex.evidence = d["message"], d["evidence"]
        return ex.id, changed
    session.add(AlertRow(id=d["id"], machine_id=a.machine_id, type=a.type, severity=a.severity, start=a.start.to_pydatetime(),
                         end=a.end.to_pydatetime(), message=d["message"], evidence=d["evidence"], rule=a.rule, origin=a.origin,
                         experimental=a.experimental))
    return d["id"], True


def alert_to_dict(r: AlertRow) -> dict:
    return {"id": r.id, "machine_id": r.machine_id, "type": r.type, "severity": r.severity, "start": r.start.isoformat(),
            "end": r.end.isoformat(), "duration_s": (r.end - r.start).total_seconds(), "message": r.message, "evidence": r.evidence,
            "rule": r.rule, "origin": r.origin, "experimental": r.experimental, "informational": True, "note": A.INFO_NOTE}


def point_brief(p: TelemetryPoint) -> dict:
    """Resumo enviado pelo WebSocket. Campos 1.1 são ADITIVOS (clientes antigos ignoram) e vão como null se não reportados."""
    return {"machine_id": p.machine_id, "ts": p.ts.isoformat(), "lat": p.lat, "lon": p.lon, "speed_ms": p.speed_ms, "state": p.state.value,
            "gnss_fix": p.gnss_fix.value, "flow_lpm": p.flow_lpm, "pressure_bar": p.pressure_bar, "tank_level_l": p.tank_level_l,
            "origin": p.origin, **{f: getattr(p, f) for f in ENGINE_ENERGY_FIELDS}}


class Live:
    def __init__(self, session_factory):
        self.sf = session_factory
        self.q: queue.Queue = queue.Queue(maxsize=200_000)
        self.rejects: queue.Queue = queue.Queue()
        self.clients: set = set()
        self.last_seen: dict[str, float] = {}
        self.stats = {"queued": 0, "stored": 0, "dropped_queue_full": 0, "batches": 0, "ops_evaluations": 0, "watchdog_runs": 0,
                      "status_messages": 0}
        self._task: asyncio.Task | None = None
        self.settings = None                      # definido por create_app (necessário aos alertas operacionais)
        self.ops_cache = OperationCache()         # Etapa 6: quadro e missão incrementais por operação
        self._last_ops_eval: dict[tuple[str, str], float] = {}
        self._last_status: dict[tuple[str, str], float] = {}
        self._last_watchdog = time.monotonic()

    # chamado pela thread do MQTT
    def push(self, p: TelemetryPoint):
        try:
            self.q.put_nowait(p)
            self.stats["queued"] += 1
        except queue.Full:
            self.stats["dropped_queue_full"] += 1

    def push_reject(self, topic: str, reason: str):
        self.rejects.put((topic, reason))

    def is_live(self, machine_id: str, window_s: int) -> bool:
        t = self.last_seen.get(machine_id)
        return t is not None and time.monotonic() - t <= window_s

    async def start(self):
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self):
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(0.5)
            batch: list[TelemetryPoint] = []
            while len(batch) < 5000:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            rej = []
            while True:
                try:
                    rej.append(self.rejects.get_nowait())
                except queue.Empty:
                    break
            msgs: list[dict] = []
            if time.monotonic() - self._last_watchdog >= WATCHDOG_S and self.settings is not None:
                self._last_watchdog = time.monotonic()
                try:
                    msgs += await loop.run_in_executor(None, self.run_watchdog)
                except Exception as e:
                    self.stats["last_error"] = repr(e)
            if batch or rej:
                try:
                    msgs += await loop.run_in_executor(None, self.process, batch, rej)
                except Exception as e:  # nunca derrubar o laço de ingestão
                    self.stats["last_error"] = repr(e)
            for m in msgs:
                await self.broadcast(m)

    def process(self, batch: list[TelemetryPoint], rej: list[tuple[str, str]]) -> list[dict]:
        out: list[dict] = []
        with self.sf() as s:
            for topic, reason in rej:
                s.add(RejectRow(ts=datetime.now(timezone.utc), source="mqtt", topic=topic[:250], reason=reason))
            if rej:
                s.commit()
                old = s.execute(select(RejectRow.id).order_by(RejectRow.id.desc()).offset(KEEP_REJECTS)).scalars().all()
                if old:
                    s.query(RejectRow).filter(RejectRow.id.in_(old)).delete(synchronize_session=False)
                    s.commit()
            if not batch:
                return out
            self.stats["stored"] += insert_points(s, batch)
            self.stats["batches"] += 1
            now = time.monotonic()
            last: dict[str, TelemetryPoint] = {}
            for p in batch:
                self.last_seen[p.machine_id] = now
                if p.machine_id not in last or p.ts >= last[p.machine_id].ts:
                    last[p.machine_id] = p
            out.append({"type": "telemetry", "points": [point_brief(p) for p in last.values()]})
            cfg = alert_config(s)
            cfg.use_iforest = False      # no streaming só regras (o modelo multivariado roda sob demanda)
            for mid, p in last.items():
                df = load_frame(s, [mid], start=p.ts - timedelta(seconds=self.window_s(s)), end=p.ts, origin=p.origin)
                for a in A.detect(df, cfg).alerts:
                    aid, changed = upsert_alert(s, a)
                    if changed:
                        out.append({"type": "alert", "alert": {**a.to_dict(), "id": aid, "category": "anomaly"}})
                s.commit()
            out += self.evaluate_operations(s, last)
        return out

    def evaluate_operations(self, s, last: dict) -> list[dict]:
        """Operação corrente de cada máquina do lote: alertas operacionais (no máximo a cada ``eval_interval_s``) e
        mensagem ``operation_status`` (no máximo a cada ``status_interval_s``, só com cliente conectado). Um único
        cálculo da missão, incremental, serve aos dois."""
        if self.settings is None:
            return []
        from . import op_alerts_service as OPS
        from .operations_api import operation_state
        out = []
        every = OPS.ops_config(s).eval_interval_s
        st_every = self.settings.status_interval_s
        now = time.monotonic()
        for mid, p in last.items():
            key = (mid, p.origin)
            need_eval = now - self._last_ops_eval.get(key, -1e9) >= every
            need_status = bool(self.clients) and now - self._last_status.get(key, -1e9) >= st_every
            if not (need_eval or need_status):
                continue
            if need_eval:
                self._last_ops_eval[key] = now
            if need_status:
                self._last_status[key] = now
            cur = OPS.current_operations(s, p.origin, mid).get((p.origin, mid))
            if cur is None:                                  # só a operação corrente é dona do estado ao vivo
                continue
            state = operation_state(s, cur.id, None, self, self.settings)
            if need_eval:
                trs, _sk, _st = OPS.evaluate(s, cur.id, self, self.settings, opstate=state)
                self.stats["ops_evaluations"] += 1
                out += [{"type": "alert", "alert": a} for a in trs]
                s.commit()
            if need_status:
                out.append(self.status_message(s, state))
        return out

    def status_message(self, s, state) -> dict:
        """Mensagem ``operation_status``: o mesmo corpo de ``GET /operations/{id}/status`` (aditiva; clientes antigos
        ignoram tipos que não conhecem)."""
        from .main import clean
        from .operations_api import status_payload
        self.stats["status_messages"] += 1
        return {"type": "operation_status", "operation_id": state.op.operation_id, "machine_id": state.op.machine_id,
                "origin": state.op.origin, "status": clean(status_payload(s, state, self.settings))}

    def run_watchdog(self) -> list[dict]:
        """Vigia de conexão e, com cliente conectado, status das operações correntes: a conexão passa de "conectado" a
        "atrasado" e "sem conexão" com o tempo, mesmo sem nenhum dado novo."""
        from . import op_alerts_service as OPS
        from .operations_api import operation_state
        with self.sf() as s:
            self.stats["watchdog_runs"] += 1
            out = [{"type": "alert", "alert": a} for a in OPS.watchdog(s, self, self.settings)]
            if self.clients:
                now = time.monotonic()
                for (origin, mid), row in OPS.current_operations(s).items():
                    if now - self._last_status.get((mid, origin), -1e9) < self.settings.status_interval_s:
                        continue
                    self._last_status[(mid, origin)] = now
                    try:
                        out.append(self.status_message(s, operation_state(s, row.id, None, self, self.settings)))
                    except Exception as e:                   # uma operação com problema não cala as outras
                        self.stats["last_error"] = repr(e)
            return out

    def window_s(self, s) -> int:
        return int(get_setting(s, "alert_window_s", 600))

    async def broadcast(self, msg: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
