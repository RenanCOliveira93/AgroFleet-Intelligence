"""Clima (Etapa 7): tipos comuns, HTTP, cache e utilidades dos provedores.

Regras que valem para todo provedor:
- Nada é inventado: valor ausente no provedor vira ``None`` (nunca 0). Sentinelas tratadas como ausência: ``9999``,
  ``-9999``, ``"Null"``, ``null``, ``""``, ``"*"`` (e ``"*"`` no fim de números, como em ``"27.6*"``, é removido).
- Horários sempre em UTC. A idade de uma leitura vem do horário da MEDIÇÃO contra o relógio do servidor AgroFleet,
  nunca do cabeçalho ``Date`` do provedor (o do INMET já foi visto ~18 min atrasado).
- HTTP só com a biblioteca padrão (``urllib``), User-Agent descritivo e genérico (sem dado pessoal) e tempo limite.
  A função de busca é injetável: os testes nunca acessam a rede.
- Cache em memória por provedor + recurso, com TTL; falhas também ficam em cache por pouco tempo, para não insistir
  num provedor fora do ar. Uma única busca por chave de cada vez (as demais esperam o resultado).
"""
from __future__ import annotations

import math
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

DEFAULT_USER_AGENT = "AgroFleet-Intelligence/1.1 (+https://github.com/RenanCOliveira93/AgroFleet-Intelligence)"
NO_DATA_MESSAGE = "Sem dados meteorológicos"
MAX_BODY_BYTES = 8 * 1024 * 1024
_SENTINELS = {"", "*", "null", "none", "nan", "-", "--"}


# ----------------------------------------------------------------------------------------------------------------
# erros e resposta HTTP
# ----------------------------------------------------------------------------------------------------------------

class ProviderError(Exception):
    """Falha de um provedor com código do contrato (``reasons[].code``) e mensagem em PT."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class HttpResponse:
    status: int
    headers: dict          # nomes em minúsculas
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


Fetch = Callable[[str, dict, float], HttpResponse]


def urllib_fetch(url: str, headers: dict, timeout: float) -> HttpResponse:
    """Busca HTTP GET com ``urllib``. Códigos HTTP de erro voltam como resposta (o chamador decide); falha de rede e
    tempo esgotado viram ``ProviderError`` com código ``timeout`` ou ``http_error``."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (URL montada só a partir da config)
            return HttpResponse(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(MAX_BODY_BYTES))
    except urllib.error.HTTPError as e:
        body = e.read(65536) if e.fp else b""
        return HttpResponse(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body)
    except (socket.timeout, TimeoutError) as e:
        raise ProviderError("timeout", f"tempo esgotado ({timeout:g} s)") from e
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise ProviderError("timeout", f"tempo esgotado ({timeout:g} s)") from e
        raise ProviderError("http_error", f"falha de conexão: {e.reason}") from e
    except OSError as e:
        raise ProviderError("http_error", f"falha de conexão: {e}") from e


# ----------------------------------------------------------------------------------------------------------------
# configuração
# ----------------------------------------------------------------------------------------------------------------

@dataclass
class WeatherConfig:
    providers: list = field(default_factory=lambda: ["inmet"])   # ordem de preferência
    env: str = "dev"
    timeout_s: float = 8.0
    max_age_s: float = 7200.0             # leitura mais velha que isso é marcada ``stale`` (ainda é devolvida)
    obs_ttl_s: float = 1800.0
    forecast_ttl_s: float = 10800.0
    fail_ttl_s: float = 120.0
    catalog_ttl_s: float = 86400.0
    default_ibge: str | None = None
    inmet_token: str | None = None
    inmet_max_station_km: float = 60.0
    allow_noncommercial: bool = False
    openmeteo_base_url: str = "https://api.open-meteo.com"
    openmeteo_api_key: str | None = None
    metno_base_url: str = "https://api.met.no"
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def from_settings(cls, st) -> "WeatherConfig":
        """Lê os campos ``weather_*`` de ``backend.app.config.Settings`` (ou de qualquer objeto com os mesmos nomes)."""
        d = cls()
        g = lambda name, default: getattr(st, name, default) if getattr(st, name, None) is not None else default  # noqa: E731
        return cls(providers=list(g("weather_providers", d.providers)), env=g("env", d.env),
                   timeout_s=float(g("weather_timeout_s", d.timeout_s)), max_age_s=float(g("weather_max_age_s", d.max_age_s)),
                   obs_ttl_s=float(g("weather_obs_ttl_s", d.obs_ttl_s)), forecast_ttl_s=float(g("weather_forecast_ttl_s", d.forecast_ttl_s)),
                   fail_ttl_s=float(g("weather_fail_ttl_s", d.fail_ttl_s)), catalog_ttl_s=d.catalog_ttl_s,
                   default_ibge=g("weather_ibge", None), inmet_token=g("inmet_token", None),
                   inmet_max_station_km=float(g("inmet_max_station_km", d.inmet_max_station_km)),
                   allow_noncommercial=bool(g("weather_allow_noncommercial", False)),
                   openmeteo_base_url=g("openmeteo_base_url", d.openmeteo_base_url), openmeteo_api_key=g("openmeteo_api_key", None),
                   metno_base_url=g("metno_base_url", d.metno_base_url), user_agent=g("weather_user_agent", d.user_agent))


@dataclass
class Location:
    """Ponto consultado. ``lat``/``lon`` podem faltar (só o código IBGE); ``now`` é o relógio da consulta (UTC)."""
    lat: float | None
    lon: float | None
    ibge: str | None
    tz: str
    now: datetime


# ----------------------------------------------------------------------------------------------------------------
# cache
# ----------------------------------------------------------------------------------------------------------------

class TTLCache:
    """Cache em memória com TTL por entrada, limite de tamanho (LRU) e busca única por chave (single-flight)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_entries: int = 512):
        self.clock, self.max_entries = clock, max_entries
        self._d: OrderedDict = OrderedDict()      # key -> (expires_mono, ok, value, fetched_at_utc)
        self._lock = threading.Lock()
        self._key_locks: dict = {}
        self.stats = {"hits": 0, "misses": 0, "failures_cached": 0}

    def _get(self, key):
        with self._lock:
            e = self._d.get(key)
            if e is None:
                return None
            if e[0] <= self.clock():
                self._d.pop(key, None)
                return None
            self._d.move_to_end(key)
            return e

    def _put(self, key, ttl, ok, value, fetched_at):
        with self._lock:
            self._d[key] = (self.clock() + ttl, ok, value, fetched_at)
            self._d.move_to_end(key)
            while len(self._d) > self.max_entries:
                self._d.popitem(last=False)

    def get_or_fetch(self, key, ttl: float, fail_ttl: float, fn: Callable[[], object], utcnow: Callable[[], datetime]):
        """Devolve ``(valor, veio_do_cache, buscado_em)``. Se ``fn`` levanta ``ProviderError``, a falha fica em cache por
        ``fail_ttl`` e é relançada (também nas chamadas seguintes, até expirar)."""
        e = self._get(key)
        if e is None:
            with self._lock:
                kl = self._key_locks.setdefault(key, threading.Lock())
            with kl:
                e = self._get(key)                 # outra thread pode ter buscado enquanto esperávamos
                if e is None:
                    self.stats["misses"] += 1
                    try:
                        v = fn()
                    except ProviderError as err:
                        self.stats["failures_cached"] += 1
                        self._put(key, fail_ttl, False, err, utcnow())
                        raise
                    ts = utcnow()
                    self._put(key, ttl, True, v, ts)
                    return v, False, ts
        self.stats["hits"] += 1
        _exp, ok, v, ts = e
        if not ok:
            raise ProviderError(v.code, v.message)
        return v, True, ts

    def clear(self):
        with self._lock:
            self._d.clear()

    def __len__(self):
        return len(self._d)


# ----------------------------------------------------------------------------------------------------------------
# utilidades
# ----------------------------------------------------------------------------------------------------------------

def num(v) -> float | None:
    """Converte valor do provedor em número; sentinelas de ausência viram ``None`` (nunca 0)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).strip()
        if s.lower() in _SENTINELS:
            return None
        s = s.rstrip("*").strip().replace(",", ".")       # "27.6*" (condição das capitais) e "-13,01" (CSV histórico)
        if s.lower() in _SENTINELS:
            return None
        try:
            x = float(s)
        except ValueError:
            return None
    if not math.isfinite(x) or abs(x) == 9999 or abs(x) == 99999:
        return None
    return x


def as_int_if_whole(x: float | None):
    return int(x) if x is not None and float(x).is_integer() else x


def text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in _SENTINELS else s


def ms_to_kmh(v: float | None) -> float | None:
    return None if v is None else round(v * 3.6, 1)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def iso_z(t: datetime | None) -> str | None:
    if t is None:
        return None
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compass8(deg: float | None) -> str | None:
    if deg is None:
        return None
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int(((deg % 360) + 22.5) // 45) % 8]


def freshness(observed_at: datetime | None, now: datetime, max_age_s: float) -> tuple[float | None, bool]:
    """(idade em s, stale). Leitura no futuro (relógio do provedor adiantado) conta como idade 0."""
    if observed_at is None:
        return None, True
    age = max((now - observed_at).total_seconds(), 0.0)
    return round(age), age > max_age_s


def fmt_age(age_s: float) -> str:
    h = age_s / 3600
    return f"{h:.1f}".replace(".", ",") + " h" if h >= 1 else f"{round(age_s / 60)} min"


class Provider:
    """Interface mínima de um provedor de clima. Cada método devolve o bloco do contrato (``observation`` ou
    ``forecast``) já com metadados ``_cached``/``_fetched_at`` (o serviço os remove), ou levanta ``ProviderError``.
    ``reasons`` recebe avisos não fatais (ex.: rota com token falhou e caiu na rota pública)."""

    name = "base"
    label = "Base"
    supports: frozenset = frozenset()
    non_commercial = False

    def __init__(self, cfg: WeatherConfig, fetch: Fetch, cache: TTLCache, utcnow: Callable[[], datetime]):
        self.cfg, self.fetch, self.cache, self.utcnow = cfg, fetch, cache, utcnow

    def gate(self) -> ProviderError | None:
        """Motivo para NÃO usar o provedor nesta implantação (licença, configuração), ou ``None``."""
        return None

    def attribution(self) -> dict:
        raise NotImplementedError

    def observation(self, loc: Location, reasons: list) -> dict:
        raise ProviderError("no_data", "provedor não fornece observação")

    def forecast(self, loc: Location, reasons: list) -> dict:
        raise ProviderError("no_data", "provedor não fornece previsão")

    # -- auxiliares --
    def headers(self, extra: dict | None = None) -> dict:
        return {"User-Agent": self.cfg.user_agent, "Accept": "application/json", **(extra or {})}

    def get(self, url: str, extra_headers: dict | None = None) -> HttpResponse:
        return self.fetch(url, self.headers(extra_headers), self.cfg.timeout_s)

    def cached(self, key, ttl, fn):
        return self.cache.get_or_fetch((self.name, *key), ttl, self.cfg.fail_ttl_s, fn, self.utcnow)
