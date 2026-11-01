"""Adaptadores CSV e JSON. O mapeamento de colunas e unidades é EXPLÍCITO e fica registrado em ``meta``.

Nada é inferido em silêncio: coluna não reconhecida é listada em ``meta['columns_ignored']``; unidade desconhecida
é erro; timestamp sem fuso só é aceito se o chamador declarar ``assume_tz``; valor vazio vira None.
"""
from __future__ import annotations

import io
import json
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import ValidationError

from ..schema import Envelope, Gnss, SENSORS, SensorQ, State, TelemetryPoint
from .base import AdapterError, AdapterResult

ALIASES = {
    "machine_id": ["machine_id", "machine", "id", "vehicle_id", "equipamento", "maquina", "máquina"],
    "ts": ["ts", "timestamp", "time", "datetime", "utc", "data_hora"],
    "lat": ["lat", "latitude"], "lon": ["lon", "lng", "long", "longitude"],
    "speed": ["speed_ms", "speed", "velocidade", "speed_kmh"],
    "state": ["state", "status", "estado", "operational_state"],
    "gnss_fix": ["gnss_fix", "fix", "fix_type", "gnss_status"],
    "accuracy_m": ["accuracy_m", "accuracy", "hacc", "precisao_m", "precisão_m"],
    "flow": ["flow_lpm", "flow", "vazao", "vazão", "flow_lmin"],
    "pressure": ["pressure_bar", "pressure", "pressao", "pressão"],
    "tank_level": ["tank_level_l", "tank_level", "tanque", "nivel_tanque"],
    "consumption": ["consumption_l", "consumption", "consumo", "volume_applied"],
    "width": ["working_width_m", "working_width", "width", "largura"],
    "rate": ["application_rate_lha", "application_rate", "rate", "taxa", "taxa_lha"],
    "error_codes": ["error_codes", "errors", "dtc", "codigos_erro", "códigos_erro"],
    "origin": ["origin", "origem"],
    "property_id": ["property_id", "propriedade"], "field_id": ["field_id", "talhao", "talhão", "plot_id"],
    "operator_id": ["operator_id", "operador"],
}
UNITS = {  # grandeza -> {unidade: fator para a unidade canônica}
    "speed": {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "kn": 0.514444},
    "pressure": {"bar": 1.0, "kpa": 0.01, "mpa": 10.0, "psi": 0.0689476},
    "flow": {"l/min": 1.0, "l/s": 60.0, "l/h": 1 / 60},
    "tank_level": {"l": 1.0, "m3": 1000.0}, "consumption": {"l": 1.0, "m3": 1000.0},
    "width": {"m": 1.0, "cm": 0.01, "ft": 0.3048},
    "accuracy_m": {"m": 1.0, "cm": 0.01}, "rate": {"l/ha": 1.0},
}
CANON_UNIT = {"speed": "m/s", "pressure": "bar", "flow": "l/min", "tank_level": "l", "consumption": "l", "width": "m",
              "accuracy_m": "m", "rate": "l/ha"}
STATE_ALIASES = {
    "working": State.working, "applying": State.working, "spraying": State.working, "trabalhando": State.working, "aplicando": State.working,
    "transport": State.transport, "transporte": State.transport, "driving": State.transport, "deslocamento": State.transport,
    "idle": State.idle, "parado": State.idle, "ocioso": State.idle, "stopped": State.idle,
    "refilling": State.refilling, "abastecendo": State.refilling, "fault": State.fault, "falha": State.fault, "error": State.fault,
    "off": State.off, "desligado": State.off, "unknown": State.unknown, "": State.unknown,
}
FIX_ALIASES = {"none": Gnss.none, "no_fix": Gnss.none, "sem_fix": Gnss.none, "single": Gnss.single, "autonomous": Gnss.single, "gps": Gnss.single,
               "dgps": Gnss.dgps, "sbas": Gnss.dgps, "rtk_float": Gnss.rtk_float, "float": Gnss.rtk_float, "rtk_fixed": Gnss.rtk_fixed,
               "fixed": Gnss.rtk_fixed, "rtk": Gnss.rtk_fixed, "unknown": Gnss.unknown, "": Gnss.unknown}
NULLS = {"", "na", "n/a", "nan", "null", "none", "-"}


def _isnull(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and v.strip().lower() in NULLS


def _num(v):
    if _isnull(v):
        return None
    if isinstance(v, str):
        v = v.strip()
        if v.count(",") == 1 and "." not in v:   # decimal com vírgula
            v = v.replace(",", ".")
    return float(v)


def resolve_columns(cols: list[str]) -> tuple[dict, list[str]]:
    low = {c.strip().lower(): c for c in cols}
    m = {}
    for canon, als in ALIASES.items():
        for a in als:
            if a.lower() in low and canon not in m:
                m[canon] = low[a.lower()]
    return m, [c for c in cols if c not in m.values() and not c.lower().startswith("q_")]


def parse_ts(v, ts_format: str, assume_tz: str | None) -> datetime:
    if ts_format == "epoch_s":
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    if ts_format == "epoch_ms":
        return datetime.fromtimestamp(float(v) / 1000, tz=timezone.utc)
    s = str(v).strip()
    if s[-1:] in "zZ":
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        if not assume_tz:
            raise ValueError("timestamp sem fuso horário (informe assume_tz, ex.: 'UTC' ou 'America/Sao_Paulo')")
        d = d.replace(tzinfo=ZoneInfo(assume_tz))
    return d.astimezone(timezone.utc)


def row_to_point(row: dict, cmap: dict, units: dict, opt: dict) -> TelemetryPoint:
    def g(k):
        c = cmap.get(k)
        return row.get(c) if c else None

    mid = g("machine_id")
    mid = opt.get("machine_id") or (None if _isnull(mid) else str(mid).strip())
    if not mid:
        raise ValueError("machine_id ausente (sem coluna e sem machine_id padrão)")
    d: dict = {"machine_id": mid}
    if _isnull(g("ts")):
        raise ValueError("timestamp ausente")
    d["ts"] = parse_ts(g("ts"), opt["ts_format"], opt["assume_tz"])
    d["lat"], d["lon"] = _num(g("lat")), _num(g("lon"))
    for k, target in (("speed", "speed_ms"), ("pressure", "pressure_bar"), ("flow", "flow_lpm"), ("tank_level", "tank_level_l"),
                      ("consumption", "consumption_l"), ("width", "working_width_m"), ("accuracy_m", "accuracy_m"),
                      ("rate", "application_rate_lha")):
        v = _num(g(k))
        d[target] = None if v is None else v * UNITS[k][units[k]]
    sv = g("state")
    sk = "" if _isnull(sv) else str(sv).strip().lower()
    d["state"] = opt["state_map"].get(sk) or STATE_ALIASES.get(sk, State.unknown)
    fv = g("gnss_fix")
    d["gnss_fix"] = FIX_ALIASES.get("" if _isnull(fv) else str(fv).strip().lower(), Gnss.unknown)
    ec = g("error_codes")
    d["error_codes"] = [] if _isnull(ec) else [x.strip() for x in str(ec).replace("|", ";").replace(",", ";").split(";") if x.strip()]
    ov = g("origin")
    d["origin"] = opt["origin"] if _isnull(ov) else str(ov).strip().lower()
    for k in ("property_id", "field_id", "operator_id"):
        v = g(k)
        d[k] = None if _isnull(v) else str(v)
    q = {}
    for s in SENSORS:
        qc = next((c for c in row if c.lower() == f"q_{s}"), None)
        if qc and not _isnull(row[qc]):
            q[s] = SensorQ(str(row[qc]).strip().lower())
    d["sensor_quality"] = q
    return TelemetryPoint(**d)


def _units(units: dict | None, cmap: dict) -> tuple[dict, list[str]]:
    out, notes = {}, []
    for k, table in UNITS.items():
        u = (units or {}).get(k)
        if u is None:
            u = CANON_UNIT[k]
            if k == "speed" and str(cmap.get("speed", "")).lower() == "speed_kmh":
                u = "km/h"
                notes.append("coluna 'speed_kmh': unidade km/h deduzida do NOME da coluna")
        if u.lower() not in table:
            raise AdapterError(f"unidade não suportada para {k}: {u!r} (suportadas: {sorted(table)})")
        out[k] = u.lower()
    return out, notes


def read_rows(rows: list[dict], columns: list[str], *, origin="unknown", assume_tz=None, ts_format="iso", units=None,
              state_map=None, machine_id=None, source="rows") -> AdapterResult:
    cmap, ignored = resolve_columns(columns)
    res = AdapterResult(n_input=len(rows))
    if "ts" not in cmap or ("machine_id" not in cmap and not machine_id):
        raise AdapterError(f"colunas obrigatórias não encontradas (ts e machine_id). Colunas: {columns}")
    uu, notes = _units(units, cmap)
    opt = {"origin": origin, "assume_tz": assume_tz, "ts_format": ts_format, "machine_id": machine_id,
           "state_map": {k.lower(): State(v) for k, v in (state_map or {}).items()}}
    res.meta = {"source": source, "columns_mapped": cmap, "columns_ignored": ignored, "units": uu, "assume_tz": assume_tz,
                "ts_format": ts_format, "origin_default": origin}
    res.warnings += notes
    unknown_state = 0
    for n, row in enumerate(rows, start=1):
        try:
            p = row_to_point(row, cmap, uu, opt)
        except (ValueError, ValidationError, KeyError) as e:
            res.reject(n, str(e).split("\n")[0][:240] if not isinstance(e, ValidationError)
                       else "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())[:240],
                       {k: v for k, v in list(row.items())[:8]})
            continue
        sv = row.get(cmap["state"]) if "state" in cmap else None
        if p.state == State.unknown and not _isnull(sv) and str(sv).strip().lower() != "unknown":
            unknown_state += 1
        res.points.append(p)
    if unknown_state:
        res.warnings.append(f"{unknown_state} linhas com estado não reconhecido foram mantidas como 'unknown' (use state_map para mapear)")
    if ignored:
        res.warnings.append(f"colunas ignoradas: {ignored}")
    if origin == "unknown":
        res.warnings.append("origem (simulated/real) não declarada: os pontos ficam com origin='unknown'")
    return res


def read_csv(source: str | bytes | io.IOBase, *, sep: str | None = None, **kw) -> AdapterResult:
    raw = source if isinstance(source, (str, bytes)) else source.read()
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise AdapterError(f"CSV não está em UTF-8: {e}")
    try:
        df = pd.read_csv(io.StringIO(raw), sep=sep, engine="python", dtype=str, keep_default_na=False)
    except Exception as e:
        raise AdapterError(f"CSV ilegível: {e}")
    if df.empty:
        raise AdapterError("CSV sem linhas de dados")
    return read_rows(df.to_dict("records"), list(df.columns), source="csv", **kw)


def read_json(source: str | bytes | io.IOBase, **kw) -> AdapterResult:
    """Aceita: lista de objetos; {"points":[...]}; NDJSON; ou envelopes MQTT {"schema_version","type","data"}."""
    raw = source if isinstance(source, (str, bytes)) else source.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig")
    raw = raw.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        try:
            obj = [json.loads(l) for l in raw.splitlines() if l.strip()]
        except json.JSONDecodeError as e:
            raise AdapterError(f"JSON ilegível: {e}")
    if isinstance(obj, dict) and "points" in obj:
        obj = obj["points"]
    if isinstance(obj, dict):
        obj = [obj]
    if not isinstance(obj, list) or not obj or not all(isinstance(o, dict) for o in obj):
        raise AdapterError("JSON deve ser uma lista não vazia de objetos (ou {'points': [...]})")
    rows, bad = [], []
    for n, o in enumerate(obj, 1):
        if "schema_version" in o and "data" in o:   # envelope: valida versão
            try:
                Envelope.model_validate(o)
            except ValidationError as e:
                bad.append((n, "envelope inválido: " + "; ".join(x["msg"] for x in e.errors())[:200]))
                continue
            o = o["data"]
        row = {k: (";".join(map(str, v)) if isinstance(v, list) else v) for k, v in o.items() if k != "sensor_quality"}
        for s, qv in (o.get("sensor_quality") or {}).items():
            row[f"q_{s}"] = qv
        rows.append(row)
    cols = sorted({k for r in rows for k in r})
    res = read_rows(rows, cols, source="json", **kw) if rows else AdapterResult()
    res.n_input = len(obj)
    for n, why in bad:
        res.reject(n, why)
    return res
