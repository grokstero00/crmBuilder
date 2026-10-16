"""jobs (AI design, audit, report)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("params", JSON, nullable=False),
        sa.Column("base_fingerprint", sa.String(64), nullable=True),
        sa.Column("questions", JSON, nullable=True),
        sa.Column("answers", JSON, nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("log", JSON, nullable=False),
        sa.Column("result", JSON, nullable=True),
        sa.Column("usage", JSON, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])


def downgrade() -> None:
    op.drop_table("jobs")
