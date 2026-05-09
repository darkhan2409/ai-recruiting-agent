"""IMAPSource: production источник через imap-tools.

`imap-tools` — sync-библиотека. По правилу CLAUDE.md «допустим sync внутри
узла» оборачиваем сетевой блок в `asyncio.to_thread`, чтобы не выпадать из
async-контекста worker-а.

Tenacity-retry применён к высокоуровневой операции `_fetch_sync` — он
покрывает и `login`, и `fetch`. Это закрывает временные сетевые сбои, но
НЕ ловит протокольные ошибки (плохой пароль, 535 Authentication failed —
им место в логе и алертах).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from imap_tools import AND, MailBox
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import settings
from src.ingestion.base import RawAttachment, RawEmail

if TYPE_CHECKING:
    from imap_tools.message import MailMessage

logger = logging.getLogger(__name__)


class IMAPSource:
    """Тянет непрочитанные письма из INBOX и собирает RawEmail-список.

    Помечать письмо seen — задача pipeline (после успешной idempotency-записи),
    поэтому здесь только read-only fetch. Если конфигурация не задана —
    ошибка на старте, чтобы прод-режим не уезжал молча на пустой mailbox.
    """

    def __init__(self) -> None:
        if not (settings.imap_user and settings.imap_password and settings.imap_host):
            raise RuntimeError(
                "IMAP not configured: задайте IMAP_HOST/USER/PASSWORD или USE_MOCKS=True"
            )
        self.host = settings.imap_host
        self.port = settings.imap_port
        self.user = settings.imap_user
        self.password = settings.imap_password
        self.folder = settings.imap_folder

    async def poll_once(self) -> list[RawEmail]:
        return await asyncio.to_thread(self._fetch_sync)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((OSError, TimeoutError)),
        reraise=True,
    )
    def _fetch_sync(self) -> list[RawEmail]:
        emails: list[RawEmail] = []
        with MailBox(self.host, self.port).login(self.user, self.password, self.folder) as mb:
            for msg in mb.fetch(AND(seen=False), mark_seen=False):
                try:
                    emails.append(_message_to_email(msg))
                except Exception:
                    logger.exception("imap_source: failed to parse uid=%s", msg.uid)
        logger.info("imap_source: fetched %d unseen messages", len(emails))
        return emails


def _message_to_email(msg: MailMessage) -> RawEmail:
    """Сконвертировать `MailMessage` в RawEmail; пустой Message-ID → uid-fallback."""
    raw_mid = msg.headers.get("message-id", (None,))
    message_id = raw_mid[0] if raw_mid and raw_mid[0] else f"uid:{msg.uid}"
    attachments = [
        RawAttachment(
            filename=att.filename or "attachment.bin",
            content=att.payload,
            content_type=att.content_type or "application/octet-stream",
            size_bytes=att.size,
        )
        for att in msg.attachments
    ]
    return RawEmail(
        message_id=message_id,
        sender=msg.from_,
        subject=msg.subject,
        received_at=msg.date,
        attachments=attachments,
    )
