"""Autenticação básica por papéis: viewer < analyst < admin. Token JWT HS256; senha com scrypt (stdlib).

Escopo honesto: é controle de acesso simples para uma instalação única. Não há MFA, SSO, rotação de segredo,
revogação de token nem limitação de tentativas (ver docs/GUIA_TECNICO.md (seção 11)).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

import jwt

ROLES = {"viewer": 1, "analyst": 2, "admin": 3}


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        cand = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(cand.hex(), h)
    except Exception:
        return False


class Tokens:
    def __init__(self, secret: str | None, ttl_s: int):
        self.generated = secret is None
        self.secret = secret or secrets.token_urlsafe(32)     # sem segredo no ambiente: tokens não sobrevivem a reinício
        self.ttl = ttl_s

    def issue(self, username: str, role: str) -> str:
        now = int(time.time())
        return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + self.ttl}, self.secret, algorithm="HS256")

    def verify(self, token: str) -> dict:
        return jwt.decode(token, self.secret, algorithms=["HS256"])
