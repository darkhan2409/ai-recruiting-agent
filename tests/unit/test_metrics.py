"""Unit-тесты для метрик ранжирования.

Покрываем edge cases и численные значения по reference-примерам:
NDCG из Wikipedia / Cormack et al. 2009. Без pytest fixtures — простые
assert-функции, запускаются обычным `python -m pytest tests/unit/`.
"""

from __future__ import annotations

import math

from src.eval.metrics import (
    dcg,
    hit_at_k,
    mean,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_hit_at_k_basic() -> None:
    rel = {1: 1.0, 2: 0.0, 3: 0.5}
    assert hit_at_k([2, 3, 1], rel, k=1) == 0.0  # top-1 = 2 (rel=0)
    assert hit_at_k([2, 3, 1], rel, k=2) == 1.0  # 3 в top-2
    assert hit_at_k([2, 1, 3], rel, k=5) == 1.0
    assert hit_at_k([], rel, k=5) == 0.0


def test_hit_at_k_threshold() -> None:
    """label=0.5 — положительный (порог >=0.5)."""
    rel = {1: 0.5, 2: 0.4}
    assert hit_at_k([2, 1], rel, k=2) == 1.0
    assert hit_at_k([2], rel, k=1) == 0.0


def test_reciprocal_rank() -> None:
    rel = {1: 1.0, 5: 0.5}
    assert reciprocal_rank([2, 3, 1, 4, 5], rel) == 1 / 3
    assert reciprocal_rank([5, 4, 3], rel) == 1.0  # 5 на ранге 1
    assert reciprocal_rank([2, 3, 4], rel) == 0.0


def test_recall_at_k() -> None:
    rel = {1: 1.0, 2: 0.5, 3: 0.0, 4: 1.0}  # положительных: {1,2,4}
    assert recall_at_k([1, 3, 5], rel, k=3) == 1 / 3
    assert recall_at_k([1, 2, 4], rel, k=5) == 1.0
    assert recall_at_k([3, 5, 6], rel, k=5) == 0.0


def test_recall_no_positives() -> None:
    rel = {1: 0.0, 2: 0.4}  # все < threshold
    assert recall_at_k([1, 2], rel, k=2) == 0.0


def test_dcg_known_values() -> None:
    """DCG@3 для idealized [1, 0.5, 0] vs [0, 0.5, 1]."""
    rel = {10: 1.0, 20: 0.5, 30: 0.0}
    ideal = dcg([10, 20, 30], rel, k=3)
    # 1/log2(2) + 0.5/log2(3) + 0/log2(4)
    expected = 1.0 + 0.5 / math.log2(3)
    assert math.isclose(ideal, expected)

    reversed_score = dcg([30, 20, 10], rel, k=3)
    # 0/log2(2) + 0.5/log2(3) + 1.0/log2(4) = 0.5/1.585 + 1/2 = 0.815
    expected_rev = 0.5 / math.log2(3) + 1.0 / math.log2(4)
    assert math.isclose(reversed_score, expected_rev)


def test_ndcg_perfect_ranking() -> None:
    rel = {1: 1.0, 2: 0.5, 3: 0.0}
    assert math.isclose(ndcg_at_k([1, 2, 3], rel, k=3), 1.0)


def test_ndcg_inverse_ranking() -> None:
    """Полностью обратный порядок — NDCG < 1."""
    rel = {1: 1.0, 2: 0.5, 3: 0.0}
    val = ndcg_at_k([3, 2, 1], rel, k=3)
    assert 0.0 < val < 1.0


def test_ndcg_no_relevance() -> None:
    assert ndcg_at_k([1, 2, 3], {}, k=5) == 0.0
    assert ndcg_at_k([], {1: 1.0}, k=5) == 0.0


def test_ndcg_k_smaller_than_predicted() -> None:
    """NDCG@1 берёт только верх предсказания."""
    rel = {1: 1.0, 2: 0.5}
    # predicted=[2,1], ideal top-1 = 1.0
    assert math.isclose(ndcg_at_k([2, 1], rel, k=1), 0.5 / 1.0)


def test_mean_empty() -> None:
    assert mean([]) == 0.0
    assert math.isclose(mean([1.0, 2.0, 3.0]), 2.0)
