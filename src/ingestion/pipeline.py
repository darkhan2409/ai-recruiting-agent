"""Оркестратор ingestion: source → idempotency → save → DB.

Логика:
  1. `poll_once_default()` выбирает источник (FolderSource при USE_MOCKS,
     иначе IMAPSource) и возвращает counters.
  2. `poll_and_process` обходит письма; на каждое — отдельная транзакция,
     чтобы один сбой не откатывал успешные.
  3. `process_email` валидирует attachments по расширению и размеру,
     невалидные → quarantine, валидные → файл на диск + Candidate row.
  4. `record_failure` инкрементирует attempts, после `MAX_ATTEMPTS` —
     письмо уходит в `dead_letter_emails`. Worker-tick никогда не падает.

Парсинг (raw_text / parsed_data) намеренно отложен на БЛОК 3: здесь только
intake-уровень — приём, идемпотентность, базовая валидация.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import Candidate, DeadLetter, ProcessedEmail, Quarantine, session_factory
from src.ingestion.base import (
    ALLOWED_EXTS,
    MAX_ATTEMPTS,
    TERMINAL_STATUSES,
    IngestionSource,
    QuarantineReason,
    RawAttachment,
    RawEmail,
)
from src.ingestion.folder_source import FolderSource
from src.ingestion.imap_source import IMAPSource
from src.matching.embedding import get_default_embedder
from src.matching.qdrant_store import upsert_resume
from src.parsing.pipeline import ParseSuccess, parse_resume
from src.utils.pii import mask_pii

logger = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_id(message_id: str) -> str:
    """Превратить message_id в безопасный сегмент пути (без `:` `/` `<>`)."""
    return _UNSAFE_CHARS.sub("_", message_id).strip("_") or "unknown"


async def is_terminal(session: AsyncSession, message_id: str) -> bool:
    """True если письмо уже в финальном статусе (ingested/quarantined/dead_letter)."""
    row = await session.scalar(
        select(ProcessedEmail.status).where(ProcessedEmail.message_id == message_id)
    )
    return row in TERMINAL_STATUSES


async def upsert_processed(
    session: AsyncSession,
    *,
    message_id: str,
    status: str,
    error: str | None = None,
) -> None:
    """INSERT … ON CONFLICT DO UPDATE по `message_id`.

    `updated_at` обновляется явно через `func.now()` — ORM `onupdate` не
    срабатывает на core-уровне `pg_insert.on_conflict_do_update`.
    """
    stmt = pg_insert(ProcessedEmail).values(
        message_id=message_id, status=status, error=error, attempts=0
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["message_id"],
        set_={"status": status, "error": error, "updated_at": func.now()},
    )
    await session.execute(stmt)


async def record_quarantine(
    session: AsyncSession,
    *,
    message_id: str,
    file_path: str | None,
    reason: QuarantineReason,
    details: dict[str, Any] | None,
) -> None:
    """Записать карантин-row + перевести письмо в терминальный quarantined."""
    session.add(
        Quarantine(
            source_message_id=message_id,
            file_path=file_path,
            reason=reason.value,
            details=details,
        )
    )
    await upsert_processed(session, message_id=message_id, status="quarantined")


def _build_dead_letter_payload(email: RawEmail | None) -> dict[str, Any] | None:
    """Собрать PII-safe forensics payload для dead_letter_emails.

    Сохраняем только метаданные: sender, subject, received_at и список
    attachments с filename/content_type/size_bytes — БЕЗ raw bytes (PII).
    `received_at` сериализуется в ISO 8601 для JSONB-совместимости.
    """
    if email is None:
        return None
    return {
        "sender": email.sender,
        "subject": email.subject,
        "received_at": email.received_at.isoformat() if email.received_at else None,
        "attachments": [
            {
                "filename": att.filename,
                "content_type": att.content_type,
                "size_bytes": att.size_bytes,
            }
            for att in email.attachments
        ],
    }


async def record_failure(
    session: AsyncSession,
    *,
    message_id: str,
    error: str,
    email: RawEmail | None = None,
) -> None:
    """Инкрементировать attempts; после MAX_ATTEMPTS — dead-letter.

    Args:
        session: Async-сессия.
        message_id: Идентификатор письма.
        error: Сообщение об ошибке (уйдёт в `processed_emails.error` и
            `dead_letter_emails.reason`). Вызывающий код обязан передать уже
            замаскированную через `mask_pii` строку (DATA_POLICY §«Логи»).
        email: Опциональный RawEmail. Если передан и письмо уходит в
            dead-letter — попадает в `payload` (PII-safe metadata, без bytes).
    """
    stmt = (
        pg_insert(ProcessedEmail)
        .values(message_id=message_id, status="failed", error=error, attempts=1)
        .on_conflict_do_update(
            index_elements=["message_id"],
            set_={
                "status": "failed",
                "error": error,
                "attempts": ProcessedEmail.__table__.c.attempts + 1,
                "updated_at": func.now(),
            },
        )
    )
    await session.execute(stmt)

    attempts = await session.scalar(
        select(ProcessedEmail.attempts).where(ProcessedEmail.message_id == message_id)
    )
    if attempts is not None and attempts >= MAX_ATTEMPTS:
        session.add(
            DeadLetter(
                source_message_id=message_id,
                payload=_build_dead_letter_payload(email),
                reason=error[:1000],
            )
        )
        await session.execute(
            update(ProcessedEmail)
            .where(ProcessedEmail.message_id == message_id)
            .values(status="dead_letter", updated_at=func.now())
        )


def _save_attachment(att: RawAttachment, message_id: str) -> Path:
    """Сохранить attachment на диск; синхронно (write_bytes — мгновенно)."""
    target = Path(settings.resumes_dir) / _safe_id(message_id) / att.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(att.content)
    return target


async def process_email(session: AsyncSession, email: RawEmail) -> None:
    """Обработать одно письмо: валидация → сохранение → парсинг → Candidate.

    Парсинг (БЛОК 3) встроен сюда: после `_save_attachment` вызывается
    `parse_resume`. Успех → создаём Candidate с заполненными raw_text и
    parsed_data. Неудача → quarantine с правильным reason, файл остаётся
    на диске для review.

    Финальный статус письма в `processed_emails`:
      • все attachments → ParseSuccess → `ingested`;
      • хотя бы один → ParseFailure → `quarantined` (терминальный); даже если
        другие attachments успешные, мы НЕ перезаписываем на `ingested` —
        сохраняем сигнал, что письмо требовало review. Кандидаты для
        успешных attachments всё равно создаются.
    """
    max_bytes = settings.max_attachment_mb * 1024 * 1024
    candidates_added = 0
    duplicates_skipped = 0
    quarantined_any = False
    for att in email.attachments:
        ext = Path(att.filename).suffix.lower()
        if ext not in ALLOWED_EXTS:
            await record_quarantine(
                session,
                message_id=email.message_id,
                file_path=None,
                reason=QuarantineReason.UNSUPPORTED_MIME,
                details={"filename": att.filename, "content_type": att.content_type},
            )
            return
        if att.size_bytes > max_bytes:
            await record_quarantine(
                session,
                message_id=email.message_id,
                file_path=None,
                reason=QuarantineReason.TOO_LARGE,
                details={"filename": att.filename, "size_bytes": att.size_bytes},
            )
            return
        path = _save_attachment(att, email.message_id)
        parse = await parse_resume(path)
        if isinstance(parse, ParseSuccess):
            content_hash = hashlib.sha256(parse.text.encode("utf-8")).hexdigest()
            stmt = (
                pg_insert(Candidate)
                .values(
                    file_path=str(path),
                    source_message_id=email.message_id,
                    language=parse.language.value,
                    raw_text=parse.text,
                    parsed_data=parse.resume.model_dump(mode="json"),
                    content_hash=content_hash,
                )
                .on_conflict_do_nothing(index_elements=["content_hash"])
                .returning(Candidate.id)
            )
            new_id = await session.scalar(stmt)
            if new_id is None:
                # Дубль: тот же контент уже есть в БД и в Qdrant. Не плодим
                # row, не дёргаем embedder, не трогаем Qdrant.
                logger.info(
                    "ingestion: duplicate content_hash=%s skip (message_id=%s, file=%s)",
                    content_hash[:8],
                    email.message_id,
                    path.name,
                )
                duplicates_skipped += 1
                continue
            embedder = get_default_embedder()
            vector = await embedder.embed_passage(parse.text)
            await upsert_resume(
                candidate_id=new_id,
                vector=vector,
                payload={
                    "candidate_id": new_id,
                    "language": parse.language.value,
                    "source_message_id": email.message_id,
                },
            )
            candidates_added += 1
        else:
            await record_quarantine(
                session,
                message_id=email.message_id,
                file_path=str(path),
                reason=parse.reason,
                details=parse.details,
            )
            quarantined_any = True
    if not quarantined_any:
        await upsert_processed(session, message_id=email.message_id, status="ingested")
    logger.info(
        "ingestion: processed message_id=%s candidates=%d duplicates=%d quarantined=%s",
        email.message_id,
        candidates_added,
        duplicates_skipped,
        quarantined_any,
    )


async def poll_and_process(source: IngestionSource) -> dict[str, int]:
    """Главный цикл: source → per-email транзакция → счётчики.

    TODO (БЛОК 6): между `is_terminal` и `commit` нет блокировки. Сейчас
    `APScheduler.max_instances=1` (default) исключает параллельные тики, но
    в БЛОКе 6 появится `POST /sync-mail` — recruiter может дёрнуть его
    параллельно с тиком и одно письмо пройдёт через `process_email` дважды
    (на `processed_emails` сработает upsert, но `Candidate` НЕ имеет UNIQUE
    на `source_message_id` → возможен дубль). Решение: либо `SELECT ... FOR
    UPDATE` на `processed_emails.message_id` под is_terminal-чек, либо
    capture-pattern (`INSERT ... ON CONFLICT DO NOTHING` со статусом
    `processing` до начала работы). Отложено до БЛОКа 6 со своим ADR.
    """
    counts = {"processed": 0, "skipped": 0, "failed": 0}
    emails = await source.poll_once()
    for email in emails:
        async with session_factory() as session:
            try:
                if await is_terminal(session, email.message_id):
                    counts["skipped"] += 1
                    continue
                await process_email(session, email)
                await session.commit()
                counts["processed"] += 1
            except Exception as exc:
                await session.rollback()
                logger.exception("ingestion: failed message_id=%s", email.message_id)
                # str(exc) может содержать фрагменты текста резюме
                # (DATA_POLICY violation) — маскируем email/phone до записи в
                # processed_emails.error.
                async with session_factory() as fs:
                    await record_failure(
                        fs,
                        message_id=email.message_id,
                        error=mask_pii(str(exc)),
                        email=email,
                    )
                    await fs.commit()
                counts["failed"] += 1
    return counts


async def poll_once_default() -> dict[str, int]:
    """Точка входа для `ingestion_tick` и `POST /sync-mail`.

    Источник определяется `settings.ingestion_source`:
      • "imap" — живой IMAPSource;
      • "folder" — FolderSource из `inbox_dir`;
      • "auto" — folder при `use_mocks=True`, иначе imap (backwards-compat).
    """
    mode = settings.ingestion_source
    if mode == "auto":
        mode = "folder" if settings.use_mocks else "imap"
    source: IngestionSource = FolderSource() if mode == "folder" else IMAPSource()
    return await poll_and_process(source)
