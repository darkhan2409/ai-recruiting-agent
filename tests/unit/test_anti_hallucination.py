"""Unit-тесты для verify_matched_skills (OWASP LLM06).

Контракт: skills не вычищаются, но при наличии unverified ставится
confidence=LOW + warning в начале explanation. Прочие поля остаются как есть.
"""

from __future__ import annotations

from src.matching.anti_hallucination import verify_matched_skills
from src.schemas import Confidence, MatchResult, Recommendation


def _make_result(
    matched_skills: list[str],
    explanation: str = "Good fit.",
    confidence: Confidence = Confidence.HIGH,
) -> MatchResult:
    return MatchResult(
        candidate_id=42,
        score=0.85,
        matched_skills=matched_skills,
        gaps=[],
        extras=[],
        confidence=confidence,
        explanation=explanation,
        recommendation=Recommendation.INTERVIEW,
        quotes=[],
    )


def test_all_skills_present_no_change() -> None:
    raw = "Опыт: Python, Django, PostgreSQL. Работал с AWS."
    result = _make_result(["Python", "Django"])
    out = verify_matched_skills(result, raw)
    assert out is result  # тот же объект, без model_copy


def test_unverified_skill_drops_confidence() -> None:
    raw = "Я разрабатывал бэкенд на Python и Django."
    result = _make_result(["Python", "UnobtainiumLang"])
    out = verify_matched_skills(result, raw)
    assert out.confidence == Confidence.LOW
    assert "UnobtainiumLang" in out.explanation
    assert "Anti-hallucination" in out.explanation
    # skill сохранён в matched_skills (не вычистили)
    assert "UnobtainiumLang" in out.matched_skills


def test_fuzzy_match_passes() -> None:
    """`postgres` в matched + `PostgreSQL` в тексте — partial_ratio=100, проходит.

    rapidfuzz partial_ratio устойчив к субстрингам и регистру — substring miss
    (`postgres` ≠ `postgresql`) → fuzzy match → skill подтверждён.
    """
    raw = "Опыт с PostgreSQL и Redis на проекте."
    result = _make_result(["postgres"])
    out = verify_matched_skills(result, raw, fuzz_threshold=85)
    assert out.confidence == Confidence.HIGH  # без изменений


def test_multiple_unverified_listed_in_warning() -> None:
    raw = "Только Python и SQL."
    result = _make_result(["Python", "Rust", "Haskell"])
    out = verify_matched_skills(result, raw)
    assert out.confidence == Confidence.LOW
    assert "Rust" in out.explanation
    assert "Haskell" in out.explanation
    assert "2/3" in out.explanation


def test_empty_matched_skills_noop() -> None:
    raw = "Любой текст резюме"
    result = _make_result([])
    out = verify_matched_skills(result, raw)
    assert out is result


def test_other_fields_preserved() -> None:
    raw = "Только Python."
    result = _make_result(["Python", "Cobol"])
    out = verify_matched_skills(result, raw)
    assert out.candidate_id == 42
    assert out.score == 0.85
    assert out.recommendation == Recommendation.INTERVIEW
    assert out.confidence == Confidence.LOW  # это менялось
    assert "Good fit." in out.explanation  # original sentence сохранена
