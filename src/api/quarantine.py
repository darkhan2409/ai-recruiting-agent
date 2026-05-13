"""GET /quarantine: read-only список карантинных entries.

Действия (mark_legitimate / standalone delete) — БЛОК 7 (Streamlit page) /
nice-to-have. Жёсткое удаление кандидата с привязанной quarantine — через
`DELETE /candidates/{id}` (каскадно очищает quarantine по
source_message_id).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import Quarantine, get_session

router = APIRouter(prefix="/quarantine", tags=["quarantine"])


class QuarantineEntry(BaseModel):
    """Запись о карантине — битый/подозрительный файл для review."""

    id: int
    source_message_id: str | None
    file_path: str | None
    reason: str
    details: dict[str, Any] | None
    created_at: datetime


@router.get("", response_model=list[QuarantineEntry])
async def list_quarantine(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[QuarantineEntry]:
    """Последние `limit` карантинных entries (created_at desc)."""
    rows = (
        (
            await session.execute(
                select(Quarantine).order_by(Quarantine.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        QuarantineEntry(
            id=r.id,
            source_message_id=r.source_message_id,
            file_path=r.file_path,
            reason=r.reason,
            details=r.details,
            created_at=r.created_at,
        )
        for r in rows
    ]
