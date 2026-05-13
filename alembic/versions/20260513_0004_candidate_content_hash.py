"""candidate content_hash UNIQUE (2026-05-13)

Revision ID: 0004_candidate_content_hash
Revises: 0003_drop_embedding_cache
Create Date: 2026-05-13

Добавляет `candidates.content_hash` (sha256 hex от `raw_text`) с UNIQUE
индексом. Цель — снять задвоение кандидатов: одно и то же резюме,
присланное двумя письмами, теперь даст одну row вместо двух.

Шаги:
  1. ADD COLUMN nullable
  2. Backfill: sha256 от raw_text для каждой row (Python-loop, без pgcrypto).
  3. Dedupe: удалить все row кроме min(id) на каждый content_hash.
     `matches` чистится автоматически по FK CASCADE.
     Qdrant points для удалённых candidate_id чистятся отдельным
     post-migration скриптом (см. PROGRESS.md).
  4. SET NOT NULL.
  5. CREATE UNIQUE INDEX.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_candidate_content_hash"
down_revision: str | None = "0003_drop_embedding_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("content_hash", sa.String(64), nullable=True),
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, raw_text FROM candidates WHERE raw_text IS NOT NULL")
    ).fetchall()
    for row in rows:
        h = hashlib.sha256(row.raw_text.encode("utf-8")).hexdigest()
        conn.execute(
            sa.text("UPDATE candidates SET content_hash = :h WHERE id = :id"),
            {"h": h, "id": row.id},
        )

    # Кандидаты без raw_text не должны существовать в боевом потоке, но если
    # есть legacy-rows — оставляем их под удаление (NOT NULL constraint
    # на следующем шаге сломается, и это явный сигнал почистить вручную).
    conn.execute(sa.text("DELETE FROM candidates WHERE content_hash IS NULL"))

    # Dedup: оставляем минимальный id на каждый content_hash. matches CASCADE.
    conn.execute(
        sa.text(
            """
            DELETE FROM candidates
            WHERE id NOT IN (
                SELECT MIN(id) FROM candidates GROUP BY content_hash
            )
            """
        )
    )

    op.alter_column("candidates", "content_hash", nullable=False)
    op.create_index(
        "ix_candidates_content_hash",
        "candidates",
        ["content_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_candidates_content_hash", table_name="candidates")
    op.drop_column("candidates", "content_hash")
