"""TfidfRetriever: classical TF-IDF + cosine similarity baseline.

Подход 2 ТЗ («TF-IDF + ML классификатор как baseline»). Использован
unsupervised cosine, не supervised classifier — на 30-50 golden парах
supervised обучение даёт data leak (см. plan §11.5.1, Q&A 3).
ML-компонент в системе представлен LLM-judge как Learning-to-Rank без
потребности в размеченных данных.

Re-fit `TfidfVectorizer` на корпусе `candidates.raw_text` при каждом
запросе. Для MVP <500 кандидатов re-fit < 50ms — простота важнее
оптимизации. Roadmap: персистентный vectorizer + incremental update.
"""

from __future__ import annotations

import asyncio
import logging

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import Candidate
from src.matching.dense import RetrievalHit
from src.schemas import Job

logger = logging.getLogger(__name__)


class TfidfRetriever:
    """Stateless TF-IDF retriever; vectorizer пере-fit на каждый запрос."""

    async def retrieve(
        self,
        job: Job,
        session: AsyncSession,
        top_k: int = 50,
    ) -> list[RetrievalHit]:
        """Top_k кандидатов по cosine similarity TF-IDF (job vs candidate.raw_text).

        Args:
            job: Вакансия. Используем `description` как query.
            session: Async-сессия для SELECT corpus.
            top_k: Сколько кандидатов вернуть.

        Returns:
            Список `RetrievalHit` сортированный по cosine desc. Пустой
            список — если в БД нет кандидатов с `raw_text IS NOT NULL`.
        """
        rows = (
            await session.execute(
                select(Candidate.id, Candidate.raw_text).where(Candidate.raw_text.is_not(None))
            )
        ).all()
        if not rows:
            logger.debug("TfidfRetriever: empty corpus")
            return []

        ids: list[int] = [row[0] for row in rows]
        corpus: list[str] = [row[1] for row in rows]

        hits = await asyncio.to_thread(self._rank_sync, corpus, ids, job.description, top_k)
        logger.debug(
            "TfidfRetriever: job_id=%s corpus=%d top_k=%d → %d hits",
            job.id,
            len(corpus),
            top_k,
            len(hits),
        )
        return hits

    @staticmethod
    def _rank_sync(
        corpus: list[str],
        ids: list[int],
        query: str,
        top_k: int,
    ) -> list[RetrievalHit]:
        """Синхронный fit + transform + cosine; вызывается через `to_thread`.

        Bigrams (`ngram_range=(1, 2)`) ловят составные навыки —
        «machine learning», «node.js». `strip_accents="unicode"` нормализует
        латиницу с диакритикой (немецкие/французские имена).
        """
        vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=50_000,
            lowercase=True,
            strip_accents="unicode",
        )
        corpus_matrix = vectorizer.fit_transform(corpus)
        query_matrix = vectorizer.transform([query])
        sims = cosine_similarity(query_matrix, corpus_matrix)[0]

        # argsort по убыванию; берём top_k индексов
        ranked = sorted(range(len(sims)), key=lambda i: float(sims[i]), reverse=True)[:top_k]
        return [RetrievalHit(candidate_id=ids[i], score=float(sims[i])) for i in ranked]
