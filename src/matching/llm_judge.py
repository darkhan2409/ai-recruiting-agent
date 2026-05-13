"""LLMJudge: gpt-4o + structured output → MatchResult; кэш через MatchCache.

Зеркалит pattern `LLMExtractor` (`src/parsing/extractor.py:159`) — без shared
base, потому что Protocols/ABC ради двух impl запрещены CLAUDE.md «Не делать
без реальной нужды». Если появится третий LLM-клиент (например, локальная
Llama) — рефакторим в общий BaseAsyncLLMClient.

Кэш-key: `sha256(f"{candidate_id}|{job_text_hash}|{model_version}|{prompt_version}")`.
Версионирование инвалидирует кэш при ротации модели или правке промпта без
ручной чистки. TTL по умолчанию 24h (`settings.match_cache_ttl_hours`).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings
from src.db import Candidate, MatchCache
from src.matching.prompts import JUDGE_SYSTEM_PROMPT, build_judge_user_prompt
from src.schemas import Confidence, Job, MatchResult, Recommendation, Resume

logger = logging.getLogger(__name__)


# --- Mock judge (USE_MOCKS=True) ---


def _mock_judge(job: Job, candidate_id: int, resume: Resume) -> MatchResult:
    """Детерминированный mock на основе пересечения skills.

    Score = |required ∩ resume| / |required|. Recommendation производный
    от score. Без LLM-вызовов — экономит квоты в dev и даёт стабильные
    результаты для smoke-тестов.
    """
    job_skills = {s.lower() for s in job.required_skills}
    cand_skills = {s.lower() for s in resume.skills}

    matched = sorted(job_skills & cand_skills)
    gaps = sorted(job_skills - cand_skills)
    extras = sorted(cand_skills - job_skills)

    score = len(matched) / len(job_skills) if job_skills else 0.5
    if score >= 0.6:
        recommendation = Recommendation.INTERVIEW
    elif score >= 0.3:
        recommendation = Recommendation.CONSIDER
    else:
        recommendation = Recommendation.PASS

    return MatchResult(
        candidate_id=candidate_id,
        score=score,
        matched_skills=matched,
        gaps=gaps,
        extras=extras,
        confidence=Confidence.MEDIUM,
        explanation=f"[mock] Skill overlap: {len(matched)}/{len(job_skills)} required.",
        recommendation=recommendation,
        quotes=[],
    )


# --- Real judge ---


class LLMJudge:
    """OpenAI judge с USE_MOCKS-веткой и tenacity-retry; mirror LLMExtractor."""

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        if not settings.use_mocks:
            if not settings.openai_api_key:
                raise RuntimeError("LLMJudge: USE_MOCKS=False, но OPENAI_API_KEY пуст")
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    @property
    def model_version(self) -> str:
        """Стабильная строка-идентификатор модели (входит в cache_key)."""
        if settings.use_mocks or self._client is None:
            return "mock:llm_judge"
        return f"openai:{settings.llm_judge_model}"

    async def judge_one(
        self,
        job: Job,
        candidate_id: int,
        resume: Resume,
        raw_text: str,
    ) -> MatchResult:
        """Оценить одного кандидата под одну вакансию.

        В USE_MOCKS=True ветка возвращает детерминированный mock без OpenAI.
        В реальной ветке — `beta.chat.completions.parse` со structured output;
        retry только на сетевых/RateLimit ошибках.
        """
        if settings.use_mocks or self._client is None:
            return _mock_judge(job, candidate_id, resume)
        return await self._judge_real(job, candidate_id, resume, raw_text)

    async def _judge_real(
        self,
        job: Job,
        candidate_id: int,
        resume: Resume,
        raw_text: str,
    ) -> MatchResult:
        assert self._client is not None  # noqa: S101 — проверено в __init__
        user_msg = build_judge_user_prompt(job, resume, raw_text, candidate_id)
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(
                (APIConnectionError, APITimeoutError, RateLimitError, APIError, TimeoutError)
            ),
            reraise=True,
        ):
            with attempt:
                completion = await self._client.beta.chat.completions.parse(
                    model=settings.llm_judge_model,
                    temperature=settings.llm_judge_temperature,
                    messages=[
                        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    response_format=MatchResult,
                )
                parsed: MatchResult | None = completion.choices[0].message.parsed
                if parsed is None:
                    refusal = completion.choices[0].message.refusal or "no parsed output"
                    raise RuntimeError(f"LLM-judge refused / no parsed: {refusal}")
                # Принудительно перезаписываем — модель может ошибиться с id.
                return parsed.model_copy(update={"candidate_id": candidate_id})
        raise RuntimeError("unreachable")  # pragma: no cover


@lru_cache(maxsize=1)
def get_default_judge() -> LLMJudge:
    """Lazy-singleton; создание клиента OpenAI откладывается до первого вызова."""
    return LLMJudge()


# --- Cache helpers ---


def _cache_key(
    candidate_id: int,
    job: Job,
    model_version: str,
    prompt_version: str,
) -> str:
    """sha256 over `candidate_id|job_text_hash|model_version|prompt_version`."""
    job_text_hash = hashlib.sha256(job.description.encode("utf-8")).hexdigest()
    payload = f"{candidate_id}|{job_text_hash}|{model_version}|{prompt_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def judge_with_cache(
    judge: LLMJudge,
    job: Job,
    candidate: Candidate,
    session: AsyncSession,
) -> MatchResult:
    """Получить MatchResult с кэшем; cache hit при `expires_at > now()`.

    На miss/expired — judge.judge_one + INSERT ON CONFLICT DO UPDATE
    обновляет старую запись (TTL переустанавливается).

    Args:
        judge: LLMJudge инстанс.
        job: Вакансия (id допустим None для ad-hoc).
        candidate: ORM-кандидат с заполненными `parsed_data` и `raw_text`.
        session: Async session (вызывающий коммитит).

    Raises:
        ValueError: Если у кандидата нет parsed_data или raw_text — он не
            должен попадать в matching pipeline.
    """
    if candidate.parsed_data is None or candidate.raw_text is None:
        raise ValueError(f"Candidate {candidate.id} not parsed (parsed_data/raw_text is None)")
    resume = Resume.model_validate(candidate.parsed_data)
    raw_text = candidate.raw_text

    prompt_version = settings.llm_judge_prompt_version
    key = _cache_key(candidate.id, job, judge.model_version, prompt_version)

    row = (
        await session.execute(
            select(MatchCache.result, MatchCache.expires_at).where(MatchCache.cache_key == key)
        )
    ).first()
    if row is not None:
        result_dict, expires_at = row
        if expires_at is None or expires_at > datetime.now(UTC):
            logger.info("LLM-judge cache HIT candidate_id=%s job_id=%s", candidate.id, job.id)
            return MatchResult.model_validate(result_dict)
        logger.debug("LLM-judge cache EXPIRED candidate_id=%s — re-judging", candidate.id)

    result = await judge.judge_one(job, candidate.id, resume, raw_text)

    expires = datetime.now(UTC) + timedelta(hours=settings.match_cache_ttl_hours)
    insert_stmt = pg_insert(MatchCache).values(
        cache_key=key,
        result=result.model_dump(mode="json"),
        model_version=judge.model_version,
        prompt_version=prompt_version,
        expires_at=expires,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["cache_key"],
        set_={
            "result": insert_stmt.excluded.result,
            "expires_at": insert_stmt.excluded.expires_at,
            "model_version": insert_stmt.excluded.model_version,
            "prompt_version": insert_stmt.excluded.prompt_version,
        },
    )
    await session.execute(upsert_stmt)
    logger.info("LLM-judge cache MISS candidate_id=%s job_id=%s — wrote", candidate.id, job.id)
    return result
