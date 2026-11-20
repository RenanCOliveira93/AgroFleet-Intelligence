"""Serviço de clima: escolhe provedores pela ordem configurada, aplica cache e monta a resposta do contrato A.

- Ordem: ``AGROFLEET_WEATHER_PROVIDERS`` (padrão ``inmet``). Para a observação, vale o primeiro provedor da lista que
  devolver dado; idem para a previsão. Falhas viram ``reasons`` (nunca exceção para o chamador).
- Observação velha (> ``AGROFLEET_WEATHER_MAX_AGE_S``) é devolvida mesmo assim, com ``stale=true`` e um motivo.
- ``status``: ``ok`` (observação e previsão), ``partial`` (só uma), ``unavailable`` (nenhuma; ``message`` =
  "Sem dados meteorológicos").
- Nada é buscado na criação do serviço: só quando um endpoint de clima é chamado.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .base import NO_DATA_MESSAGE, Location, ProviderError, TTLCache, WeatherConfig, fmt_age, iso_z, urllib_fetch
from .inmet import InmetProvider
from .metno import MetnoProvider
from .open_meteo import OpenMeteoProvider

PROVIDERS = {"inmet": InmetProvider, "metno": MetnoProvider, "openmeteo": OpenMeteoProvider}
IBGE_RE = re.compile(r"^\d{7}$")
SIMULATED_NOTE = "Talhão simulado em local fictício: o clima mostrado é o real deste ponto, não do talhão."


def _reason(provider: str, e: ProviderError) -> dict:
    return {"provider": provider, "code": e.code, "message": e.message}


class WeatherService:
    def __init__(self, cfg: WeatherConfig | None = None, fetch: Callable | None = None,
                 utcnow: Callable[[], datetime] | None = None, monotonic: Callable[[], float] | None = None):
        self.cfg = cfg or WeatherConfig()
        self.fetch = fetch or urllib_fetch
        self.utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self.cache = TTLCache(monotonic or time.monotonic)
        self.unknown: list[str] = []
        self.providers = []
        for name in self.cfg.providers:
            n = str(name).strip().lower()
            if not n or n == "none":
                continue
            cls = PROVIDERS.get(n)
            if cls is None:
                self.unknown.append(n)
            elif all(p.name != n for p in self.providers):
                self.providers.append(cls(self.cfg, self.fetch, self.cache, self.utcnow))

    @classmethod
    def from_settings(cls, st, **kw) -> "WeatherService":
        return cls(WeatherConfig.from_settings(st), **kw)

    def summary(self) -> dict:
        """Resumo para ``/health`` (não faz nenhuma busca)."""
        return {"providers": [p.name for p in self.providers], "unknown_providers": self.unknown,
                "usable": [p.name for p in self.providers if p.gate() is None],
                "inmet_token": bool(self.cfg.inmet_token), "default_ibge": self.cfg.default_ibge,
                "noncommercial_allowed": self.cfg.env == "dev" and self.cfg.allow_noncommercial,
                "ttl_s": {"observation": self.cfg.obs_ttl_s, "forecast": self.cfg.forecast_ttl_s, "failure": self.cfg.fail_ttl_s},
                "cache": {"entries": len(self.cache), **self.cache.stats}}

    def clear(self):
        self.cache.clear()

    # ------------------------------------------------------------------------------------------------------------
    def _chain(self, kind: str, loc: Location, reasons: list):
        for p in self.providers:
            if kind not in p.supports:
                continue
            g = p.gate()
            if g is not None:
                reasons.append(_reason(p.name, g))
                continue
            try:
                return p, (p.observation if kind == "observation" else p.forecast)(loc, reasons)
            except ProviderError as e:
                reasons.append(_reason(p.name, e))
            except Exception as e:          # defeito de leitura de um provedor não derruba a resposta
                reasons.append({"provider": p.name, "code": "no_data", "message": f"falha ao ler resposta: {type(e).__name__}"})
        return None, None

    def lookup(self, lat: float | None, lon: float | None, *, ibge: str | None = None, tz: str | None = None,
               source: str | None = "query", fictitious: bool = False, note: str | None = None) -> dict:
        """Clima do ponto. Nunca levanta exceção por falha de provedor."""
        reasons: list[dict] = []
        if ibge and not IBGE_RE.match(str(ibge)):
            reasons.append({"provider": "agrofleet", "code": "no_municipality", "message": f"código IBGE inválido: {ibge!r}"})
            ibge = None
        if not ibge and self.cfg.default_ibge and IBGE_RE.match(self.cfg.default_ibge):
            ibge = self.cfg.default_ibge
        try:
            ZoneInfo(tz or "America/Sao_Paulo")
            tz = tz or "America/Sao_Paulo"
        except (ZoneInfoNotFoundError, ValueError):
            tz = "America/Sao_Paulo"
        loc = Location(lat, lon, ibge, tz, self.utcnow())
        location = {"lat": lat, "lon": lon, "source": source, "fictitious": bool(fictitious),
                    "note": note if note is not None else (SIMULATED_NOTE if fictitious else None), "ibge_municipality": ibge}
        if not self.providers:
            reasons.append({"provider": "agrofleet", "code": "disabled",
                            "message": "nenhum provedor de clima habilitado (AGROFLEET_WEATHER_PROVIDERS)"})
        for n in self.unknown:
            reasons.append({"provider": n, "code": "disabled", "message": f"provedor de clima desconhecido: {n!r}"})
        if lat is None and lon is None and not ibge:
            reasons.append({"provider": "agrofleet", "code": "no_data", "message": "sem coordenadas nem município para consultar o clima"})

        r_obs: list[dict] = []
        r_fc: list[dict] = []
        if self.providers and (lat is not None or ibge):
            with ThreadPoolExecutor(max_workers=2) as ex:     # observação e previsão em paralelo (rotas independentes)
                f_obs = ex.submit(self._chain, "observation", loc, r_obs)
                f_fc = ex.submit(self._chain, "forecast", loc, r_fc)
                (p_obs, obs), (p_fc, fc) = f_obs.result(), f_fc.result()
        else:
            p_obs = obs = p_fc = fc = None
        reasons += r_obs + [r for r in r_fc if r not in r_obs]

        used, cached_flags, fetched = [], [], []
        for p, blk in ((p_obs, obs), (p_fc, fc)):
            if blk is None:
                continue
            cached_flags.append(blk.pop("_cached", False))
            ts = blk.pop("_fetched_at", None)
            if ts is not None:
                fetched.append(ts)
            if p not in used:
                used.append(p)
        if obs is not None:
            if obs.get("stale"):
                reasons.append({"provider": obs["provider"], "code": "stale",
                                "message": ("última leitura " + (f"da estação {obs['station_code']} " if obs.get("station_code") else "")
                                            + (f"tem {fmt_age(obs['age_s'])}" if obs.get("age_s") is not None else "sem horário")
                                            + f"; limite {fmt_age(self.cfg.max_age_s)}")})
        status = "ok" if obs is not None and fc is not None else "partial" if (obs is not None or fc is not None) else "unavailable"
        attributions = [p.attribution() for p in used]
        return {
            "status": status,
            "message": NO_DATA_MESSAGE if status == "unavailable" else None,
            "reasons": reasons,
            "location": location,
            "observation": obs,
            "forecast": fc,
            "attribution": " · ".join(a["text"] for a in attributions) or None,
            "attributions": attributions,
            "non_commercial": any(p.non_commercial for p in used),
            "fetched_at": iso_z(min(fetched)) if fetched else None,
            "cached": bool(cached_flags) and all(cached_flags),
            "evaluated_at": iso_z(loc.now),
        }
