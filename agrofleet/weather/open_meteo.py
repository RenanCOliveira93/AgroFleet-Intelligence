"""Provedor Open-Meteo: condições "atuais" de MODELO (não é estação; dados de modelo a cada 15 min).

Somente desenvolvimento: a API gratuita é exclusivamente NÃO COMERCIAL (termos do Open-Meteo). Por isso o provedor só
roda com ``AGROFLEET_ENV=dev`` E ``AGROFLEET_WEATHER_ALLOW_NONCOMMERCIAL=1``. Uma implantação comercial que contrate o
plano pago define ``OPENMETEO_BASE_URL=https://customer-api.open-meteo.com`` e ``OPENMETEO_API_KEY``; com a chave, o uso
é coberto pela licença comercial do plano e o provedor fica liberado em qualquer ambiente (``non_commercial=false``).

Licença dos dados: CC BY 4.0, com link obrigatório junto do dado: "Weather data by Open-Meteo.com".
O valor vai no bloco ``observation`` com ``kind="model"`` e rótulo "Open-Meteo (modelo)", nunca como leitura de sensor.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from .base import Location, Provider, ProviderError, as_int_if_whole, freshness, num

CURRENT = "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation"
MODEL_NOTE = "Estimativa de modelo meteorológico para o ponto (grade de alguns km), não é leitura de estação."


def parse_current(payload) -> dict:
    cur = payload.get("current") if isinstance(payload, dict) else None
    if not isinstance(cur, dict) or not cur.get("time"):
        raise ProviderError("no_data", "Open-Meteo sem condições atuais")
    try:   # pedimos timezone=GMT: "time" é UTC sem sufixo
        t = datetime.fromisoformat(str(cur["time"])).replace(tzinfo=timezone.utc)
    except ValueError:
        raise ProviderError("no_data", "horário inválido na resposta do Open-Meteo") from None
    return {"observed_at": t.isoformat().replace("+00:00", "Z"), "temperature_c": num(cur.get("temperature_2m")),
            "humidity_pct": as_int_if_whole(num(cur.get("relative_humidity_2m"))), "wind_speed_kmh": num(cur.get("wind_speed_10m")),
            "wind_dir_deg": as_int_if_whole(num(cur.get("wind_direction_10m"))), "wind_gust_kmh": num(cur.get("wind_gusts_10m")),
            "precipitation_mm": num(cur.get("precipitation")),
            "grid_lat": num(payload.get("latitude")), "grid_lon": num(payload.get("longitude"))}


class OpenMeteoProvider(Provider):
    name, label = "openmeteo", "Open-Meteo (modelo)"
    supports = frozenset({"observation"})

    @property
    def non_commercial(self) -> bool:            # plano pago (chave) = licença comercial
        return not self.cfg.openmeteo_api_key

    def gate(self) -> ProviderError | None:
        if self.cfg.openmeteo_api_key:
            return None
        if self.cfg.env != "dev" or not self.cfg.allow_noncommercial:
            return ProviderError("not_allowed", "Open-Meteo gratuito é só para uso não comercial: exige AGROFLEET_ENV=dev e "
                                 "AGROFLEET_WEATHER_ALLOW_NONCOMMERCIAL=1 (ou OPENMETEO_API_KEY de plano pago)")
        return None

    def attribution(self) -> dict:
        return {"provider": "openmeteo", "text": "Weather data by Open-Meteo.com", "url": "https://open-meteo.com/",
                "licence": "CC BY 4.0", "licence_url": "https://creativecommons.org/licenses/by/4.0/"}

    def observation(self, loc: Location, reasons: list) -> dict:
        if loc.lat is None or loc.lon is None:
            raise ProviderError("no_data", "Open-Meteo exige coordenadas do ponto")
        lat, lon = round(loc.lat, 2), round(loc.lon, 2)
        q = {"latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}", "current": CURRENT, "wind_speed_unit": "kmh", "timezone": "GMT"}
        if self.cfg.openmeteo_api_key:
            q["apikey"] = self.cfg.openmeteo_api_key
        url = f"{self.cfg.openmeteo_base_url.rstrip('/')}/v1/forecast?{urlencode(q)}"

        def load():
            r = self.get(url)
            if r.status >= 400:
                raise ProviderError("http_error", f"Open-Meteo respondeu HTTP {r.status}")
            try:
                return parse_current(json.loads(r.text()))
            except ValueError:
                raise ProviderError("no_data", "resposta do Open-Meteo não é JSON") from None

        cur, cached, ts = self.cached(("current", lat, lon), self.cfg.obs_ttl_s, load)
        t = datetime.fromisoformat(cur["observed_at"].replace("Z", "+00:00"))
        age, stale = freshness(t, loc.now, self.cfg.max_age_s)
        return {"provider": "openmeteo", "provider_label": self.label, "kind": "model",
                "station_code": None, "station_name": None, "station_uf": None, "station_lat": None, "station_lon": None,
                "distance_km": None, "distance_basis": None, **cur, "age_s": age, "stale": stale, "note": MODEL_NOTE,
                "_cached": cached, "_fetched_at": ts}
