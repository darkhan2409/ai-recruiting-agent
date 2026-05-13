"""LLM-парсер документа вакансии в структурированные поля.

Симметричен `LLMExtractor` из `extractor.py` (для резюме): gpt-4o-mini +
structured output через `beta.chat.completions.parse(response_format=JobParsed)`.
USE_MOCKS=True → mock-результат (title из первой непустой строки текста,
description из всего текста, остальное null) — рекрутёр всё равно проверит
форму перед сохранением.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Protocol

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings
from src.parsing.language import detect_language
from src.parsing.prompts import JOB_PARSE_SYSTEM_PROMPT, build_job_parse_user_prompt
from src.schemas import JobParsed, Language

logger = logging.getLogger(__name__)


class JobDocumentParser(Protocol):
    """Контракт парсера документа вакансии."""

    async def parse(self, text: str) -> JobParsed: ...


def _mock_parse(text: str) -> JobParsed:
    """Mock-результат: title из первой непустой строки, description = весь текст."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = lines[0][:200] if lines else "Без названия"
    lang = detect_language(text) or Language.EN
    return JobParsed(
        title=title,
        description=text[:5000],
        department=None,
        experience_years=None,
        work_format=None,
        salary_range=None,
        responsibilities=None,
        conditions=None,
        required_skills=[],
        language=lang.value,
    )


class LLMJobParser:
    """gpt-4o-mini + structured output для парсинга вакансий.

    Lazy инициализация клиента: при USE_MOCKS=False, но пустом OPENAI_API_KEY,
    `__init__` падает с RuntimeError — но `get_default_job_parser()` через
    `lru_cache` откладывает создание до первого реального запроса.
    """

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        if not settings.use_mocks:
            if not settings.openai_api_key:
                raise RuntimeError("LLMJobParser: USE_MOCKS=False, но OPENAI_API_KEY пуст")
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def parse(self, text: str) -> JobParsed:
        if settings.use_mocks or self._client is None:
            return _mock_parse(text)

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(
                (
                    APIConnectionError,
                    APITimeoutError,
                    RateLimitError,
                    APIError,
                    TimeoutError,
                )
            ),
            reraise=True,
        ):
            with attempt:
                completion = await self._client.beta.chat.completions.parse(
                    model=settings.llm_extract_model,
                    temperature=settings.llm_extract_temperature,
                    messages=[
                        {"role": "system", "content": JOB_PARSE_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": build_job_parse_user_prompt(text),
                        },
                    ],
                    response_format=JobParsed,
                )
                parsed = completion.choices[0].message.parsed
                if parsed is None:
                    raise RuntimeError("LLMJobParser: empty parsed result")
                return parsed
        raise RuntimeError("LLMJobParser: exhausted retries")


@lru_cache(maxsize=1)
def get_default_job_parser() -> JobDocumentParser:
    """Singleton — инициализация откладывается до первого вызова."""
    return LLMJobParser()
