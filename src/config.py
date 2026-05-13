"""Конфигурация приложения через pydantic-settings.

Поля добавляются по мере появления соответствующих блоков, чтобы не плодить
мёртвые настройки. В БЛОКе 1 — DB / Qdrant / scheduler.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Глобальные настройки приложения.

    Attributes:
        use_mocks: Если True — LLM-вызовы (OpenAI Resume-extraction,
            LLMJudge, JobParser) подменяются регекс-мок реализациями.
            НЕ управляет источником ingestion — см. `ingestion_source`.
        ingestion_source: Какой источник писем использовать.
            `"imap"` — живой IMAPSource (требует IMAP_* credentials).
            `"folder"` — FolderSource из `inbox_dir` (для eval-fixtures
            и live-demo без живого почтового ящика).
            `"auto"` — backwards-compat: folder при `use_mocks=True`,
            иначе imap.
        database_url: DSN PostgreSQL с асинхронным драйвером asyncpg.
        qdrant_url: HTTP URL Qdrant.
        ingestion_interval_seconds: Период срабатывания APScheduler-задачи
            ingestion_tick (БЛОК 2 — забор писем).
        retention_cron_hour: Час срабатывания ночной задачи retention_cleanup
            (БЛОК 10 — удаление просроченных резюме).
        app_version: Версия приложения, отдаётся в /health.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    use_mocks: bool = True
    # Раньше `use_mocks` управлял и LLM-моками, и источником ingestion одной
    # переменной. Это блокировало eval-сценарий «FolderSource fixtures + real
    # LLM extraction». Развели в два независимых флага.
    ingestion_source: Literal["imap", "folder", "auto"] = "auto"

    database_url: str = "postgresql+asyncpg://hcb:hcb@localhost:5432/hcb"
    qdrant_url: str = "http://localhost:6333"

    ingestion_interval_seconds: int = 60
    retention_cron_hour: int = 3

    # IMAP (БЛОК 2). Пустые user/password → FolderSource.
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"

    # Хранилища (БЛОК 2)
    inbox_dir: str = "/app/storage/inbox"
    resumes_dir: str = "/app/storage/resumes"
    max_attachment_mb: int = 10

    # OpenAI / LLMExtractor (БЛОК 3)
    openai_api_key: str = ""
    llm_extract_model: str = "gpt-4o-mini"
    llm_extract_temperature: float = 0.0

    # Embeddings + Vector DB (БЛОК 4).
    # `intfloat/multilingual-e5-large` — лучшее качество для RU/EN из тех,
    # что fastembed поддерживает «из коробки»; модель ~2.24 GB качается
    # LAZY при первом запросе и кэшируется в named volume `models_cache`.
    embedder: Literal["e5", "openai"] = "e5"
    e5_model: str = "intfloat/multilingual-e5-large"
    # fastembed по умолчанию пишет в /tmp/fastembed_cache, который не
    # переживает recreate контейнера. Кладём в HOME — там примонтирован
    # named volume `models_cache` (см. docker-compose.yml).
    fastembed_cache_dir: str = "/home/hcb/.cache/fastembed"
    # Размерность вектора больше не хранится в конфиге: она вычисляется из
    # активного EmbeddingProvider в qdrant_store.init_collection. Это убирает
    # риск рассинхрона при смене E5_MODEL (см. audit P1, 2026-05-10).
    openai_embedding_model: str = "text-embedding-3-large"
    qdrant_collection: str = "resumes"

    # Matching (БЛОК 5).
    # `gpt-4o` — главный rerank, не mini: на коротком контексте резюме vs
    # вакансии разница в качестве объяснений и калибровке score заметна.
    # `fusion_k=60` — стандарт RRF по Cormack et al. 2009.
    # `llm_judge_prompt_version` входит в cache_key — инкремент при правке
    # промпта инвалидирует stale-кэш без миграции БД.
    llm_judge_model: str = "gpt-4o"
    llm_judge_temperature: float = 0.0
    llm_judge_prompt_version: str = "v1"
    match_cache_ttl_hours: int = 24
    # Тюнинг под golden 20 резюме: top-15 — достаточный охват, RRF top-7 —
    # реальный отсев перед LLM-judge. min_score 0.45 (хардкод в endpoint
    # и pipeline) отрезает явно нерелевантных (Java/iOS/1С) от пограничных.
    retriever_top_k_dense: int = 15
    retriever_top_k_tfidf: int = 15
    fusion_k: int = 60
    fusion_top_k: int = 7
    anti_halluc_fuzz_threshold: int = 85

    # API (БЛОК 6).
    # `delete_actor_default` — placeholder до появления auth (БЛОК 10.3).
    delete_actor_default: str = "api:user"
    # Bind-mounted в docker-compose (`./jobs:/app/jobs:ro`); seed JSON-файлы
    # загружаются в `lifespan` через `seed_jobs_from_directory`.
    jobs_dir: str = "/app/jobs"

    app_version: str = "0.1.0"


settings = Settings()
