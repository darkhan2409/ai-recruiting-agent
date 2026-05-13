"""Извлечение plain-text из PDF / DOCX / TXT.

PDF — `pdfplumber` (под капотом pdfminer.six): берёт текст по X/Y-координатам,
частично сохраняет порядок чтения для multi-column. DOCX — `mammoth.extract_raw_text`:
whole-document conversion в чистый текст без markdown-обёрток. TXT — нативный
`Path.read_text`.

Оба парсера sync; оборачиваем в `asyncio.to_thread` (CLAUDE.md разрешает sync
внутри узла). Layout-aware парсер (LlamaParse / LandingAI ADE) — roadmap для
production, см. запись в `PROGRESS.md` от 2026-05-09.

При `len(text) < 200` для PDF вызывающий код квалифицирует как
`vlm_extract_failed` и отправляет в quarantine — реальный VLM-OCR в roadmap
(БЛОК 10.2). Для txt/docx — `text_too_short`.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import mammoth
import pdfplumber

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS: int = 30_000
MIN_TEXT_CHARS: int = 200

_TXT_EXT = ".txt"
_PDF_EXT = ".pdf"
_DOCX_EXT = ".docx"


def _extract_pdf(path: Path) -> str:
    """Извлечь текст из PDF постранично; пустые страницы → пустые строки."""
    with pdfplumber.open(path) as pdf:
        return "\n\n".join((page.extract_text() or "") for page in pdf.pages)


def _extract_docx(path: Path) -> str:
    """Извлечь raw text из DOCX (без markdown/HTML)."""
    with path.open("rb") as f:
        result = mammoth.extract_raw_text(f)
    return str(result.value)


def extract_docx_from_bytes(data: bytes) -> str:
    """Извлечь raw text из DOCX в памяти (для FastAPI UploadFile).

    Sync; вызывающий код сам обернёт через `asyncio.to_thread`.
    """
    import io

    return str(mammoth.extract_raw_text(io.BytesIO(data)).value)


def _extract_sync(path: Path) -> str | None:
    """Синхронная маршрутизация по расширению + truncate.

    Returns:
        Plain-text (truncated до MAX_TEXT_CHARS) или None при exception.
    """
    ext = path.suffix.lower()
    try:
        if ext == _TXT_EXT:
            text = path.read_text(encoding="utf-8", errors="replace")
        elif ext == _PDF_EXT:
            text = _extract_pdf(path)
        elif ext == _DOCX_EXT:
            text = _extract_docx(path)
        else:
            logger.warning("text_extract: unsupported ext %s for %s", ext, path.name)
            return None
    except Exception:
        logger.exception("text_extract: failed for %s", path.name)
        return None

    return text[:MAX_TEXT_CHARS] if text else ""


async def extract_text(path: Path) -> str | None:
    """Async-обёртка: уводим sync-парсер в worker-thread."""
    return await asyncio.to_thread(_extract_sync, path)
