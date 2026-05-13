"""SQLAlchemy 2.0 ORM-модели и async session factory.

Все модели в одном файле по правилу CLAUDE.md «не дробить на много файлов
то, что помещается в 2». Каждый домен (ingestion / parsing / matching) сам
пишет в БД через session-инъекцию — отдельного repo-слоя нет.

JSONB используется для всех структурированных полей PII / результатов
матчинга / эмбеддингов: выбор pgvector сознательно отвергнут (см. ADR-2),
векторный поиск идёт через Qdrant.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import settings


class Base(DeclarativeBase):
    """Базовый класс ORM. Один на весь проект."""


class Candidate(Base):
    """Кандидат: оригинальный файл + извлечённый текст + структурированный профиль."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_path: Mapped[str | None] = mapped_column(String(500))
    raw_text: Mapped[str | None] = mapped_column(Text)
    parsed_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    language: Mapped[str | None] = mapped_column(String(10))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_message_id: Mapped[str | None] = mapped_column(String(500), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Job(Base):
    """Вакансия: текст + извлечённые навыки + кэш эмбеддинга."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    required_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    language: Mapped[str] = mapped_column(String(10))
    embedding_cached: Mapped[list[float] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Match(Base):
    """Результат матчинга кандидата под вакансию (полный MatchResult в JSONB)."""

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(20))
    score: Mapped[float] = mapped_column(Float)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessedEmail(Base):
    """Idempotency-журнал писем: повторная обработка одного message_id невозможна."""

    __tablename__ = "processed_emails"

    message_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # `updated_at` нужен для retention cleanup (БЛОК 10.4) — отличить «давно
    # упало в dead_letter» от «свежее». ORM-уровень `onupdate` не сработает
    # для raw SQL `update()`/`pg_insert.on_conflict_do_update`, поэтому
    # вызывающий код явно проставляет `updated_at=func.now()` в set_/values.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Quarantine(Base):
    """Карантин: битые / подозрительные файлы для ручного review рекрутёром."""

    __tablename__ = "quarantine"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_message_id: Mapped[str | None] = mapped_column(String(500), index=True)
    file_path: Mapped[str | None] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(String(50))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeadLetter(Base):
    """Dead-letter: письма, не обработанные после 3 попыток."""

    __tablename__ = "dead_letter_emails"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_message_id: Mapped[str] = mapped_column(String(500), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Аудит-лог: кто, что и когда сделал (DELETE кандидата, mark legitimate, ...)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(100), index=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MatchCache(Base):
    """Кэш LLM-judge: hash(resume_id, job_id, model_version, prompt_version) → result."""

    __tablename__ = "match_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(50))
    prompt_version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Async engine + session factory ---

engine: AsyncEngine = create_async_engine(settings.database_url, echo=False, future=True)
session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Фабрика для FastAPI Depends (БЛОК 6).

    Yields:
        Открытую AsyncSession; коммит и rollback — на стороне вызывающего
        endpoint, чтобы транзакционные границы были явными.
    """
    async with session_factory() as session:
        yield session
