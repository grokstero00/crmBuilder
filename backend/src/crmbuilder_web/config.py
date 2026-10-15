"""Settings from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "y", "yes", "true", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    allow_signup: bool = True
    cookie_secure: bool = False
    session_days: int = 30

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("CRMBUILDER_SECRET_KEY", "").strip()
        if len(secret) < 32:
            raise RuntimeError(
                "CRMBUILDER_SECRET_KEY must be set to a random string of at least 32 characters "
                "(e.g. `openssl rand -hex 32`); it signs sessions and encrypts webhooks"
            )
        return cls(
            database_url=os.environ.get("CRMBUILDER_DATABASE_URL", "sqlite:///./crmbuilder.db"),
            secret_key=secret,
            allow_signup=_flag("CRMBUILDER_ALLOW_SIGNUP", True),
            cookie_secure=_flag("CRMBUILDER_COOKIE_SECURE", False),
        )
