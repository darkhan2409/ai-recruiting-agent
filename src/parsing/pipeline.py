"""Parsing-оркестратор: file → ParseResult.

Шаги (любая ошибка → ParseFailure с правильным QuarantineReason):
  1. extract_text — pdfplumber / mammoth / TXT → plain-text или None
  2. длина < MIN_TEXT_CHARS → text_too_short (txt/docx) или vlm_extract_failed (pdf-скан)
  3. detect_language → ru/en; при неуверенности → fallback EN (билингвальные/
     короткие тексты не должны блокировать pipeline)
  4. detect_injection → нашли паттерн → prompt_injection_suspected
  5. extractor.extract → Resume или extract_failed (после tenacity-3-ретрая)

Файл при failure НЕ удаляется — он уже сохранён ingestion-ом в `<resumes_dir>`,
рекрутёр имеет его для review. Вызов из `ingestion.pipeline.process_email`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.ingestion.base import QuarantineReason
from src.parsing.extractor import get_default_extractor
from src.parsing.language import detect_language
from src.parsing.sanitize import detect_injection
from src.parsing.text_extract import MIN_TEXT_CHARS, extract_text
from src.schemas import Language, Resume
from src.utils.pii import mask_pii

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParseSuccess:
    """Удачный парс: всё что нужно для записи в `Candidate`."""

    text: str
    language: Language
    resume: Resume


@dataclass(slots=True)
class ParseFailure:
    """Парс провалился: вызывающий код пишет это в `quarantine`."""

    reason: QuarantineReason
    details: dict[str, Any] = field(default_factory=dict)


ParseResult = ParseSuccess | ParseFailure


async def parse_resume(path: Path) -> ParseResult:
    """Полный цикл parsing для одного файла."""
    text = await extract_text(path)
    if text is None:
        return ParseFailure(QuarantineReason.EXTRACT_FAILED, {"path": str(path)})

    if len(text) < MIN_TEXT_CHARS:
        # PDF с image-блоками, но без распознанного текста — кандидат на VLM-OCR
        # (roadmap БЛОК 10.2). Для txt/docx короткий текст = битый файл.
        reason = (
            QuarantineReason.VLM_EXTRACT_FAILED
            if path.suffix.lower() == ".pdf"
            else QuarantineReason.TEXT_TOO_SHORT
        )
        return ParseFailure(reason, {"path": str(path), "length": len(text)})

    language = detect_language(text)
    if language is None:
        # Билингвальные резюме (русский с английской терминологией) и
        # короткие шумные тексты сбивают langdetect. LLM-extract к языку
        # толерантен — пропускаем дальше с language=EN.
        logger.info(
            "language: fallback EN for %s (langdetect uncertain or non-ru/en code)",
            path.name,
        )
        language = Language.EN

    injection = detect_injection(text)
    if injection is not None:
        pattern_name, snippet = injection
        return ParseFailure(
            QuarantineReason.PROMPT_INJECTION_SUSPECTED,
            {"path": str(path), "pattern": pattern_name, "snippet": snippet},
        )

    try:
        resume = await get_default_extractor().extract(text, language)
    except Exception as exc:
        # str(exc) может содержать фрагменты резюме (DATA_POLICY §«Логи»):
        # mask_pii маскирует email/phone до записи в БД и логов.
        logger.exception("parse_resume: extractor failed for %s", path.name)
        return ParseFailure(
            QuarantineReason.EXTRACT_FAILED,
            {"path": str(path), "error": mask_pii(str(exc))[:500]},
        )

    return ParseSuccess(text=text, language=language, resume=resume)
