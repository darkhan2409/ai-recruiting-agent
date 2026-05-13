"""Общие pytest-фикстуры.

`golden_resumes_dir` указывает на `tests/fixtures/golden/resumes/` — 20
синтетических резюме, которые eval-runner матчит против labels.json и
которые `scripts/demo.py` копирует в `storage/inbox/` для ingestion.
Если каталог отсутствует (clean machine без fixtures) — тесты skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GOLDEN_RESUMES = _REPO_ROOT / "tests" / "fixtures" / "golden" / "resumes"


@pytest.fixture
def golden_resumes_dir() -> Path:
    """Каталог с 20 синтетическими резюме; skip если отсутствует."""
    if not _GOLDEN_RESUMES.exists():
        pytest.skip(f"golden resumes missing: {_GOLDEN_RESUMES}")
    return _GOLDEN_RESUMES
