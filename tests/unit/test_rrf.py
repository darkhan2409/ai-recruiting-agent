"""Unit-тесты для Reciprocal Rank Fusion (production hybrid pipeline).

rrf_merge — pure function, регрессии невидимы на eval-метриках (сглаживаются).
Тесты — gate на математику формулы 1/(k+rank) и поведение на пустых/частичных
рейтингах.
"""

from __future__ import annotations

import math

from src.matching.rrf import rrf_merge


def test_single_ranking_preserves_order() -> None:
    """С одним ranking выход — тот же порядок, RRF-score = 1/(k+rank)."""
    out = rrf_merge([[10, 20, 30]], k=60)
    assert [doc_id for doc_id, _ in out] == [10, 20, 30]
    assert math.isclose(out[0][1], 1 / 61)
    assert math.isclose(out[1][1], 1 / 62)
    assert math.isclose(out[2][1], 1 / 63)


def test_candidate_in_both_rankings_sums_scores() -> None:
    """Кандидат в обоих ranking → score = сумма двух 1/(k+rank)."""
    out = dict(rrf_merge([[1, 2], [1, 3]], k=60))
    assert math.isclose(out[1], 1 / 61 + 1 / 61)
    assert math.isclose(out[2], 1 / 62)
    assert math.isclose(out[3], 1 / 62)


def test_candidate_only_in_one_present_but_ranked_lower() -> None:
    """Кандидат в одном ranking есть, в другом нет — присутствует, но ниже общих."""
    out = rrf_merge([[1, 2, 3], [1, 4, 5]], k=60)
    ids = [doc_id for doc_id, _ in out]
    assert ids[0] == 1  # есть в обоих
    assert set(ids) == {1, 2, 3, 4, 5}


def test_empty_ranking_is_ignored() -> None:
    """Пустой ranking не падает, просто не вносит вклад."""
    out = rrf_merge([[1, 2], []], k=60)
    ids = [doc_id for doc_id, _ in out]
    assert ids == [1, 2]


def test_all_empty_returns_empty() -> None:
    assert rrf_merge([], k=60) == []
    assert rrf_merge([[], []], k=60) == []


def test_top_n_truncates() -> None:
    out = rrf_merge([[1, 2, 3, 4, 5]], k=60, top_n=3)
    assert len(out) == 3
    assert [doc_id for doc_id, _ in out] == [1, 2, 3]


def test_top_n_none_returns_all() -> None:
    out = rrf_merge([[1, 2, 3]], k=60, top_n=None)
    assert len(out) == 3


def test_k_affects_top_reward() -> None:
    """Меньшее k сильнее награждает топ-позиции (формула 1/(k+rank))."""
    ranking = [[1, 2, 3]]
    out_low_k = rrf_merge(ranking, k=1)
    out_high_k = rrf_merge(ranking, k=60)
    # Top score при k=1: 1/2=0.5; при k=60: 1/61≈0.0164
    assert out_low_k[0][1] > out_high_k[0][1]
    # Разрыв top-vs-second больше при низком k
    ratio_low = out_low_k[0][1] / out_low_k[1][1]
    ratio_high = out_high_k[0][1] / out_high_k[1][1]
    assert ratio_low > ratio_high


def test_deterministic() -> None:
    """Одинаковый вход → одинаковый выход (важно для воспроизводимости eval)."""
    rankings = [[10, 20, 30], [20, 40, 10]]
    out1 = rrf_merge(rankings, k=60)
    out2 = rrf_merge(rankings, k=60)
    assert out1 == out2
