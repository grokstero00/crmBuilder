"""Request dependencies: database session, current user, Bitrix24 client."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from crmbuilder.bitrix import BitrixClient
from crmbuilder_web.config import Settings
from crmbuilder_web.models import LoginSession, Project, User
from crmbuilder_web.security import Cipher, token_hash

SESSION_COOKIE = "crmb_session"

# factory(webhook, writable=False) -> client; read-only unless writable=True.
BitrixFactory = Callable[..., BitrixClient]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_cipher(request: Request) -> Cipher:
    return request.app.state.cipher


def get_bitrix_factory(request: Request) -> BitrixFactory:
    return request.app.state.bitrix_factory


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as db:
        yield db


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        found = db.get(LoginSession, token_hash(token))
        if found is not None and _aware(found.expires_at) > datetime.now(UTC):
            return found.user
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Войдите в систему")


def _aware(moment: datetime) -> datetime:
    # SQLite returns naive datetimes even for timezone-aware columns.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def get_project(
    project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Project:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == user.id)
    )
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проект не найден")
    return project
