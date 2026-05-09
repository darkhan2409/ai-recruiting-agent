"""Pydantic v2 доменные схемы — контракт между parsing → matching → API → UI.

Эти схемы — единственный источник истины для формы данных в проекте. Любое
изменение здесь — breaking change для downstream-модулей. Все поля снабжены
описаниями для авто-генерации Swagger в БЛОКе 6.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Language(StrEnum):
    """Языки резюме и вакансий. Значения совместимы с langdetect."""

    RU = "ru"
    EN = "en"


class Confidence(StrEnum):
    """Уверенность LLM-judge в результате матчинга.

    `low` присваивается, когда anti-hallucination check ловит выдуманные
    matched_skills — сигнал рекрутёру в UI.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Recommendation(StrEnum):
    """Рекомендация LLM-judge по кандидату.

    Member `PASS` имеет значение `"pass"` — это совпадает с человеческой
    формулировкой и используется при сериализации в API/UI.
    """

    INTERVIEW = "interview"
    CONSIDER = "consider"
    PASS = "pass"


class _StrictModel(BaseModel):
    """Базовый класс с запретом на лишние поля.

    Используем `extra='forbid'` чтобы LLM structured output не протаскивал
    галлюцинированные поля и чтобы любой drift схемы ловился сразу.
    """

    model_config = ConfigDict(extra="forbid")


class ExperienceItem(_StrictModel):
    """Одна запись опыта работы кандидата."""

    company: str = Field(description="Название компании.")
    position: str = Field(description="Должность.")
    start_date: str | None = Field(
        default=None,
        description="Начало периода: ISO-подобная строка ('2021-03', '2021').",
    )
    end_date: str | None = Field(
        default=None,
        description="Конец периода или 'present' для текущего места.",
    )
    description: str | None = Field(
        default=None,
        description="Краткое описание обязанностей и проектов.",
    )
    skills_used: list[str] = Field(
        default_factory=list,
        description="Технологии и навыки, использованные в этой роли.",
    )


class EducationItem(_StrictModel):
    """Одна запись образования кандидата."""

    institution: str = Field(description="Название учебного заведения.")
    degree: str | None = Field(
        default=None,
        description="Степень: бакалавр / магистр / PhD / иное.",
    )
    field: str | None = Field(
        default=None,
        description="Специальность или направление.",
    )
    graduation_year: int | None = Field(
        default=None,
        description="Год выпуска (если указан).",
    )


class Resume(_StrictModel):
    """Структурированный профиль кандидата — выход LLM extraction.

    Контракт для LLMExtractor (БЛОК 3) и SpacyExtractor (fallback).
    `email` намеренно `str`, не `EmailStr`: кандидаты пишут адреса с шумом
    (пробелы, [at]), нормализация — задача парсера, не схемы.
    """

    full_name: str = Field(description="ФИО кандидата.")
    email: str | None = Field(
        default=None,
        description="Контактный email (валидация мягкая, нормализация в парсере).",
    )
    phone: str | None = Field(
        default=None,
        description="Контактный телефон в произвольном формате.",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Технические и доменные навыки кандидата.",
    )
    experience: list[ExperienceItem] = Field(
        default_factory=list,
        description="Опыт работы в обратном хронологическом порядке.",
    )
    education: list[EducationItem] = Field(
        default_factory=list,
        description="Образование кандидата.",
    )
    total_years: float | None = Field(
        default=None,
        ge=0.0,
        description="Суммарный опыт работы в годах (может быть дробным).",
    )
    languages: list[Language] = Field(
        default_factory=list,
        description="Языки, на которых составлено резюме.",
    )
    summary: str | None = Field(
        default=None,
        description="Краткое summary / objective кандидата, если присутствует.",
    )


class Job(_StrictModel):
    """Описание вакансии.

    `id=None` соответствует ad-hoc вызову `POST /recommendations` без
    сохранения в БД. `embedding_cached` — ленивый кэш вектора вакансии,
    заполняется в БЛОКе 4 при первом матчинге.
    """

    id: int | None = Field(default=None, description="ID вакансии в БД (None для ad-hoc).")
    title: str = Field(description="Название вакансии.")
    description: str = Field(description="Полный текст вакансии.")
    required_skills: list[str] = Field(
        default_factory=list,
        description="Извлечённые обязательные навыки.",
    )
    language: Language = Field(description="Язык текста вакансии.")
    embedding_cached: list[float] | None = Field(
        default=None,
        description="Кэш эмбеддинга вакансии (заполняется matching pipeline).",
    )


class MatchResult(_StrictModel):
    """Результат матчинга одного кандидата под одну вакансию — выход LLM-judge.

    Anti-hallucination: `matched_skills` обязаны быть подкреплены `quotes`
    из исходного текста резюме (см. БЛОК 5). Если выдуманы — `confidence=LOW`.
    """

    candidate_id: int = Field(description="ID кандидата в БД.")
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Финальный score [0..1] после rerank LLM-judge.",
    )
    matched_skills: list[str] = Field(
        default_factory=list,
        description="Навыки кандидата, релевантные вакансии (с цитатами в quotes).",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Требования вакансии, не подтверждённые резюме.",
    )
    extras: list[str] = Field(
        default_factory=list,
        description="Дополнительные навыки кандидата сверх требований.",
    )
    confidence: Confidence = Field(description="Уверенность LLM-judge в оценке.")
    explanation: str = Field(description="Короткое NL-объяснение оценки.")
    recommendation: Recommendation = Field(description="Действие: интервью / рассмотреть / отказ.")
    quotes: list[str] = Field(
        default_factory=list,
        description="Цитаты из резюме, подтверждающие matched_skills (anti-hallucination).",
    )
