from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name, default=None):
    v = os.environ.get(name)
    return default if v in (None, "") else v


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///data/agrofleet.db"))
    env: str = field(default_factory=lambda: _env("AGROFLEET_ENV", "dev"))
    auth_secret: str | None = field(default_factory=lambda: _env("AGROFLEET_AUTH_SECRET"))
    token_ttl_s: int = field(default_factory=lambda: int(_env("AGROFLEET_TOKEN_TTL_S", "28800")))
    # usuários semeados na subida; sem senha no ambiente => usuário NÃO é criado (não existe senha padrão)
    users: dict = field(default_factory=lambda: {
        role: (_env(f"AGROFLEET_{role.upper()}_USER", role), _env(f"AGROFLEET_{role.upper()}_PASSWORD"))
        for role in ("admin", "analyst", "viewer")})
    mqtt_host: str | None = field(default_factory=lambda: _env("MQTT_HOST"))
    mqtt_port: int = field(default_factory=lambda: int(_env("MQTT_PORT", "1883")))
    mqtt_user: str | None = field(default_factory=lambda: _env("MQTT_USER"))
    mqtt_password: str | None = field(default_factory=lambda: _env("MQTT_PASSWORD"))
    mqtt_cafile: str | None = field(default_factory=lambda: _env("MQTT_CAFILE"))
    max_upload_mb: int = field(default_factory=lambda: int(_env("AGROFLEET_MAX_UPLOAD_MB", "50")))
    cors_origins: list = field(default_factory=lambda: [o for o in (_env("AGROFLEET_CORS_ORIGINS", "") or "").split(",") if o])
    live_window_s: int = 30
    # Etapa 6: intervalo mínimo entre mensagens WebSocket ``operation_status`` da mesma operação, e quantas operações
    # o cache incremental mantém em memória (LRU; medido: ~40 MB por operação de 5 h, ~65 MB de 12 h)
    status_interval_s: float = field(default_factory=lambda: float(_env("AGROFLEET_STATUS_INTERVAL_S", "5")))
    ops_cache_size: int = field(default_factory=lambda: int(_env("AGROFLEET_OPS_CACHE_SIZE", "8")))
    alert_window_s: int = field(default_factory=lambda: int(_env("AGROFLEET_ALERT_WINDOW_S", "600")))
    # Etapa 7: clima. Provedores em ordem de preferência (inmet | metno | openmeteo | none); nada é buscado na subida.
    weather_providers: list = field(default_factory=lambda: [p.strip().lower() for p in
                                                             (_env("AGROFLEET_WEATHER_PROVIDERS", "inmet") or "").split(",") if p.strip()])
    weather_timeout_s: float = field(default_factory=lambda: float(_env("AGROFLEET_WEATHER_TIMEOUT_S", "8")))
    weather_max_age_s: float = field(default_factory=lambda: float(_env("AGROFLEET_WEATHER_MAX_AGE_S", "7200")))
    weather_obs_ttl_s: float = field(default_factory=lambda: float(_env("AGROFLEET_WEATHER_OBS_TTL_S", "1800")))
    weather_forecast_ttl_s: float = field(default_factory=lambda: float(_env("AGROFLEET_WEATHER_FORECAST_TTL_S", "10800")))
    weather_fail_ttl_s: float = field(default_factory=lambda: float(_env("AGROFLEET_WEATHER_FAIL_TTL_S", "120")))
    weather_ibge: str | None = field(default_factory=lambda: _env("AGROFLEET_WEATHER_IBGE"))   # município padrão (7 dígitos)
    weather_user_agent: str | None = field(default_factory=lambda: _env("AGROFLEET_WEATHER_USER_AGENT"))
    inmet_token: str | None = field(default_factory=lambda: _env("AGROFLEET_INMET_TOKEN"))
    inmet_max_station_km: float = field(default_factory=lambda: float(_env("AGROFLEET_INMET_MAX_STATION_KM", "60")))
    # Open-Meteo gratuito é NÃO COMERCIAL: só roda com env=dev e esta flag = 1 (ou com chave de plano pago)
    weather_allow_noncommercial: bool = field(default_factory=lambda: _env("AGROFLEET_WEATHER_ALLOW_NONCOMMERCIAL", "0") == "1")
    openmeteo_base_url: str = field(default_factory=lambda: _env("OPENMETEO_BASE_URL", "https://api.open-meteo.com"))
    openmeteo_api_key: str | None = field(default_factory=lambda: _env("OPENMETEO_API_KEY"))
