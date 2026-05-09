"""EmbeddingProvider Protocol + e5 (fastembed) + OpenAI реализации.

`fastembed` — runtime от Qdrant team, ONNX-обёртка над теми же
HuggingFace-моделями (включая `intfloat/multilingual-e5-*`). Это закрывает
букву ТЗ «Sentence-BERT/USE/HuggingFace» (модель та же), но без зависимости
от PyTorch — ~330 MB к image вместо ~2.5 GB.

Префиксы `query: ` / `passage: ` для e5 — обязательны по model card. fastembed
применяет их АВТОМАТИЧЕСКИ через `query_embed()` / `passage_embed()`, нам не
нужно делать это вручную.

Кэшируем только passages (резюме): они стабильны и часто повторяются (один и
тот же файл при ре-обработке). Queries (вакансии) varied — кэш-hit редок.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from functools import lru_cache
from typing import Protocol, cast

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import EmbeddingCache

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    """Контракт провайдера эмбеддингов.

    `model_version` входит в hash кэша — при смене модели старые векторы
    автоматически становятся cache miss.
    """

    model_version: str
    dim: int

    async def embed_passage(self, text: str) -> list[float]: ...
    async def embed_query(self, text: str) -> list[float]: ...


class E5Embedder:
    """fastembed wrapper для intfloat/multilingual-e5-* (ONNX runtime)."""

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=settings.e5_model)
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
        return cast(list[float], resp.data[0].embedding)

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


def text_hash(text: str, model_version: str) -> str:
    """sha256(text|model_version) — model_version в hash инвалидирует кэш при rotate."""
    h = hashlib.sha256()
    h.update(model_version.encode("utf-8"))
    h.update(b"|")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


async def embed_passage_with_cache(
    text: str, embedder: EmbeddingProvider, session: AsyncSession
) -> list[float]:
    """Получить passage-эмбеддинг с кэшем по hash(text, model_version).

    SELECT → hit вернуть; miss → encode + INSERT ON CONFLICT DO NOTHING +
    вернуть. Конкурентные insert-ы безопасны: первый победил, остальные
    просто пропускаются (вектор тот же).
    """
    h = text_hash(text, embedder.model_version)
    cached: list[float] | None = await session.scalar(
        select(EmbeddingCache.vector).where(EmbeddingCache.text_hash == h)
    )
    if cached is not None:
        return cached

    vector = await embedder.embed_passage(text)
    stmt = (
        pg_insert(EmbeddingCache)
        .values(text_hash=h, vector=vector, model_version=embedder.model_version)
        .on_conflict_do_nothing(index_elements=["text_hash"])
    )
    await session.execute(stmt)
    return vector
