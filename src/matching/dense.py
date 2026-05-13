"""DenseRetriever: dense (e5/openai) retrieval через Qdrant cosine search.

Подход 1 ТЗ («Sentence-BERT/HF»). На вход — `Job`, на выход — top_k
кандидатов с cosine score. Используется в hybrid pipeline (БЛОК 5.7) и как
самостоятельный method="dense" для live-demo сравнения.

Score кандидата — raw cosine из Qdrant. Для нормализованных e5-эмбеддингов
он лежит в [-1..1], но на realistic passages типично в [0..1]; не
нормализуем — пусть LLM-judge принимает решение поверх абсолютных значений.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.matching import qdrant_store
from src.matching.embedding import EmbeddingProvider, embed_query_for_job
from src.schemas import Job

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    """Один результат retriever-а: candidate_id + score [0..1]."""

    candidate_id: int
    score: float


class DenseRetriever:
    """Dense retriever поверх Qdrant cosine search.

    Stateless: hot-path — `embed_query → qdrant.search`. Embedder и Qdrant
    клиент кэшируются на уровне модулей (`get_default_embedder`,
    `qdrant_store.get_client`).
    """

    def __init__(self, embedder: EmbeddingProvider) -> None:
        self._embedder = embedder

    async def retrieve(
        self,
        job: Job,
        session: AsyncSession,
        top_k: int = 50,
    ) -> list[RetrievalHit]:
        """Найти top_k кандидатов через dense search.

        Args:
            job: Вакансия.
            session: Async-сессия (для job embedding cache UPDATE).
            top_k: Сколько кандидатов вернуть из Qdrant.

        Returns:
            Список `RetrievalHit` отсортированный по убыванию score (Qdrant
            сам сортирует). Пустой список — если Qdrant пуст.
        """
        vector = await embed_query_for_job(job, self._embedder, session)
        points = await qdrant_store.search_resumes(vector, top_k=top_k)
        hits = [RetrievalHit(candidate_id=int(p.id), score=float(p.score)) for p in points]
        logger.debug("DenseRetriever: job_id=%s top_k=%d → %d hits", job.id, top_k, len(hits))
        return hits
