from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
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
    # Off by default: a project only reads its portal until the owner allows changes.
    allow_write: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    runs: Mapped[list[Run]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Run.id.desc()"
    )


class Run(Base):
    """One apply or rollback: what the portal was before, the plan, progress and outcome."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # apply | rollback
    # The run whose "before" a rollback restores.
    rollback_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # pending | running | verified | diverged | failed | interrupted | unchanged
    status: Mapped[str] = mapped_column(String(16), default="pending")
    allow_destructive: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    desired_spec: Mapped[dict[str, Any]] = mapped_column(Json)
    before_spec: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    plan: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    # Addresses of carried-out changes, in order, and the one that failed.
    done: Mapped[list[str]] = mapped_column(Json, default=list)
    failed: Mapped[str | None] = mapped_column(Text, nullable=True)
    journal: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    residual: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(Json, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="runs")
