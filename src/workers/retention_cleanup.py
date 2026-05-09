"""APScheduler-job: ночная очистка просроченных кандидатов.

Заглушка для БЛОКа 1; реальная реализация — БЛОК 10.4 (DATA_POLICY:
RESUME_RETENTION_DAYS=180, тот же каскад что DELETE /candidates/{id}).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def retention_cleanup() -> None:
    """Удалить кандидатов старше RESUME_RETENTION_DAYS. Реализация — БЛОК 10.4."""
    try:
        logger.debug("retention_cleanup: stub (БЛОК 10.4 заменит реализацию)")
    except Exception:
        logger.exception("retention_cleanup failed")
