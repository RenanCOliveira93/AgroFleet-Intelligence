"""Provedor MET Norway (api.met.no, Locationforecast 2.0 compact): previsão de MODELO para qualquer ponto do globo.

Opcional e desligado por padrão (entra só se ``metno`` estiver em ``AGROFLEET_WEATHER_PROVIDERS``). Licença dos dados:
CC BY 4.0 / NLOD 2.0, que permitem uso comercial com crédito "Data from MET Norway".

Termos de serviço respeitados: User-Agent identificador; coordenadas com no máximo 4 casas (aqui 2, ~1 km, o que
também melhora o cache); nunca repetir a mesma consulta antes do horário do cabeçalho ``Expires``; revalidar com
``If-Modified-Since`` (resposta 304 reaproveita o dado guardado). A validade é calculada como ``Expires − Date`` sobre o
nosso relógio, para não depender do relógio do servidor.

O resumo em português vem do ``symbol_code`` do próprio provedor (tradução de rótulo, não inferência nossa).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from .base import Location, Provider, ProviderError, as_int_if_whole, compass8, ms_to_kmh, num
from .inmet import PERIOD_LABEL, period_for_hour

_BASE = {"clearsky": "Céu limpo", "fair": "Poucas nuvens", "partlycloudy": "Parcialmente nublado", "cloudy": "Nublado",
         "fog": "Nevoeiro", "lightrain": "Chuva fraca", "rain": "Chuva", "heavyrain": "Chuva forte",
         "lightrainshowers": "Pancadas de chuva fracas", "rainshowers": "Pancadas de chuva", "heavyrainshowers": "Pancadas de chuva fortes",
         "lightsleet": "Chuva com neve fraca", "sleet": "Chuva com neve", "heavysleet": "Chuva com neve forte",
         "lightsleetshowers": "Pancadas de chuva com neve fracas", "sleetshowers": "Pancadas de chuva com neve",
         "heavysleetshowers": "Pancadas de chuva com neve fortes", "lightsnow": "Neve fraca", "snow": "Neve", "heavysnow": "Neve forte",
         "lightsnowshowers": "Pancadas de neve fracas", "snowshowers": "Pancadas de neve", "heavysnowshowers": "Pancadas de neve fortes"}
_WINDOWS = {"manha": (6, 12), "tarde": (12, 18), "noite": (18, 24)}


def symbol_pt(code: str | None) -> str | None:
    """``partlycloudy_day`` → "Parcialmente nublado"; ``rainshowersandthunder_night`` → "Pancadas de chuva com trovoadas"."""
    if not code:
        return None
    base = code.split("_")[0]
    thunder = base.endswith("andthunder")
    if thunder:
        base = base[: -len("andthunder")]
    pt = _BASE.get(base)
    if pt is None:
        return None
    return pt + (" com trovoadas" if thunder else "")


def _symbol(entry: dict) -> str | None:
    d = entry.get("data") or {}
    for k in ("next_1_hours", "next_6_hours", "next_12_hours"):
        c = ((d.get(k) or {}).get("summary") or {}).get("symbol_code")
        if c:
            return c
    return None


def parse_compact(payload) -> list[dict]:
    """Resposta ``compact`` → série ``[{t, temperature_c, humidity_pct, wind_speed_kmh, wind_dir_deg, symbol, precip_1h_mm}]``."""
    ts = (((payload or {}).get("properties") or {}).get("timeseries")) if isinstance(payload, dict) else None
    if not isinstance(ts, list) or not ts:
        raise ProviderError("no_data", "MET Norway sem série de previsão")
    out = []
    for e in ts:
        try:
            t = datetime.fromisoformat(str(e["time"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        det = (((e.get("data") or {}).get("instant") or {}).get("details")) or {}
        n1 = ((e.get("data") or {}).get("next_1_hours") or {}).get("details") or {}
        out.append({"t": t, "temperature_c": num(det.get("air_temperature")), "humidity_pct": num(det.get("relative_humidity")),
                    "wind_speed_kmh": ms_to_kmh(num(det.get("wind_speed"))), "wind_dir_deg": num(det.get("wind_from_direction")),
                    "symbol": _symbol(e), "precip_1h_mm": num(n1.get("precipitation_amount"))})
    if not out:
        raise ProviderError("no_data", "MET Norway sem série de previsão")
    return out


def forecast_view(series: list[dict], loc: Location) -> dict:
    """Período atual do dia local (mesma regra do INMET) → bloco ``forecast``. Mín./máx. sobre as horas do período;
    valores pontuais da hora mais próxima de agora."""
    tz = ZoneInfo(loc.tz)
    local = loc.now.astimezone(tz)
    period = period_for_hour(local.hour)
    h0, h1 = _WINDOWS[period]
    day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
    w0, w1 = day0 + timedelta(hours=h0), day0 + timedelta(hours=h1)
    win = [e for e in series if w0 <= e["t"].astimezone(tz) < w1]
    if not win:
        raise ProviderError("no_data", "previsão MET Norway não cobre o período atual")
    future = [e for e in series if e["t"] >= loc.now - timedelta(minutes=30)]
    cur = min(future or series, key=lambda e: abs((e["t"] - loc.now).total_seconds()))
    temps = [e["temperature_c"] for e in win if e["temperature_c"] is not None]
    hums = [e["humidity_pct"] for e in win if e["humidity_pct"] is not None]
    sym = cur["symbol"] if w0 <= cur["t"].astimezone(tz) < w1 else next((e["symbol"] for e in win if e["symbol"]), None)
    return {"provider": "metno", "provider_label": "MET Norway (modelo)", "kind": "forecast",
            "municipality_code": None, "municipality": None, "uf": None, "date": local.date().isoformat(),
            "period": period, "period_label": PERIOD_LABEL[period],
            "summary": symbol_pt(sym), "symbol_code": sym,
            "temp_min_c": round(min(temps), 1) if temps else None, "temp_max_c": round(max(temps), 1) if temps else None,
            "humidity_min_pct": as_int_if_whole(round(min(hums))) if hums else None,
            "humidity_max_pct": as_int_if_whole(round(max(hums))) if hums else None,
            "wind_dir": compass8(cur["wind_dir_deg"]), "wind_intensity": None,
            "temperature_c": cur["temperature_c"], "humidity_pct": as_int_if_whole(cur["humidity_pct"]),
            "wind_speed_kmh": cur["wind_speed_kmh"], "valid_at": cur["t"].isoformat().replace("+00:00", "Z"), "days": None}


class MetnoProvider(Provider):
    name, label = "metno", "MET Norway"
    supports = frozenset({"forecast"})

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._http: dict = {}          # chave → {"until": datetime UTC, "last_modified": str | None, "series": [...]}
        self._lock = threading.Lock()

    def attribution(self) -> dict:
        return {"provider": "metno", "text": "Data from MET Norway", "url": "https://api.met.no/doc/License",
                "licence": "CC BY 4.0 / NLOD 2.0"}

    def _load(self, lat: float, lon: float):
        key = (lat, lon)
        now = self.utcnow()
        with self._lock:
            mem = self._http.get(key)
        if mem and now < mem["until"]:
            return mem["series"]                 # ToS: não repetir antes do Expires
        extra = {"If-Modified-Since": mem["last_modified"]} if mem and mem.get("last_modified") else {}
        r = self.get(f"{self.cfg.metno_base_url}/weatherapi/locationforecast/2.0/compact?lat={lat:.2f}&lon={lon:.2f}", extra)
        if r.status == 304 and mem:
            series = mem["series"]
        elif r.status in (200, 203):
            try:
                series = parse_compact(json.loads(r.text()))
            except ValueError:
                raise ProviderError("no_data", "resposta do MET Norway não é JSON") from None
        elif r.status == 429:
            raise ProviderError("http_error", "MET Norway limitou as consultas (HTTP 429)")
        else:
            raise ProviderError("http_error", f"MET Norway respondeu HTTP {r.status}")
        until = now
        try:
            exp = parsedate_to_datetime(r.headers["expires"])
            ref = parsedate_to_datetime(r.headers["date"]) if r.headers.get("date") else now
            until = now + max(exp - ref, timedelta(0))
        except (KeyError, TypeError, ValueError):
            pass
        with self._lock:
            self._http[key] = {"until": until, "last_modified": r.headers.get("last-modified") or (mem or {}).get("last_modified"),
                               "series": series}
        return series

    def forecast(self, loc: Location, reasons: list) -> dict:
        if loc.lat is None or loc.lon is None:
            raise ProviderError("no_data", "MET Norway exige coordenadas do ponto")
        lat, lon = round(loc.lat, 2), round(loc.lon, 2)
        series, cached, ts = self.cached(("compact", lat, lon), self.cfg.forecast_ttl_s, lambda: self._load(lat, lon))
        return {**forecast_view(series, loc), "_cached": cached, "_fetched_at": ts}
