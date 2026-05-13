"""EmbeddingProvider Protocol + e5 (fastembed) + OpenAI реализации.

`fastembed` — runtime от Qdrant team, ONNX-обёртка над теми же
HuggingFace-моделями (включая `intfloat/multilingual-e5-*`). Это закрывает
букву ТЗ «Sentence-BERT/USE/HuggingFace» (модель та же), но без зависимости
от PyTorch — ~330 MB к image вместо ~2.5 GB.

Префиксы `query: ` / `passage: ` для e5 — обязательны по model card. fastembed
применяет их АВТОМАТИЧЕСКИ через `query_embed()` / `passage_embed()`, нам не
нужно делать это вручную.

Вакансии получают lazy-кэш в колонке `jobs.embedding_cached` (см.
`embed_query_for_job`). Passage-кэш для резюме был выпилен — encode идёт
напрямую через `embedder.embed_passage()`.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Protocol, cast

from openai import AsyncOpenAI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import Job as JobORM
from src.schemas import Job

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    """Контракт провайдера эмбеддингов.

    `model_version` — строковая метка для логов и кэша вакансий; на смену
    модели старые `jobs.embedding_cached` инвалидируются вручную (через
    миграцию или re-create job).
    """

    model_version: str
    dim: int

    async def embed_passage(self, text: str) -> list[float]: ...
    async def embed_query(self, text: str) -> list[float]: ...


class E5Embedder:
    """fastembed wrapper для intfloat/multilingual-e5-* (ONNX runtime)."""

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        # cache_dir=settings.fastembed_cache_dir — без этого fastembed качает
        # модель в /tmp, который теряется при recreate контейнера.
        self._model = TextEmbedding(
            model_name=settings.e5_model,
            cache_dir=settings.fastembed_cache_dir,
        )
        self.model_version = f"e5:{settings.e5_model}"
        self.dim = TextEmbedding.get_embedding_size(settings.e5_model)

    def _passage_sync(self, text: str) -> list[float]:
        # passage_embed() автоматически добавляет префикс "passage: "
        vec = next(iter(self._model.passage_embed([text])))
        return cast(list[float], vec.tolist())

    def _query_sync(self, text: str) -> list[float]:
        vec = next(iter(self._model.query_embed([text])))
        return cast(list[float], vec.tolist())

    async def embed_passage(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._passage_sync, text)

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._query_sync, text)


class OpenAIEmbedder:
    """OpenAI text-embedding-3-* через AsyncOpenAI.

    У OpenAI prefix не используется — `embed_passage` и `embed_query`
    возвращают одинаковый результат для одинакового текста.
    """

    _DIMS: dict[str, int] = {
        "text-embedding-3-large": 3072,
        "text-embedding-3-small": 1536,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OpenAIEmbedder: OPENAI_API_KEY пуст")
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model_version = f"openai:{settings.openai_embedding_model}"
        self.dim = self._DIMS.get(settings.openai_embedding_model, 1536)

    async def _embed(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(
            model=settings.openai_embedding_model, input=text
        )
        return list(resp.data[0].embedding)

    async def embed_passage(self, text: str) -> list[float]:
        return await self._embed(text)

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(text)


@lru_cache(maxsize=1)
def get_default_embedder() -> EmbeddingProvider:
    """Lazy singleton провайдера, выбираемого по `settings.embedder`."""
    if settings.embedder == "openai":
        return OpenAIEmbedder()
    return E5Embedder()


async def embed_query_for_job(
    job: Job,
    embedder: EmbeddingProvider,
    session: AsyncSession,
) -> list[float]:
    """Query-эмбеддинг для job.description с lazy-кэшем в `jobs.embedding_cached`.

    Семантика хранения — один-к-одному с job row: при пересчёте текста
    вакансии создаётся новая Job (новый id). Ad-hoc вакансия (`job.id is
    None`) кэш не получает.

    Args:
        job: Pydantic Job с заполненным `description` (id опционален).
        embedder: Провайдер эмбеддингов.
        session: Async SQLAlchemy сессия (вызывающий сам коммитит).

    Returns:
        Query-вектор размерности `embedder.dim`.
    """
    if job.embedding_cached is not None:
        logger.debug("embed_query_for_job: cache hit (in-memory) for job_id=%s", job.id)
        return job.embedding_cached

    vector = await embedder.embed_query(job.description)

    if job.id is not None:
        await session.execute(
            update(JobORM).where(JobORM.id == job.id).values(embedding_cached=vector)
        )
        # Обновляем in-memory модель — повторный матчинг той же job в той
        # же сессии не пойдёт в БД повторно.
        job.embedding_cached = vector
        logger.info("embed_query_for_job: cached vector for job_id=%s", job.id)

    return vector
