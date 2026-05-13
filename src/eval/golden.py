"""Golden labels: загрузка + резолв filename → id из БД.

Labels хранятся в `tests/fixtures/golden/labels.json` в filename-формате
(`resume_filename`, `job_filename`), чтобы переживать пересборку БД и ID-shift
при quarantine отдельных кандидатов. Здесь резолвим filenames в реальные
`candidates.id` / `jobs.id` для прогона `find_candidates`.

Несуществующие имена логируются и попадают в `missing_*` — caller решает,
проваливать ли eval или продолжать с уменьшенной выборкой.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import Candidate
from src.db import Job as JobORM
from src.workers.job_seeder import _load_job_file

logger = logging.getLogger(__name__)

DEFAULT_LABELS_PATH = Path("tests/fixtures/golden/labels.json")


@dataclass(frozen=True, slots=True)
class GoldenEntry:
    """Одна пара (резюме, вакансия) с graded label."""

    resume_filename: str
    job_filename: str
    label: float
    rationale: str


@dataclass(slots=True)
class GoldenSet:
    """Резолвленный golden dataset.

    Структуры:
    - `relevance[job_id][candidate_id] = label` — для метрик
    - `job_id_to_filename` / `candidate_id_to_filename` — для отчётов
    - `jobs[job_id] = Job` (Pydantic) — пробрасываем в find_candidates
    - `missing_resumes` / `missing_jobs` — filenames без id-резолва
    """

    relevance: dict[int, dict[int, float]] = field(default_factory=dict)
    job_id_to_filename: dict[int, str] = field(default_factory=dict)
    candidate_id_to_filename: dict[int, str] = field(default_factory=dict)
    jobs: dict[int, object] = field(default_factory=dict)
    missing_resumes: set[str] = field(default_factory=set)
    missing_jobs: set[str] = field(default_factory=set)
    skipped_entries: list[GoldenEntry] = field(default_factory=list)


def load_entries(path: Path = DEFAULT_LABELS_PATH) -> list[GoldenEntry]:
    """Прочитать JSON и валидировать ключи / типы."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries: list[GoldenEntry] = []
    for i, item in enumerate(raw):
        try:
            entries.append(
                GoldenEntry(
                    resume_filename=item["resume_filename"],
                    job_filename=item["job_filename"],
                    label=float(item["label"]),
                    rationale=item["rationale"],
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"golden entry #{i} malformed: {exc}") from exc
    return entries


async def _candidate_basename_index(session: AsyncSession) -> dict[str, int]:
    """{basename(file_path) → candidate.id}. PostgreSQL hits ~mks."""
    rows = (await session.execute(select(Candidate.id, Candidate.file_path))).all()
    return {Path(fp).name: cid for cid, fp in rows if fp}


async def _job_title_index(session: AsyncSession) -> dict[str, int]:
    """{job.title → job.id}. Используется для резолва job_filename → id
    через сопоставление title в JSON-сидере."""
    rows = (await session.execute(select(JobORM.id, JobORM.title))).all()
    return {title: jid for jid, title in rows}


def _resolve_job_filename_to_title(filename: str, jobs_dir: Path) -> str | None:
    """Прочитать seed-JSON → вернуть title. None если файл не существует."""
    path = jobs_dir / filename
    if not path.is_file():
        return None
    try:
        return _load_job_file(path).title
    except Exception:
        logger.exception("golden: cannot read job file %s", path)
        return None


async def resolve(
    entries: Iterable[GoldenEntry],
    session: AsyncSession,
    jobs_dir: Path = Path("jobs"),
) -> GoldenSet:
    """Преобразовать filename-entries в id-индексированный GoldenSet."""
    cand_index = await _candidate_basename_index(session)
    job_title_index = await _job_title_index(session)

    out = GoldenSet()
    relevance: dict[int, dict[int, float]] = defaultdict(dict)

    for entry in entries:
        cand_id = cand_index.get(entry.resume_filename)
        if cand_id is None:
            out.missing_resumes.add(entry.resume_filename)
            out.skipped_entries.append(entry)
            continue

        title = _resolve_job_filename_to_title(entry.job_filename, jobs_dir)
        if title is None:
            out.missing_jobs.add(entry.job_filename)
            out.skipped_entries.append(entry)
            continue
        job_id = job_title_index.get(title)
        if job_id is None:
            # Файл существует, но job-row с таким title нет в БД (seeder
            # не отработал или БД пуста). Считаем «job missing».
            out.missing_jobs.add(entry.job_filename)
            out.skipped_entries.append(entry)
            continue

        relevance[job_id][cand_id] = entry.label
        out.job_id_to_filename[job_id] = entry.job_filename
        out.candidate_id_to_filename[cand_id] = entry.resume_filename

    out.relevance = dict(relevance)

    # Догружаем Pydantic Job из БД для каждой job_id, попавшей в labels.
    if out.relevance:
        await _attach_pydantic_jobs(out, session)

    return out


async def _attach_pydantic_jobs(out: GoldenSet, session: AsyncSession) -> None:
    """Поднять Pydantic Job для всех job_id из relevance."""
    from src.schemas import Job as PydanticJob  # local: избежать circular

    rows = (
        (await session.execute(select(JobORM).where(JobORM.id.in_(out.relevance.keys()))))
        .scalars()
        .all()
    )
    for row in rows:
        out.jobs[row.id] = PydanticJob(
            id=row.id,
            title=row.title,
            description=row.description,
            required_skills=list(row.required_skills or []),
            language=row.language,
            embedding_cached=row.embedding_cached,
        )
