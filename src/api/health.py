"""GET /health — проверка БД, Qdrant и версии приложения.

Используется docker compose healthcheck и человеком на старте демо.

Семантика:
  • Postgres — критическая зависимость (без неё ingestion/API нерабочие).
    Если БД недоступна → HTTP 503, контейнер уйдёт в `unhealthy`.
  • Qdrant — нужен только для матчинга (БЛОК 4+). Его недоступность
    возвращает 200 с `status=degraded`; позволяет сервисам, зависящим от
    api (`streamlit: depends_on api: service_healthy`), нормально стартовать.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.config import settings
from src.db import engine

router = APIRouter(tags=["health"])


async def _check_postgres() -> bool:
    """SELECT 1 в БД; True если живая."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _check_qdrant() -> bool:
    """GET /readyz у Qdrant; True если 200."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{settings.qdrant_url.rstrip('/')}/readyz")
        return r.status_code == 200
    except Exception:
        return False


def _build_payload(pg_ok: bool, qdrant_ok: bool) -> dict[str, Any]:
    if pg_ok and qdrant_ok:
        status = "ok"
    elif pg_ok:
        status = "degraded"
    else:
        status = "down"
    return {
        "status": status,
        "version": settings.app_version,
        "components": {
            "postgres": "ok" if pg_ok else "down",
            "qdrant": "ok" if qdrant_ok else "down",
        },
    }


@router.get("/health")
async def health() -> JSONResponse:
    """Совокупный статус. 503 если Postgres down (api нерабочий), иначе 200."""
    pg_ok = await _check_postgres()
    qdrant_ok = await _check_qdrant()
    payload = _build_payload(pg_ok, qdrant_ok)
    status_code = 200 if pg_ok else 503
    return JSONResponse(status_code=status_code, content=payload)
