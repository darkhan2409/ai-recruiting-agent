"""GET/POST/DELETE /jobs + POST /jobs/parse-document (LLM-парсер DOCX).

Production-extraction skills (NER) — БЛОК 10 / roadmap.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import AuditLog, get_session
from src.db import Job as JobORM
from src.parsing.job_parser import get_default_job_parser
from src.parsing.language import detect_language
from src.parsing.text_extract import extract_docx_from_bytes
from src.schemas import JobParsed, Language

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobSummary(BaseModel):
    """Карточка вакансии для UI dropdown."""

    id: int
    title: str
    language: str
    required_skills: list[str]
    created_at: datetime


class JobDetail(BaseModel):
    """Полная карточка вакансии — без `embedding_cached` (1024 floats — мусор для UI)."""

    id: int
    title: str
    description: str
    language: str
    required_skills: list[str] = Field(default_factory=list)
    created_at: datetime


class JobCreate(BaseModel):
    """Тело POST /jobs. Все «рекрутёрские» поля (department, experience_years,
    work_format, salary_range, responsibilities, conditions) опциональны; на
    сервере склеиваются вместе с `description` в единый текст и сохраняются
    в `JobORM.description`. Матчинг-pipeline видит всю информацию через него.

    `required_skills` передаются руками — production-extraction (LLM/NER из
    текста вакансии) в БЛОК 10 / roadmap. `language` опционален: если не
    указан, определяется через langdetect по `description`, fallback EN.
    """

    title: str = Field(
        min_length=1,
        max_length=500,
        description="Название вакансии.",
        examples=["Senior AI Engineer"],
    )
    description: str = Field(
        min_length=20,
        description="Краткое описание (1-2 абзаца). Используется для langdetect.",
        examples=[
            "Развиваем ML-pipeline рекрутинга в HCB: ингест резюме, "
            "матчинг под вакансии, объяснимость для рекрутёра."
        ],
    )
    department: str | None = Field(
        default=None,
        min_length=1,
        description="Отдел / подразделение.",
        examples=["Управление AI-разработки"],
    )
    experience_years: str | None = Field(
        default=None,
        min_length=1,
        description="Требуемый опыт. Свободная строка.",
        examples=["3-5 лет"],
    )
    work_format: str | None = Field(
        default=None,
        min_length=1,
        description="Формат работы: офис / гибрид / удалёнка.",
        examples=["гибрид (3/2)"],
    )
    salary_range: str | None = Field(
        default=None,
        min_length=1,
        description="Зарплатная вилка (свободная строка, валюта/gross-net на усмотрение).",
        examples=["от 500 000 tg gross"],
    )
    responsibilities: str | None = Field(
        default=None,
        min_length=1,
        description="Обязанности (multi-line, поддерживает буллеты через \\n).",
        examples=[
            "- Разработка и интеграция ML-моделей в production\n"
            "- Поддержка матчинг-pipeline и его эволюция\n"
            "- Работа с PII-compliance и DATA_POLICY"
        ],
    )
    conditions: str | None = Field(
        default=None,
        min_length=1,
        description="Условия трудоустройства (multi-line, бенефиты и пр.).",
        examples=[
            "- ДМС со стоматологией\n- Apple-техника\n- Удалённая работа из РФ, гибкое начало дня"
        ],
    )
    required_skills: list[str] = Field(
        default_factory=list,
        description="Список обязательных навыков (lowercase). Используется TF-IDF и LLM-judge.",
        examples=[["python", "fastapi", "pytorch", "llm", "rag"]],
    )
    language: Language | None = Field(
        default=None,
        description="ru | en. Если не указан — langdetect по description, fallback EN.",
        examples=["ru"],
    )


def _build_full_description(body: JobCreate) -> str:
    """Склеить рекрутёрские поля в единый текст для `JobORM.description`.

    Шаблон фиксирован (Должность/Отдел/Опыт/Формат/Зарплата → Обязанности
    → Описание → Условия). Пустые поля опускаются вместе с заголовком.
    Этот текст пойдёт в dense embedding и TF-IDF retriever — pipeline ничего
    не знает о структуре, видит свободный текст.
    """
    header = [f"Должность: {body.title}"]
    if body.department:
        header.append(f"Отдел: {body.department}")
    if body.experience_years:
        header.append(f"Опыт: {body.experience_years}")
    if body.work_format:
        header.append(f"Формат: {body.work_format}")
    if body.salary_range:
        header.append(f"Зарплата: {body.salary_range}")

    parts = ["\n".join(header)]
    if body.responsibilities:
        parts.append(f"Обязанности:\n{body.responsibilities}")
    parts.append(f"Описание:\n{body.description}")
    if body.conditions:
        parts.append(f"Условия:\n{body.conditions}")
    return "\n\n".join(parts)


@router.get("", response_model=list[JobSummary])
async def list_jobs(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[JobSummary]:
    """Последние `limit` вакансий (created_at desc)."""
    rows = (
        (await session.execute(select(JobORM).order_by(JobORM.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [
        JobSummary(
            id=j.id,
            title=j.title,
            language=j.language,
            required_skills=j.required_skills or [],
            created_at=j.created_at,
        )
        for j in rows
    ]


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JobDetail:
    """Полный JSON вакансии по id (404 если нет)."""
    j = await session.get(JobORM, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail=f"job_id={job_id} not found")
    return JobDetail(
        id=j.id,
        title=j.title,
        description=j.description,
        language=j.language,
        required_skills=j.required_skills or [],
        created_at=j.created_at,
    )


@router.post("", response_model=JobDetail, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: JobCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JobDetail:
    """Создать новую вакансию. Embedding посчитается лениво при первом матчинге.

    Все рекрутёрские поля склеиваются в единый текст (см. `_build_full_description`)
    и сохраняются в `JobORM.description` — тот же столбец читают и dense, и TF-IDF.
    `langdetect` запускается на ОРИГИНАЛЬНОМ коротком `body.description`: на
    склеенном тексте русские заголовки могут перебить детекцию EN-вакансии.
    """
    lang = body.language or detect_language(body.description) or Language.EN
    full_text = _build_full_description(body)
    job = JobORM(
        title=body.title,
        description=full_text,
        required_skills=[s.lower() for s in body.required_skills],
        language=lang.value,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return JobDetail(
        id=job.id,
        title=job.title,
        description=job.description,
        language=job.language,
        required_skills=job.required_skills or [],
        created_at=job.created_at,
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Удалить вакансию. CASCADE удалит matches; AuditLog запись обязательна.

    `match_cache` не трогаем — он по `job_text_hash`, протухнет по TTL.
    Qdrant не трогаем — там только candidates, jobs не индексируются.
    """
    j = await session.get(JobORM, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail=f"job_id={job_id} not found")

    title = j.title
    await session.delete(j)
    session.add(
        AuditLog(
            actor=settings.delete_actor_default,
            action="delete_job",
            target_id=str(job_id),
            details={"title": title},
        )
    )
    await session.commit()
    logger.info("delete_job %s (title=%r) — cascade matches removed", job_id, title)


@router.post("/parse-document", response_model=JobParsed)
async def parse_document(
    file: Annotated[UploadFile, File(description="DOCX файл вакансии.")],
) -> JobParsed:
    """Распарсить DOCX-документ вакансии через LLM в структурированные поля.

    Поток: DOCX → mammoth (raw text) → gpt-4o-mini structured output → JobParsed.
    Не пишет в БД — рекрутёр правит форму и подтверждает через POST /jobs.
    USE_MOCKS=True → детерминированный mock без OpenAI.
    """
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=415, detail="Поддерживается только .docx")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    try:
        text = await asyncio.to_thread(extract_docx_from_bytes, data)
    except Exception as exc:
        logger.exception("parse_document: mammoth failed for %s", file.filename)
        raise HTTPException(status_code=422, detail=f"Не удалось прочитать DOCX: {exc}") from exc
    if len(text.strip()) < 20:
        raise HTTPException(status_code=422, detail="Текст документа слишком короткий")
    parser = get_default_job_parser()
    return await parser.parse(text)
