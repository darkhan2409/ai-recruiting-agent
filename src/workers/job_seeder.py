"""APScheduler-job: загрузка seed-вакансий из JSON-директории.

Запускается один раз в `lifespan` после `init_collection()`. Идемпотентен:
повторный старт обнаруживает уже вставленные вакансии по `title` и пропускает.
Битый JSON логируется и пропускается — остальные файлы загружаются.

PROGRESS.md (БЛОК 6.0): seed-файлы кладутся в bind-mounted `./jobs:/app/jobs:ro`,
порядок sorted-обхода важен для воспроизводимости job_id в golden eval.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import Job as JobORM
from src.schemas import Job

logger = logging.getLogger(__name__)


def _load_job_file(path: Path) -> Job:
    """Распарсить и валидировать один seed-файл.

    Args:
        path: Путь к JSON-файлу с описанием вакансии.

    Returns:
        Валидированный `Job` (без `id` / `embedding_cached` — они проставятся БД).

    Raises:
        json.JSONDecodeError: невалидный JSON.
        pydantic.ValidationError: схема не сошлась.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    # Пользователь может случайно положить `id`/`embedding_cached` в seed —
    # это поля БД-уровня, перетирать их через seed нельзя.
    data.pop("id", None)
    data.pop("embedding_cached", None)
    return Job.model_validate(data)


async def _insert_if_missing(job: Job, session: AsyncSession) -> bool:
    """SELECT по title → INSERT JobORM если нет.

    Идемпотентность по смыслу (title — не UNIQUE constraint в БД, чтобы
    рекрутёр мог иметь два роле «Senior Python» с разными деталями).
    Для seed-файлов однозначность title — соглашение.

    Args:
        job: валидированная Pydantic-схема вакансии.
        session: активная async-сессия.

    Returns:
        True если вставлена новая запись, False если уже была.
    """
    existing = await session.scalar(select(JobORM.id).where(JobORM.title == job.title))
    if existing is not None:
        return False
    session.add(
        JobORM(
            title=job.title,
            description=job.description,
            required_skills=job.required_skills,
            language=job.language.value,
        )
    )
    await session.commit()
    return True


async def seed_jobs_from_directory(
    jobs_dir: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Загрузить все `*.json` из директории в таблицу `jobs`.

    Sorted-обход критичен: при чистой БД id вакансий совпадёт с префиксом
    `01_..10_*.json`, что нужно для воспроизводимости golden eval.

    Args:
        jobs_dir: путь к директории с seed-файлами.
        session_factory: фабрика async-сессий.

    Returns:
        Количество новых вставленных вакансий (повторно — 0).
    """
    root = Path(jobs_dir)
    if not root.is_dir():
        logger.warning("job_seeder: %s is not a directory; skip", jobs_dir)
        return 0

    count = 0
    for path in sorted(root.glob("*.json")):
        try:
            job = _load_job_file(path)
        except Exception:
            logger.warning("job_seeder: skipped broken %s", path.name, exc_info=True)
            continue
        async with session_factory() as session:
            if await _insert_if_missing(job, session):
                count += 1
    logger.info("job_seeder: %d new jobs from %s", count, jobs_dir)
    return count
