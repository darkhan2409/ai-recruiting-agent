"""ResumeExtractor: контракт + две реализации.

`LLMExtractor` — primary. В `USE_MOCKS=True` (default dev) использует regex
поверх извлечённого текста — даёт реалистичный Resume без квот OpenAI.
В `USE_MOCKS=False` — `AsyncOpenAI.beta.chat.completions.parse` со
structured-output (`response_format=Resume`).

`SpacyExtractor` — fallback-stub. Реальная имплементация (spaCy NER + skills
dict + regex) перенесена в roadmap; в MVP с USE_MOCKS=True ветка не
вызывается (LLMExtractor сам обслуживает mock-сценарий).
"""

from __future__ import annotations

import logging
import re
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
from src.parsing.prompts import SYSTEM_PROMPT, build_user_prompt
from src.schemas import Language, Resume

logger = logging.getLogger(__name__)


class ResumeExtractor(Protocol):
    """Контракт извлекателя: текст + язык → структурированный Resume."""

    async def extract(self, text: str, language: Language) -> Resume: ...


# --- Регексы для mock-extraction ---

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Телефон: +? 7-15 цифр с разделителями, минимум 10 цифр всего, окружение —
# не-буква (`(?<!\w)` / `(?!\w)`), чтобы не матчить «2021-2026» внутри слов
# или дат. Поддерживает форматы `+7 707 123 45 67`, `+1 (415) 555-0123`,
# `8 (495) 999-88-77`, `+77071234567`.
_PHONE_RE = re.compile(
    r"(?<!\w)"
    r"(?:\+?\d{1,3}[\s\-]?)?"
    r"\(?\d{3}\)?[\s\-]?"
    r"\d{3}[\s\-]?"
    r"\d{2}[\s\-]?\d{2}"
    r"(?!\w)"
)

_YEARS_RE = re.compile(r"(\d+(?:[\.,]\d+)?)\s*(?:лет|года|год|years?|yrs?)", re.IGNORECASE)

# Мини-словарь — для substring-mock. Лежит в этом же файле, чтобы не плодить
# отдельный data-asset; реальный skills-dict — БЛОК 8 / external (Lightcast).
_SKILLS_DICT: tuple[str, ...] = (
    "python",
    "java",
    "kotlin",
    "javascript",
    "typescript",
    "go",
    "rust",
    "c++",
    "fastapi",
    "django",
    "flask",
    "spring",
    "react",
    "vue",
    "angular",
    "node.js",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "kafka",
    "rabbitmq",
    "docker",
    "kubernetes",
    "aws",
    "gcp",
    "azure",
    "terraform",
    "ansible",
    "git",
    "ci/cd",
    "github actions",
    "jenkins",
    "ml",
    "machine learning",
    "deep learning",
    "pytorch",
    "tensorflow",
    "llm",
    "rag",
    "langchain",
    "openai",
    "transformers",
    "huggingface",
    "sql",
    "etl",
    "airflow",
    "spark",
    "pandas",
    "numpy",
)


def _skill_pattern(skill: str) -> re.Pattern[str]:
    """Скомпилировать regex с границами слова для skill.

    Для skill, заканчивающихся не-словом (`c++`, `node.js`, `ci/cd`) `\\b`
    не работает — используем lookaround `(?<![\\w-])` / `(?![\\w-])` чтобы
    исключить продолжение буквами / дефисами.
    """
    return re.compile(rf"(?<![\w-]){re.escape(skill)}(?![\w-])", re.IGNORECASE)


@lru_cache(maxsize=1)
def _skill_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Кэш скомпилированных паттернов; вычисляется один раз."""
    return tuple((s, _skill_pattern(s)) for s in _SKILLS_DICT)


def _mock_extract(text: str, language: Language) -> Resume:
    """Regex-derived Resume для USE_MOCKS=True ветки."""
    skills = sorted({skill for skill, pat in _skill_patterns() if pat.search(text)})
    email_match = _EMAIL_RE.search(text)
    phone_match = _PHONE_RE.search(text)
    years_match = _YEARS_RE.search(text)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "Unknown")
    summary = " ".join(text.split())[:200] or None
    return Resume(
        full_name=first_line[:200],
        email=email_match.group(0) if email_match else None,
        phone=phone_match.group(0) if phone_match else None,
        skills=skills,
        experience=[],
        education=[],
        total_years=float(years_match.group(1).replace(",", ".")) if years_match else None,
        languages=[language],
        summary=summary,
    )


class LLMExtractor:
    """OpenAI-extractor c USE_MOCKS-веткой и tenacity-retry."""

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        if not settings.use_mocks:
            if not settings.openai_api_key:
                raise RuntimeError("LLMExtractor: USE_MOCKS=False, но OPENAI_API_KEY пуст")
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def extract(self, text: str, language: Language) -> Resume:
        if settings.use_mocks or self._client is None:
            return _mock_extract(text, language)
        return await self._extract_real(text)

    async def _extract_real(self, text: str) -> Resume:
        assert self._client is not None  # noqa: S101 — проверено в __init__
        # Ретраим только сетевые/транзиентные ошибки OpenAI. Refusal,
        # ValidationError и прочие detereministic-failures поднимаем сразу.
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(
                (APIConnectionError, APITimeoutError, RateLimitError, APIError, TimeoutError)
            ),
            reraise=True,
        ):
            with attempt:
                completion = await self._client.beta.chat.completions.parse(
                    model=settings.llm_extract_model,
                    temperature=settings.llm_extract_temperature,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(text)},
                    ],
                    response_format=Resume,
                )
                parsed: Resume | None = completion.choices[0].message.parsed
                if parsed is None:
                    refusal = completion.choices[0].message.refusal or "no parsed output"
                    raise RuntimeError(f"LLM refused / returned no parsed: {refusal}")
                return parsed
        raise RuntimeError("unreachable")  # pragma: no cover


class SpacyExtractor:
    """Fallback-стаб; реальная имплементация — roadmap (БЛОК 8 notebook + 10)."""

    async def extract(self, text: str, language: Language) -> Resume:
        raise RuntimeError(
            "SpacyExtractor: not implemented in MVP; см. roadmap. "
            "В USE_MOCKS=True используется LLMExtractor mock-ветка."
        )


@lru_cache(maxsize=1)
def get_default_extractor() -> ResumeExtractor:
    """Lazy-singleton default-экстрактора.

    Раньше `default_extractor = LLMExtractor()` выполнялся при импорте
    модуля; при `USE_MOCKS=False` и пустом `OPENAI_API_KEY` это убивало
    api на старте. Lazy-init откладывает проверку до первого использования
    (т.е. до первой реальной обработки резюме), что позволяет /health
    работать даже при отсутствующем OpenAI-ключе.
    """
    return LLMExtractor()
