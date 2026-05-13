"""Reciprocal Rank Fusion — pure function для слияния ranked-списков retriever-ов.

Используется в hybrid pipeline (БЛОК 5.7): объединяет dense + tfidf rankings
без необходимости калибровать score-шкалы. RRF score кандидата — сумма
`1 / (k + rank)` по всем rankings, где он встретился. `k=60` — стандарт по
Cormack et al. 2009 (Reciprocal Rank Fusion outperforms Condorcet and
individual Rank Learning Methods, SIGIR'09).

Решает false negatives: кандидат проходит дальше, если силён хотя бы в
одном методе (даже если в другом отсутствует).
"""

from __future__ import annotations

from collections import defaultdict


def rrf_merge(
    rankings: list[list[int]],
    k: int = 60,
    top_n: int | None = None,
) -> list[tuple[int, float]]:
    """Слить несколько ranked-списков candidate_id по формуле RRF.

    Args:
        rankings: Список rankings, каждый — упорядоченный список candidate_id
            (позиция = rank, начиная с 1). Пустые rankings игнорируются.
        k: Константа RRF; меньше — сильнее награда за топ-позиции, больше —
            более «сглаженная» fusion. Стандарт 60 (Cormack 2009).
        top_n: Если задан — обрезать выход до top_n элементов.

    Returns:
        Список `(candidate_id, rrf_score)` отсортированный по убыванию score.
        При тай-брейке порядок определяется реализацией dict (insertion order
        в Python 3.7+) — детерминирован при детерминированном входе.
    """
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += 1.0 / (k + rank)
    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return fused[:top_n] if top_n is not None else fused
