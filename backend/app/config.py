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
    alert_window_s: int = field(default_factory=lambda: int(_env("AGROFLEET_ALERT_WINDOW_S", "600")))
