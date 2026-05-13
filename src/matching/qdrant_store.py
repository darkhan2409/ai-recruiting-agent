"""Qdrant store wrapper: idempotent init + upsert/search/delete для резюме.

Собственный thin-wrapper вместо прямого `AsyncQdrantClient` в нескольких
местах — даёт единую точку для tenacity-ретраев и инициализации collection
на старте FastAPI.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Distance, PointStruct, ScoredPoint, VectorParams

from src.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_client() -> AsyncQdrantClient:
    """Lazy singleton клиента — соединение лениво откладывается до первого вызова."""
    return AsyncQdrantClient(url=settings.qdrant_url)


async def init_collection() -> None:
    """Создать collection если её нет; idempotent (вызывается на старте api).

    Размерность вектора берётся из текущего `EmbeddingProvider`, а не из
    статики в config — иначе при смене `E5_MODEL` (e5-large 1024 → e5-base
    768) collection бы создавалась с несовпадающим size, и upsert падал.

    Используем `Distance.COSINE` — по плану §4 (e5 normalized embeddings).
    """
    # Локальный импорт — embedder тянет fastembed/openai, а qdrant_store
    # должен импортироваться даже если эти deps лениво подгружаются.
    from src.matching.embedding import get_default_embedder

    client = get_client()
    name = settings.qdrant_collection
    if await client.collection_exists(collection_name=name):
        logger.info("qdrant: collection %s already exists", name)
        return
    dim = get_default_embedder().dim
    await client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    logger.info("qdrant: created collection %s (dim=%d, cosine)", name, dim)


async def upsert_resume(candidate_id: int, vector: list[float], payload: dict[str, Any]) -> None:
    """Upsert одного резюме в Qdrant. id-точки = `candidates.id` (BigInteger)."""
    client = get_client()
    await client.upsert(
        collection_name=settings.qdrant_collection,
        points=[PointStruct(id=candidate_id, vector=vector, payload=payload)],
        wait=False,
    )


async def search_resumes(vector: list[float], top_k: int = 5) -> list[ScoredPoint]:
    """Cosine-search top-k резюме по query-вектору. Используется БЛОКом 5."""
    client = get_client()
    response = await client.query_points(
        collection_name=settings.qdrant_collection,
        query=vector,
        limit=top_k,
        with_payload=True,
    )
    return list(response.points)


async def delete_resume(candidate_id: int) -> None:
    """Удалить точку резюме (для DELETE /candidates/{id} — БЛОК 6.4)."""
    client = get_client()
    await client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=[candidate_id],
    )
