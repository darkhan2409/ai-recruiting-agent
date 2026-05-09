"""Конфигурация приложения через pydantic-settings.

Поля добавляются по мере появления соответствующих блоков, чтобы не плодить
мёртвые настройки. В БЛОКе 1 — DB / Qdrant / scheduler.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Глобальные настройки приложения.

    Attributes:
        use_mocks: Если True — внешние API (OpenAI, IMAP) подменяются
            реалистичными mock-реализациями. Default по CLAUDE.md.
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
    embedding_dim: int = 1024
    openai_embedding_model: str = "text-embedding-3-large"
    qdrant_collection: str = "resumes"

    app_version: str = "0.1.0"


settings = Settings()
