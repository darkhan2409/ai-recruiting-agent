"""initial schema (БЛОК 1)

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-09

Создаёт 9 таблиц домена: candidates, jobs, matches, processed_emails,
quarantine, dead_letter_emails, audit_log, match_cache, embedding_cache.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("parsed_data", JSONB(), nullable=True),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("source_message_id", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_candidates_source_message_id", "candidates", ["source_message_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required_skills", JSONB(), nullable=False, server_default="[]"),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("embedding_cached", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "matches",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "candidate_id",
            sa.BigInteger(),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.BigInteger(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_matches_candidate_id", "matches", ["candidate_id"])
    op.create_index("ix_matches_job_id", "matches", ["job_id"])

    op.create_table(
        "processed_emails",
        sa.Column("message_id", sa.String(500), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "quarantine",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_message_id", sa.String(500), nullable=True),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("reason", sa.String(50), nullable=False),
        sa.Column("details", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_quarantine_source_message_id", "quarantine", ["source_message_id"])

    op.create_table(
        "dead_letter_emails",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_message_id", sa.String(500), nullable=False),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_dead_letter_emails_source_message_id",
        "dead_letter_emails",
        ["source_message_id"],
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=True),
        sa.Column("details", JSONB(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_log_target_id", "audit_log", ["target_id"])

    op.create_table(
        "match_cache",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

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


def downgrade() -> None:
    op.drop_table("embedding_cache")
    op.drop_table("match_cache")
    op.drop_index("ix_audit_log_target_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_dead_letter_emails_source_message_id", table_name="dead_letter_emails")
    op.drop_table("dead_letter_emails")
    op.drop_index("ix_quarantine_source_message_id", table_name="quarantine")
    op.drop_table("quarantine")
    op.drop_table("processed_emails")
    op.drop_index("ix_matches_job_id", table_name="matches")
    op.drop_index("ix_matches_candidate_id", table_name="matches")
    op.drop_table("matches")
    op.drop_table("jobs")
    op.drop_index("ix_candidates_source_message_id", table_name="candidates")
    op.drop_table("candidates")
