"""GET + POST /recommendations: top-k кандидатов под вакансию.

GET принимает job_id (для seeded/saved jobs); POST принимает job_text + опц.
title/required_skills (ad-hoc для live-demo). Оба endpoint поддерживают
`method` query/body param — единственное место в продукте, где живо
сравниваются 4 подхода матчинга на одних данных.

Persist Match rows только для GET (есть job_id для FK). Ad-hoc возврат
напрямую без записи в БД.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import Job as JobORM
from src.db import Match, get_session
from src.matching.pipeline import Method, find_candidates
from src.parsing.language import detect_language
from src.schemas import Job, Language, MatchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


class JobAdHocRequest(BaseModel):
    """Тело POST /recommendations: ad-hoc вакансия по тексту."""

    job_text: str = Field(min_length=20, description="Полный текст вакансии (мин. 20 символов).")
    title: str | None = Field(default=None, description="Название (опц., default 'ad-hoc').")
    required_skills: list[str] = Field(
        default_factory=list,
        description="Подсказка скиллов для LLM-judge (опц., можно пусто).",
    )
    top_k: int = Field(default=5, ge=1, le=50)
    method: Literal["dense", "tfidf", "llm", "hybrid"] = "hybrid"
    min_score: float = Field(default=0.45, ge=0.0, le=1.0)


class RecommendationsResponse(BaseModel):
    """Универсальный ответ для GET и POST."""

    job_id: int | None = Field(description="ID вакансии в БД (None для ad-hoc).")
    method: str = Field(description="Применённый метод матчинга.")
    min_score: float = Field(description="Применённый порог отсечения.")
    results: list[MatchResult] = Field(description="Top-k кандидатов, отсортированы по score desc.")


def _orm_to_job(orm: JobORM) -> Job:
    """ORM Job → Pydantic Job (Language enum конверсия + safe defaults)."""
    return Job(
        id=orm.id,
        title=orm.title,
        description=orm.description,
        required_skills=orm.required_skills or [],
        language=Language(orm.language),
        embedding_cached=orm.embedding_cached,
    )


def _persist_matches(
    session: AsyncSession,
    job_id: int,
    method: str,
    results: list[MatchResult],
) -> None:
    """Записать Match rows для аудита/будущего eval. Только для GET (есть job_id FK)."""
    if not results:
        return
    session.add_all(
        [
            Match(
                candidate_id=r.candidate_id,
                job_id=job_id,
                method=method,
                score=r.score,
                result=r.model_dump(mode="json"),
            )
            for r in results
        ]
    )


@router.get("", response_model=RecommendationsResponse)
async def get_recommendations(
    session: Annotated[AsyncSession, Depends(get_session)],
    job_id: Annotated[int, Query(ge=1, description="ID существующей вакансии.")],
    top_k: Annotated[int, Query(ge=1, le=50)] = 5,
    method: Annotated[Method, Query()] = "hybrid",
    min_score: Annotated[float, Query(ge=0.0, le=1.0)] = 0.45,
) -> RecommendationsResponse:
    """Top-k кандидатов под сохранённую вакансию.

    Persist Match rows для аудита; на повторных вызовах создаются новые
    rows (decision history, не upsert).
    """
    orm = await session.get(JobORM, job_id)
    if orm is None:
        raise HTTPException(status_code=404, detail=f"job_id={job_id} not found")
    job = _orm_to_job(orm)
    results = await find_candidates(job, session, top_k=top_k, method=method, min_score=min_score)
    _persist_matches(session, job_id=job_id, method=method, results=results)
    await session.commit()
    return RecommendationsResponse(
        job_id=job_id, method=method, min_score=min_score, results=results
    )


@router.post("", response_model=RecommendationsResponse)
async def post_recommendations(
    req: JobAdHocRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RecommendationsResponse:
    """Top-k кандидатов под произвольный текст вакансии (без сохранения)."""
    lang = detect_language(req.job_text) or Language.EN
    job = Job(
        id=None,
        title=req.title or "ad-hoc",
        description=req.job_text,
        required_skills=req.required_skills,
        language=lang,
    )
    results = await find_candidates(
        job, session, top_k=req.top_k, method=req.method, min_score=req.min_score
    )
    # Не persist — нет job_id для FK. Не commit — pipeline сам делает кэш-апдейты.
    await session.commit()
    return RecommendationsResponse(
        job_id=None, method=req.method, min_score=req.min_score, results=results
    )
