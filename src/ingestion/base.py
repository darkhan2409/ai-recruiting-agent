"""Контракт ingestion-источников + общие константы.

`IngestionSource` — единственная абстракция, которой владеет верхний уровень
pipeline-а. Реализации (IMAPSource, FolderSource) ничего не знают про БД и
файловую систему: только умеют выдать набор `RawEmail` за один опрос.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol


@dataclass(slots=True)
class RawAttachment:
    """Сырое вложение письма / файл из inbox-каталога."""

    filename: str
    content: bytes
    content_type: str
    size_bytes: int


@dataclass(slots=True)
class RawEmail:
    """Сырое письмо (или файл-как-письмо для FolderSource).

    `message_id` — стабильный идентификатор для idempotency. Для IMAP это
    заголовок Message-ID (или `uid:<uid>` если заголовок отсутствует), для
    FolderSource — `folder:<sha1(path|mtime)>`.

    `received_at` опционален: некоторые письма приходят без `Date:`-header.
    """

    message_id: str
    sender: str | None
    subject: str | None
    received_at: datetime | None
    attachments: list[RawAttachment] = field(default_factory=list)


class IngestionSource(Protocol):
    """Источник писем. Один метод — один опрос.

    Реализации должны быть idempotency-safe сами по себе (дважды позвал —
    не получил дубликаты), но финальная защита — таблица `processed_emails`.
    """

    async def poll_once(self) -> list[RawEmail]: ...


# Whitelist расширений — единственное место, где он определён.
ALLOWED_EXTS: frozenset[str] = frozenset({".pdf", ".docx", ".txt"})

# Сколько раз ретраим обработку одного письма перед dead-letter.
MAX_ATTEMPTS: int = 3


class QuarantineReason(StrEnum):
    """Причины помещения файла в карантин.

    В БЛОКе 2 используются только `unsupported_mime` и `too_large`; остальные
    подключаются в БЛОКе 3 (text extraction / sanitize / langdetect).
    """

    TEXT_TOO_SHORT = "text_too_short"
    VLM_EXTRACT_FAILED = "vlm_extract_failed"
    UNSUPPORTED_MIME = "unsupported_mime"
    TOO_LARGE = "too_large"
    PROMPT_INJECTION_SUSPECTED = "prompt_injection_suspected"
    LANG_UNKNOWN = "lang_unknown"
    EXTRACT_FAILED = "extract_failed"


# Терминальные статусы processed_emails: повторная обработка пропускается.
TERMINAL_STATUSES: frozenset[str] = frozenset({"ingested", "quarantined", "dead_letter"})
