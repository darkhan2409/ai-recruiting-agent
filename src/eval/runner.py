"""`python -m src.eval.runner` — прогон golden eval + markdown-отчёт.

Для каждой `(job, method)` пары:
  1. `find_candidates(job, method, top_k=K, min_score=0.0)` — берём БЕЗ
     min-score фильтра, чтобы Recall@K не зависел от tuning'а.
  2. Считаем NDCG@5 / Hit@5 / MRR / Recall@10 по `GoldenSet.relevance`.

Затем агрегируем по методу и по RU/EN split. Пишем `reports/eval_YYYY-MM-DD.md`
(текущая дата UTC). Файл готов к embedding в README.

`find_candidates` нескомпрометирован — мы вызываем ту же функцию, что и
production API (`GET /recommendations`). Это даёт честное сравнение методов.

Логи прогресса в stderr (per-job + per-method), чтобы пользователь видел
ход eval — может занимать минуты при USE_MOCKS=False (real LLM).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.db import session_factory
from src.eval.golden import (
    DEFAULT_LABELS_PATH,
    GoldenSet,
    load_entries,
    resolve,
)
from src.eval.metrics import hit_at_k, mean, ndcg_at_k, recall_at_k, reciprocal_rank
from src.matching.pipeline import Method, find_candidates
from src.schemas import Job

logger = logging.getLogger(__name__)

DEFAULT_METHODS: tuple[Method, ...] = ("dense", "tfidf", "llm", "hybrid")
TOP_K_RANKED = 5  # для NDCG@K, Hit@K
RECALL_K = 10  # для Recall@K — шире, чем top_k_ranked


@dataclass(slots=True)
class JobMethodResult:
    """Метрики одного (job, method)."""

    job_id: int
    job_filename: str
    job_language: str
    method: Method
    predicted: list[int]
    ndcg5: float
    hit5: float
    mrr: float
    recall10: float
    latency_ms: float


@dataclass(slots=True)
class AggregatedMethod:
    """Aggregated метрики по методу (across all jobs)."""

    method: Method
    ndcg5_mean: float
    hit5_mean: float
    mrr_mean: float
    recall10_mean: float
    latency_p50_ms: float
    latency_p95_ms: float
    per_language: dict[str, dict[str, float]] = field(default_factory=dict)


async def _eval_one(
    job: Job,
    method: Method,
    relevance: dict[int, float],
) -> JobMethodResult:
    """Один прогон find_candidates → метрики."""
    started = time.perf_counter()
    async with session_factory() as session:
        results = await find_candidates(
            job,
            session,
            top_k=max(TOP_K_RANKED, RECALL_K),
            method=method,
            min_score=0.0,  # eval БЕЗ min-score gating; tuning отдельно
        )
        # AsyncSession context exit без commit → откатывает pending writes
        # (match_cache, embedding_cache jobs.embedding_cached). Без explicit
        # commit eval теряет cache between runs — каждый прогон стартует с
        # пустого кэша, тратит OpenAI квоту заново. Pipeline сам не коммитит,
        # это решение вызывающего (см. docstring judge_with_cache).
        await session.commit()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    predicted = [r.candidate_id for r in results]
    return JobMethodResult(
        job_id=job.id or 0,
        job_filename="",  # set by caller
        job_language=job.language.value,
        method=method,
        predicted=predicted,
        ndcg5=ndcg_at_k(predicted, relevance, TOP_K_RANKED),
        hit5=hit_at_k(predicted, relevance, TOP_K_RANKED),
        mrr=reciprocal_rank(predicted, relevance),
        recall10=recall_at_k(predicted, relevance, RECALL_K),
        latency_ms=elapsed_ms,
    )


async def run_eval(
    golden: GoldenSet,
    methods: Sequence[Method] = DEFAULT_METHODS,
) -> list[JobMethodResult]:
    """Прогон всех (job, method) пар. Последовательный — eval не throughput-bound."""
    rows: list[JobMethodResult] = []
    job_ids = sorted(golden.relevance.keys())
    total = len(job_ids) * len(methods)
    done = 0
    for jid in job_ids:
        job_obj = golden.jobs.get(jid)
        if job_obj is None:
            logger.warning("eval: job_id=%s not in jobs dict, skip", jid)
            continue
        assert isinstance(job_obj, Job)
        rel = golden.relevance[jid]
        for method in methods:
            done += 1
            logger.info(
                "eval [%d/%d]: job=%s method=%s (%d candidates labeled)",
                done,
                total,
                golden.job_id_to_filename.get(jid, jid),
                method,
                len(rel),
            )
            row = await _eval_one(job_obj, method, rel)
            row.job_filename = golden.job_id_to_filename.get(jid, "")
            rows.append(row)
    return rows


def aggregate(rows: list[JobMethodResult]) -> list[AggregatedMethod]:
    """Сгруппировать по method + per-language split."""
    by_method: dict[Method, list[JobMethodResult]] = defaultdict(list)
    for r in rows:
        by_method[r.method].append(r)

    out: list[AggregatedMethod] = []
    for method, items in by_method.items():
        ndcgs = [r.ndcg5 for r in items]
        hits = [r.hit5 for r in items]
        mrrs = [r.mrr for r in items]
        recalls = [r.recall10 for r in items]
        latencies = sorted(r.latency_ms for r in items)

        per_lang: dict[str, dict[str, float]] = {}
        for lang in {r.job_language for r in items}:
            lang_items = [r for r in items if r.job_language == lang]
            per_lang[lang] = {
                "ndcg5": mean([r.ndcg5 for r in lang_items]),
                "hit5": mean([r.hit5 for r in lang_items]),
                "mrr": mean([r.mrr for r in lang_items]),
                "recall10": mean([r.recall10 for r in lang_items]),
                "n": float(len(lang_items)),
            }

        out.append(
            AggregatedMethod(
                method=method,
                ndcg5_mean=mean(ndcgs),
                hit5_mean=mean(hits),
                mrr_mean=mean(mrrs),
                recall10_mean=mean(recalls),
                latency_p50_ms=_percentile(latencies, 50),
                latency_p95_ms=_percentile(latencies, 95),
                per_language=per_lang,
            )
        )
    # Стабильный порядок строк в отчёте: dense, tfidf, llm, hybrid
    order = {m: i for i, m in enumerate(DEFAULT_METHODS)}
    out.sort(key=lambda a: order.get(a.method, 99))
    return out


def _percentile(sorted_vals: Sequence[float], pct: int) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = max(0, min(len(sorted_vals) - 1, round(len(sorted_vals) * pct / 100) - 1))
    return sorted_vals[idx]


def render_markdown(
    rows: list[JobMethodResult],
    agg: list[AggregatedMethod],
    golden: GoldenSet,
    methods: Sequence[Method],
) -> str:
    """Markdown-отчёт, готовый к коммиту в reports/ и embedding в README."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append(f"# Eval report — {today}")
    lines.append("")
    lines.append(
        f"Golden: {len(golden.relevance)} jobs, "
        f"{sum(len(r) for r in golden.relevance.values())} pairs. "
        f"Methods: {', '.join(methods)}."
    )
    if golden.missing_resumes or golden.missing_jobs:
        lines.append("")
        lines.append(
            f"Skipped {len(golden.skipped_entries)} entries — "
            f"missing resumes={sorted(golden.missing_resumes)}, "
            f"missing jobs={sorted(golden.missing_jobs)}."
        )

    lines.append("")
    lines.append("## Метрики по методам")
    lines.append("")
    lines.append("| Method | NDCG@5 | Hit@5 | MRR | Recall@10 | p50 ms | p95 ms |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for a in agg:
        lines.append(
            f"| {a.method} | {a.ndcg5_mean:.3f} | {a.hit5_mean:.3f} | "
            f"{a.mrr_mean:.3f} | {a.recall10_mean:.3f} | "
            f"{a.latency_p50_ms:.0f} | {a.latency_p95_ms:.0f} |"
        )

    lines.append("")
    lines.append("## RU / EN split (NDCG@5)")
    lines.append("")
    langs = sorted({lang for a in agg for lang in a.per_language})
    if langs:
        header = "| Method | " + " | ".join(f"{lang} (n)" for lang in langs) + " |"
        sep = "|---|" + "|".join("---:" for _ in langs) + "|"
        lines.append(header)
        lines.append(sep)
        for a in agg:
            cells: list[str] = [a.method]
            for lang in langs:
                metrics = a.per_language.get(lang)
                if metrics:
                    cells.append(f"{metrics['ndcg5']:.3f} ({int(metrics['n'])})")
                else:
                    cells.append("—")
            lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("## Подробности по вакансиям")
    lines.append("")
    by_job: dict[int, list[JobMethodResult]] = defaultdict(list)
    for r in rows:
        by_job[r.job_id].append(r)
    for jid in sorted(by_job):
        first = by_job[jid][0]
        lines.append(f"### {first.job_filename} (lang={first.job_language})")
        lines.append("")
        lines.append("| Method | NDCG@5 | Hit@5 | MRR | Recall@10 | predicted top-5 |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for r in sorted(by_job[jid], key=lambda x: x.method):
            names = [golden.candidate_id_to_filename.get(c, str(c))[:20] for c in r.predicted[:5]]
            lines.append(
                f"| {r.method} | {r.ndcg5:.3f} | {r.hit5:.3f} | "
                f"{r.mrr:.3f} | {r.recall10:.3f} | {', '.join(names)} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def serialize_for_json(
    rows: list[JobMethodResult],
    agg: list[AggregatedMethod],
    golden: GoldenSet,
    methods: Sequence[Method],
) -> dict[str, Any]:
    """Структурированный snapshot eval — для notebook / dashboards.

    Schema стабильный (notebooks/methods_comparison.ipynb опирается на него).
    Изменения здесь требуют обновления notebook.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return {
        "date": today,
        "golden": {
            "jobs": len(golden.relevance),
            "pairs": sum(len(r) for r in golden.relevance.values()),
            "missing_resumes": sorted(golden.missing_resumes),
            "missing_jobs": sorted(golden.missing_jobs),
        },
        "methods": list(methods),
        "aggregated": [
            {
                "method": a.method,
                "ndcg5": a.ndcg5_mean,
                "hit5": a.hit5_mean,
                "mrr": a.mrr_mean,
                "recall10": a.recall10_mean,
                "p50_ms": a.latency_p50_ms,
                "p95_ms": a.latency_p95_ms,
            }
            for a in agg
        ],
        "per_language": {
            a.method: {lang: m["ndcg5"] for lang, m in a.per_language.items()} for a in agg
        },
        "per_job": [
            {
                "job_filename": r.job_filename,
                "method": r.method,
                "language": r.job_language,
                "ndcg5": r.ndcg5,
                "hit5": r.hit5,
                "mrr": r.mrr,
                "recall10": r.recall10,
                "latency_ms": r.latency_ms,
                "predicted_top5_filenames": [
                    golden.candidate_id_to_filename.get(c, str(c)) for c in r.predicted[:5]
                ],
            }
            for r in rows
        ],
    }


def write_report(text: str, json_data: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """Записать markdown + JSON в reports/eval_YYYY-MM-DD.{md,json}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    md_path = out_dir / f"eval_{today}.md"
    json_path = out_dir / f"eval_{today}.json"
    md_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


async def main_async(
    labels_path: Path,
    jobs_dir: Path,
    out_dir: Path,
    methods: Sequence[Method],
) -> tuple[Path, Path]:
    entries = load_entries(labels_path)
    logger.info("eval: loaded %d golden entries from %s", len(entries), labels_path)
    async with session_factory() as session:
        golden = await resolve(entries, session, jobs_dir=jobs_dir)
    logger.info(
        "eval: resolved %d jobs, %d candidates labeled (skipped %d entries)",
        len(golden.relevance),
        len(golden.candidate_id_to_filename),
        len(golden.skipped_entries),
    )
    rows = await run_eval(golden, methods=methods)
    agg = aggregate(rows)
    markdown = render_markdown(rows, agg, golden, methods)
    json_data = serialize_for_json(rows, agg, golden, methods)
    md_path, json_path = write_report(markdown, json_data, out_dir)
    logger.info("eval: report written to %s + %s", md_path, json_path)
    return md_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="HCB Recruiting — golden eval runner")
    parser.add_argument(
        "--golden",
        type=Path,
        default=DEFAULT_LABELS_PATH,
        help=f"Path to labels.json (default: {DEFAULT_LABELS_PATH})",
    )
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=Path("jobs"),
        help="Path to seed jobs dir (default: jobs)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports"),
        help="Output dir for markdown reports (default: reports)",
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=list(DEFAULT_METHODS),
        default=list(DEFAULT_METHODS),
        help="Methods to evaluate (subset of dense/tfidf/llm/hybrid)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    try:
        md_path, json_path = asyncio.run(
            main_async(args.golden, args.jobs_dir, args.out, args.methods)
        )
    except Exception:
        logger.exception("eval: runner failed")
        return 1
    print(md_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# Re-export для notebook / debug — порядок tests/notebook видят через
# `from src.eval.runner import run_eval, aggregate, render_markdown`.
__all__ = [
    "AggregatedMethod",
    "JobMethodResult",
    "aggregate",
    "main",
    "main_async",
    "render_markdown",
    "run_eval",
    "write_report",
]


# statistics import kept-as-is — `_percentile` использует ручной алгоритм,
# а statistics.quantiles округляет иначе, чем привычный «classic».
_ = statistics
