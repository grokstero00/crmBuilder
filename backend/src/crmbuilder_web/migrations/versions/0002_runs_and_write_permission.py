"""runs (apply history) and per-project write permission

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("allow_write", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.create_table(
        "runs",
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
        sa.Column("rollback_of", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("allow_destructive", sa.Boolean(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("desired_spec", JSON, nullable=False),
        sa.Column("before_spec", JSON, nullable=True),
        sa.Column("before_state", JSON, nullable=True),
        sa.Column("plan", JSON, nullable=True),
        sa.Column("done", JSON, nullable=False),
        sa.Column("failed", sa.Text(), nullable=True),
        sa.Column("journal", JSON, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("residual", JSON, nullable=True),
        sa.Column("warnings", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_runs_project_id", "runs", ["project_id"])


def downgrade() -> None:
    op.drop_table("runs")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("allow_write")
