"""processed_emails.updated_at (audit P6, 2026-05-10)

Revision ID: 0002_processed_emails_updated_at
Revises: 0001_initial
Create Date: 2026-05-10

Добавляет `updated_at` колонку в `processed_emails` для retention cleanup
(БЛОК 10.4): нужно отличать «давно упало в dead_letter» от «свежее».

Backfill: existing rows получают `updated_at = created_at` (логичнее, чем
`now()`, который перезаписал бы исторические данные).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_processed_emails_updated_at"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processed_emails",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.execute("UPDATE processed_emails SET updated_at = created_at")
    op.alter_column("processed_emails", "updated_at", nullable=False)


def downgrade() -> None:
    op.drop_column("processed_emails", "updated_at")
