from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from crmbuilder_web.db import Base

# JSONB on PostgreSQL, plain JSON elsewhere (SQLite in tests).
Json = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    projects: Mapped[list[Project]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class LoginSession(Base):
    __tablename__ = "sessions"

    # SHA-256 of the cookie token: a leaked database does not leak live sessions.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class Project(Base):
    """A client portal: its webhook, the spec (desired configuration) and the state."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    portal: Mapped[str] = mapped_column(String(255))
    lang: Mapped[str] = mapped_column(String(8), default="ru")
    # Encrypted with the server secret: the webhook gives full access to the portal.
    webhook_encrypted: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    state: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(back_populates="projects")
