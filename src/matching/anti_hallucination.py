"""Anti-hallucination check: matched_skills ⊆ raw_text.

OWASP LLM06 (overreliance): LLM может выдумывать «matched_skills», которых
нет в резюме. Проверяем substring + fuzzy match (rapidfuzz) для устойчивости
к падежам/орфографии (`питон` vs `питоном` vs `python`).

Skills из списка НЕ удаляем — рекрутёр в UI должен видеть, что именно LLM
выдумал. Понижаем `confidence` до `LOW` и прикрепляем предупреждение в
`explanation`.
"""

from __future__ import annotations

import logging

from rapidfuzz import fuzz

from src.schemas import Confidence, MatchResult

logger = logging.getLogger(__name__)


def verify_matched_skills(
    result: MatchResult,
    raw_text: str,
    fuzz_threshold: int = 85,
) -> MatchResult:
    """Проверить, что каждый matched_skill встречается в raw_text.

    Substring (case-insensitive) → подтверждён. Иначе fuzzy `partial_ratio`
    >= threshold → подтверждён (терпит падежи и опечатки). Иначе skill
    помечен как не подтверждённый.

    Args:
        result: Исходный MatchResult от LLM-judge.
        raw_text: Сырой текст резюме (с которым работал extraction).
        fuzz_threshold: 0..100; 85 — компромисс recall/precision (см. plan §11.5.6).

    Returns:
        Тот же MatchResult, если все skills подтверждены. Иначе copy с
        `confidence=LOW` и предупреждением в `explanation`.
    """
    if not result.matched_skills:
        return result

    raw_lower = raw_text.lower()
    unverified: list[str] = []
    for skill in result.matched_skills:
        skill_lower = skill.lower()
        if skill_lower in raw_lower:
            continue
        if fuzz.partial_ratio(skill_lower, raw_lower) >= fuzz_threshold:
            continue
        unverified.append(skill)

    if not unverified:
        return result

    warning = (
        f"⚠ Anti-hallucination: {len(unverified)}/{len(result.matched_skills)} "
        f"skills not found in resume ({', '.join(unverified)})."
    )
    logger.warning(
        "anti-halluc: candidate_id=%s unverified=%s",
        result.candidate_id,
        unverified,
    )
    return result.model_copy(
        update={
            "confidence": Confidence.LOW,
            "explanation": f"{warning} {result.explanation}",
        }
    )
