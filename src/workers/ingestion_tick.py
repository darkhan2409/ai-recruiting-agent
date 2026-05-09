"""APScheduler-job: периодический тик ingestion.

Дёргает единый pipeline-вход `poll_once_default()` — ту же кодовую дорожку,
что используется в `POST /sync-mail` (БЛОК 6.3). Try/except на верхнем
уровне: одно битое резюме / падение IMAP не должно ронять worker.
"""

from __future__ import annotations

import logging

from src.ingestion.pipeline import poll_once_default

logger = logging.getLogger(__name__)


async def ingestion_tick() -> None:
    """Один тик: pull → process → лог counters."""
    try:
        counts = await poll_once_default()
        if counts["processed"] or counts["failed"]:
            logger.info("ingestion_tick: %s", counts)
        else:
            logger.debug("ingestion_tick: %s", counts)
    except Exception:
        logger.exception("ingestion_tick: unhandled error")
