"""FastAPI entrypoint + APScheduler в lifespan.

CLAUDE.md: APScheduler в FastAPI-процессе, не отдельный worker-контейнер
(меньше точек отказа на демо). Для прод-масштаба — выносим в arq/celery.

Все реальные endpoint-ы (recommendations / candidates / jobs / quarantine)
подключаются в последующих блоках.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.api.errors import register_exception_handlers
from src.api.health import router as health_router
from src.config import settings
from src.workers.ingestion_tick import ingestion_tick
from src.workers.retention_cleanup import retention_cleanup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Прикладывает correlation_id к каждому запросу для трейсинга в логах."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        cid = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response


def _register_jobs(scheduler: AsyncIOScheduler) -> None:
    """Подцепить периодические задачи. В БЛОКе 1 — заглушки."""
    scheduler.add_job(
        ingestion_tick,
        IntervalTrigger(seconds=settings.ingestion_interval_seconds),
        id="ingestion_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        retention_cleanup,
        CronTrigger(hour=settings.retention_cron_hour, minute=0),
        id="retention_cleanup",
        replace_existing=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Запускает APScheduler на startup и аккуратно останавливает на shutdown."""
    scheduler = AsyncIOScheduler()
    _register_jobs(scheduler)
    scheduler.start()
    logger.info("APScheduler started: ingestion every %ss", settings.ingestion_interval_seconds)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


app = FastAPI(
    title="HCB Recruiting Agent",
    version=settings.app_version,
    lifespan=lifespan,
)
app.add_middleware(CorrelationIdMiddleware)
register_exception_handlers(app)
app.include_router(health_router)
