"""find_candidates: единая точка входа matching pipeline.

Branching по `method`:
- `dense` — Qdrant cosine, без LLM. «Syntetic» MatchResult с derived
  recommendation. Для live-demo сравнения подходов.
- `tfidf` — sklearn TF-IDF cosine, без LLM. Закрывает букву ТЗ.
- `llm` — Dense top-20 → LLM-judge на всём пуле → top_k.
- `hybrid` (default, production) — Dense + TF-IDF top-50 параллельно → RRF
  fusion top-20 → LLM-judge → anti-hallucination → top_k.

После всех веток — фильтр по `min_score` и финальная сортировка.
Persist Match rows в БЛОКе 6 endpoint, не здесь.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import Candidate, session_factory
from src.matching.anti_hallucination import verify_matched_skills
from src.matching.dense import DenseRetriever, RetrievalHit
from src.matching.embedding import get_default_embedder
from src.matching.llm_judge import LLMJudge, get_default_judge, judge_with_cache
from src.matching.rrf import rrf_merge
from src.matching.tfidf_retriever import TfidfRetriever
from src.schemas import Confidence, Job, MatchResult, Recommendation

logger = logging.getLogger(__name__)

Method = Literal["dense", "tfidf", "llm", "hybrid"]


def _clip_score(score: float) -> float:
    """MatchResult.score должен быть в [0..1]. Cosine может дать чуть выше из-за float — clamp."""
    return max(0.0, min(1.0, score))


def _derived_recommendation(score: float) -> Recommendation:
    if score >= 0.7:
        return Recommendation.INTERVIEW
    if score >= 0.4:
        return Recommendation.CONSIDER
    return Recommendation.PASS


def _synthetic_match_result(hit: RetrievalHit, explanation: str) -> MatchResult:
    """MatchResult из retriever-hit без LLM (для dense/tfidf one-shot путей)."""
    score = _clip_score(hit.score)
    return MatchResult(
        candidate_id=hit.candidate_id,
        score=score,
        matched_skills=[],
        gaps=[],
        extras=[],
        confidence=Confidence.MEDIUM,
        explanation=explanation,
        recommendation=_derived_recommendation(score),
        quotes=[],
    )


async def _judge_candidates(
    candidate_ids: list[int],
    job: Job,
    judge: LLMJudge,
    session: AsyncSession,
) -> list[MatchResult]:
    """Загрузить кандидатов по ids, пройти LLM-judge параллельно, anti-halluc."""
    if not candidate_ids:
        return []

    rows = (
        (await session.execute(select(Candidate).where(Candidate.id.in_(candidate_ids))))
        .scalars()
        .all()
    )
    valid = [c for c in rows if c.parsed_data is not None and c.raw_text is not None]
    if len(valid) < len(rows):
        logger.warning(
            "judge: skipped %d candidates without parsed_data/raw_text",
            len(rows) - len(valid),
        )
    if not valid:
        return []

    # OpenAI Tier 1 ограничение 30k TPM на gpt-4o. Один judge-call ~5-7k
    # токенов prompt + 500 output; даже concurrency=2 даёт пиковые spikes
    # 50-80k TPM с retry-storm. Sequential (Semaphore(1)) — ~4 calls/min,
    # ~35-40 мин на 140 calls для eval-режима. При Tier 2+ (200k TPM)
    # можно вернуть Semaphore(3-5).
    sem = asyncio.Semaphore(1)

    # Per-task session_factory вместо shared outer session: при 429-retry-storm
    # tenacity долго ждёт между попытками, asyncio свопит между корутинами в
    # gather — даже под Semaphore(1) shared AsyncSession ловит
    # `asyncpg: another operation is in progress` из-за preempted connection
    # state. Своя session на task → connection из pool возвращается чисто после
    # каждого commit, не делится между корутинами.
    async def _judge_throttled(cand: Candidate) -> MatchResult:
        async with sem, session_factory() as task_session:
            result = await judge_with_cache(judge, job, cand, task_session)
            await task_session.commit()
            return result

    judge_results = await asyncio.gather(*(_judge_throttled(cand) for cand in valid))
    return [
        verify_matched_skills(
            result, cand.raw_text or "", fuzz_threshold=settings.anti_halluc_fuzz_threshold
        )
        for result, cand in zip(judge_results, valid, strict=True)
    ]


async def find_candidates(
    job: Job,
    session: AsyncSession,
    top_k: int = 5,
    method: Method = "hybrid",
    min_score: float = 0.45,
) -> list[MatchResult]:
    """Главный сервис матчинга. БЛОК 6 (API) вызывает отсюда.

    Args:
        job: Вакансия (id может быть None для ad-hoc).
        session: Async session — pipeline сам не коммитит.
        top_k: Финальное число кандидатов (после фильтра min_score).
        method: dense | tfidf | llm | hybrid (default).
        min_score: Кандидаты со score < min_score отбрасываются — см.
            plan §11.5.3 (блокировка показа топ-5 случайных).

    Returns:
        Список MatchResult, отсортированный по score desc, длиной ≤ top_k.
        Может быть короче top_k или пустым — это корректное поведение.
    """
    embedder = get_default_embedder()
    dense_retriever = DenseRetriever(embedder)
    tfidf_retriever = TfidfRetriever()
    judge = get_default_judge()

    results: list[MatchResult]
    match method:
        case "dense":
            hits = await dense_retriever.retrieve(job, session, top_k=top_k * 2)
            results = [_synthetic_match_result(hit, "Dense (e5) cosine retrieval") for hit in hits]
        case "tfidf":
            hits = await tfidf_retriever.retrieve(job, session, top_k=top_k * 2)
            results = [_synthetic_match_result(hit, "TF-IDF cosine retrieval") for hit in hits]
        case "llm":
            hits = await dense_retriever.retrieve(job, session, top_k=settings.fusion_top_k)
            results = await _judge_candidates([h.candidate_id for h in hits], job, judge, session)
        case "hybrid":
            logger.info("hybrid: parallel dense+tfidf retrieval start (job_id=%s)", job.id)
            dense_hits, tfidf_hits = await asyncio.gather(
                dense_retriever.retrieve(job, session, top_k=settings.retriever_top_k_dense),
                tfidf_retriever.retrieve(job, session, top_k=settings.retriever_top_k_tfidf),
            )
            fused = rrf_merge(
                [[h.candidate_id for h in dense_hits], [h.candidate_id for h in tfidf_hits]],
                k=settings.fusion_k,
                top_n=settings.fusion_top_k,
            )
            logger.info(
                "hybrid: dense=%d tfidf=%d → fused=%d → judge",
                len(dense_hits),
                len(tfidf_hits),
                len(fused),
            )
            results = await _judge_candidates([cid for cid, _ in fused], job, judge, session)

    filtered = [r for r in results if r.score >= min_score]
    filtered.sort(key=lambda r: r.score, reverse=True)
    return filtered[:top_k]
