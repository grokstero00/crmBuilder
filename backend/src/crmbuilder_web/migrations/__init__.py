"""Alembic migrations, configured in code so they ship inside the package."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

HERE = Path(__file__).parent


def config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(config(database_url), revision)
