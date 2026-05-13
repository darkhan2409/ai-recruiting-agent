"""POST /sync-mail: принудительный триггер ingestion poll.

Снимает «минуту тишины» APScheduler tick на live-demo. Использует ту же
кодовую дорожку `poll_once_default` что и периодический tick — race
безопасна за счёт `processed_emails` PK + idempotency.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.ingestion.pipeline import poll_once_default

router = APIRouter(tags=["ingestion"])


@router.post("/sync-mail")
async def sync_mail() -> dict[str, int]:
    """Запустить poll IMAP/Folder source и вернуть counts.

    Returns:
        `{"processed": N, "skipped": M, "failed": K}` — те же ключи, что
        пишет в лог APScheduler `ingestion_tick`.
    """
    return await poll_once_default()
