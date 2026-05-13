"""Unit-тесты для text_extract (PDF / DOCX / TXT + edge cases).

Не дублируем fixtures: читаем из `tests/fixtures/golden/resumes/` (см.
`tests/conftest.py:golden_resumes_dir`). Edge-cases — через tmp_path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.parsing.text_extract import (
    MAX_TEXT_CHARS,
    extract_docx_from_bytes,
    extract_text,
)


def test_extract_docx_returns_text(golden_resumes_dir: Path) -> None:
    candidates = list(golden_resumes_dir.glob("*.docx"))
    assert candidates, "ожидалось ≥1 docx fixture"
    text = asyncio.run(extract_text(candidates[0]))
    assert text is not None
    assert len(text) > 100


def test_extract_pdf_returns_text(golden_resumes_dir: Path) -> None:
    candidates = list(golden_resumes_dir.glob("*.pdf"))
    assert candidates, "ожидалось ≥1 pdf fixture"
    text = asyncio.run(extract_text(candidates[0]))
    assert text is not None
    assert len(text) > 100


def test_extract_txt_roundtrip(golden_resumes_dir: Path) -> None:
    """TXT: содержимое после extract совпадает с исходным (truncated до MAX)."""
    candidates = list(golden_resumes_dir.glob("*.txt"))
    assert candidates, "ожидалось ≥1 txt fixture"
    raw = candidates[0].read_text(encoding="utf-8", errors="replace")
    text = asyncio.run(extract_text(candidates[0]))
    assert text is not None
    assert text == raw[:MAX_TEXT_CHARS]


def test_unsupported_extension_returns_none(tmp_path: Path) -> None:
    fake = tmp_path / "archive.zip"
    fake.write_bytes(b"PK\x03\x04 not a real zip")
    text = asyncio.run(extract_text(fake))
    assert text is None


def test_corrupted_pdf_returns_none(tmp_path: Path) -> None:
    """Битый PDF (фейковые magic bytes) — exception ловится, возвращается None."""
    fake = tmp_path / "broken.pdf"
    fake.write_bytes(b"%PDF-1.4\nnot a real pdf body")
    text = asyncio.run(extract_text(fake))
    assert text is None


def test_truncate_max_chars(tmp_path: Path) -> None:
    """TXT длиннее MAX_TEXT_CHARS обрезается, не падает."""
    big = tmp_path / "huge.txt"
    big.write_text("A" * (MAX_TEXT_CHARS + 5_000), encoding="utf-8")
    text = asyncio.run(extract_text(big))
    assert text is not None
    assert len(text) == MAX_TEXT_CHARS


def test_extract_docx_from_bytes_roundtrip(golden_resumes_dir: Path) -> None:
    """extract_docx_from_bytes — sync helper для UploadFile path."""
    candidates = list(golden_resumes_dir.glob("*.docx"))
    assert candidates
    data = candidates[0].read_bytes()
    text = extract_docx_from_bytes(data)
    assert len(text) > 100
