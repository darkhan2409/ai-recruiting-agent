"""drop embedding_cache table (2026-05-12)

Revision ID: 0003_drop_embedding_cache
Revises: 0002_processed_emails_updated_at
Create Date: 2026-05-12

Passage-кэш по hash(text) для резюме оказался ненужен на практике: при
ре-обработке файла создаётся новая Candidate row (новый file_path),
кэш-hit маловероятен, а write-нагрузка на каждый encode сохранялась.
Encode идёт напрямую через `embedder.embed_passage()`. Кэш вакансий
(колонка `jobs.embedding_cached`) сохраняется — он по job_id, 1:1 с row,
там кэш-hit реален.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0003_drop_embedding_cache"
down_revision: str | None = "0002_processed_emails_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("embedding_cache")


def downgrade() -> None:
    op.create_table(
        "embedding_cache",
        sa.Column("text_hash", sa.String(64), primary_key=True),
        sa.Column("vector", JSONB(), nullable=False),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
