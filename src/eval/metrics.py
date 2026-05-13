"""Метрики ранжирования для оценки matching pipeline.

Все функции — чистые: на вход `predicted` (упорядоченный список candidate_id) и
`relevance` (мапа candidate_id → grade ∈ [0..1]). На выход — float в [0..1].
Кандидаты в `predicted`, отсутствующие в `relevance` считаются нерелевантными
(grade=0) — это соответствует open-world labels.

Релевантность бинаризуется для Hit@K / MRR / Recall@K порогом 0.5 — пары с
label=0.5 ("частичный match") идут в положительные. NDCG@K использует
graded relevance напрямую (label-as-gain), чтобы 1.0 кандидаты весили в 2 раза
больше 0.5.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

POSITIVE_THRESHOLD = 0.5


def hit_at_k(predicted: Sequence[int], relevance: Mapping[int, float], k: int) -> float:
    """1.0 если в топ-K есть хотя бы один кандидат с relevance ≥ POSITIVE_THRESHOLD."""
    for cid in predicted[:k]:
        if relevance.get(cid, 0.0) >= POSITIVE_THRESHOLD:
            return 1.0
    return 0.0


def reciprocal_rank(predicted: Sequence[int], relevance: Mapping[int, float]) -> float:
    """1/rank первого релевантного (rank считается с 1). 0.0 если нет."""
    for idx, cid in enumerate(predicted, start=1):
        if relevance.get(cid, 0.0) >= POSITIVE_THRESHOLD:
            return 1.0 / idx
    return 0.0


def recall_at_k(predicted: Sequence[int], relevance: Mapping[int, float], k: int) -> float:
    """|релевантные ∩ топ-K| / |релевантные| (по бинарному порогу)."""
    positives = {cid for cid, rel in relevance.items() if rel >= POSITIVE_THRESHOLD}
    if not positives:
        return 0.0
    found = sum(1 for cid in predicted[:k] if cid in positives)
    return found / len(positives)


def dcg(predicted: Sequence[int], relevance: Mapping[int, float], k: int) -> float:
    """Discounted Cumulative Gain @ K (linear gain, log2 discount)."""
    total = 0.0
    for idx, cid in enumerate(predicted[:k], start=1):
        gain = relevance.get(cid, 0.0)
        if gain == 0.0:
            continue
        total += gain / math.log2(idx + 1)
    return total


def ndcg_at_k(predicted: Sequence[int], relevance: Mapping[int, float], k: int) -> float:
    """NDCG@K с graded relevance.

    Идеальный порядок — все известные положительные grades отсортированы desc.
    Если в `relevance` нет ни одного положительного grade, возвращаем 0.0.
    """
    if not predicted or not relevance:
        return 0.0
    actual = dcg(predicted, relevance, k)
    ideal_grades = sorted(relevance.values(), reverse=True)[:k]
    ideal = sum(g / math.log2(i + 1) for i, g in enumerate(ideal_grades, start=1) if g > 0.0)
    if ideal == 0.0:
        return 0.0
    return actual / ideal


def mean(values: Sequence[float]) -> float:
    """Среднее с проверкой на пустую последовательность — возвращает 0.0."""
    if not values:
        return 0.0
    return sum(values) / len(values)
