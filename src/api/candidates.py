"""GET /candidates list + DELETE /candidates/{id} каскадно.

DELETE: Postgres CASCADE удаляет matches; явно — quarantine по
source_message_id; Qdrant point — best-effort (не блокирует DB DELETE);
файл — после commit; AuditLog запись обязательна (DATA_POLICY).
"""

from __future__ import annotations

import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import AuditLog, Candidate, Quarantine, get_session
from src.matching import qdrant_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/candidates", tags=["candidates"])


class CandidateSummary(BaseModel):
    """Карточка кандидата для UI dropdown / списков."""

    id: int
    full_name: str | None
    language: str | None
    file_path: str | None
    created_at: datetime


@router.get("", response_model=list[CandidateSummary])
async def list_candidates(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[CandidateSummary]:
    """Последние `limit` кандидатов (created_at desc). full_name из parsed_data."""
    rows = (
        (
            await session.execute(
                select(Candidate).order_by(Candidate.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        CandidateSummary(
            id=c.id,
            full_name=(c.parsed_data or {}).get("full_name"),
            language=c.language,
            file_path=c.file_path,
            created_at=c.created_at,
        )
        for c in rows
    ]


@router.delete("/{candidate_id}", status_code=204)
async def delete_candidate(
    candidate_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Каскадное удаление кандидата.

    Порядок:
        1. SELECT кандидата (404 если нет).
        2. Запомнить `file_path`, `source_message_id`.
        3. Qdrant DELETE — best-effort (Qdrant down → лог + продолжаем).
        4. Postgres DELETE quarantine WHERE source_message_id = ... (явно).
        5. Postgres DELETE candidate → CASCADE на matches.
        6. AuditLog запись.
        7. commit.
        8. unlink файла с диска (после commit, missing_ok).

    `match_cache` и `processed_emails` не трогаем: первый протухнет по TTL,
    второй — idempotency journal (удаление = повторная обработка письма).
    """
    cand = await session.get(Candidate, candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail=f"candidate_id={candidate_id} not found")

    file_path = cand.file_path
    source_message_id = cand.source_message_id

    try:
        await qdrant_store.delete_resume(candidate_id)
    except Exception:
        logger.exception(
            "delete_candidate %s: qdrant delete failed; continuing with DB delete",
            candidate_id,
        )

    if source_message_id:
        await session.execute(
            delete(Quarantine).where(Quarantine.source_message_id == source_message_id)
        )

    await session.delete(cand)

    session.add(
        AuditLog(
            actor=settings.delete_actor_default,
            action="delete_candidate",
            target_id=str(candidate_id),
            details={"file_path": file_path, "source_message_id": source_message_id},
        )
    )

    await session.commit()

    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception:
            logger.exception(
                "delete_candidate %s: file unlink failed for %s",
                candidate_id,
                file_path,
            )


@router.get("/{candidate_id}/file")
async def download_candidate_file(
    candidate_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    """Скачать оригинальный файл резюме. Логируем доступ в audit_log (DATA_POLICY)."""
    cand = await session.get(Candidate, candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail=f"candidate_id={candidate_id} not found")
    if not cand.file_path or not Path(cand.file_path).exists():
        raise HTTPException(status_code=404, detail="file not available")

    media_type, _ = mimetypes.guess_type(cand.file_path)
    media_type = media_type or "application/octet-stream"

    session.add(
        AuditLog(
            actor=settings.delete_actor_default,
            action="download_candidate_file",
            target_id=str(candidate_id),
            details={"file_path": cand.file_path},
        )
    )
    await session.commit()

    return FileResponse(
        path=cand.file_path,
        filename=Path(cand.file_path).name,
        media_type=media_type,
    )
