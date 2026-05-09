"""Глобальный exception handler для FastAPI.

CLAUDE.md «Ошибки»: try/except на верхнем уровне endpoint достаточно;
кастомных иерархий не плодим. Этот модуль только цепляет обработчик
неожиданных Exception к app — даёт стабильный JSON-ответ + лог.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Возвращает 500 с error_id; логирует traceback с контекстом запроса.

    error_id отдаётся клиенту, чтобы рекрутёр / разработчик мог найти
    конкретный traceback в логах без копания в массиве.
    """
    error_id = uuid.uuid4().hex
    correlation_id = getattr(request.state, "correlation_id", None)
    logger.exception(
        "unhandled_exception error_id=%s correlation_id=%s path=%s method=%s",
        error_id,
        correlation_id,
        request.url.path,
        request.method,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "error_id": error_id,
            "correlation_id": correlation_id,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Подцепить глобальный обработчик к app.

    Args:
        app: Инстанс FastAPI.
    """
    app.add_exception_handler(Exception, _unhandled_exception_handler)
