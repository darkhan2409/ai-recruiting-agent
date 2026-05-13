"""End-to-end smoke: sync → list → recommend.

Требует поднятого стека (`make up`). По умолчанию пропускается через
`addopts = -m 'not integration'`. Запуск: `pytest -m integration`.

Проверяет связь слоёв (ingestion + parsing + matching + API), не корректность
формулы матчинга — это покрывает eval-runner.
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.getenv("HCB_API_URL", "http://localhost:8000")
TIMEOUT = 120.0  # cold cache LLM-judge может занять до минуты


pytestmark = pytest.mark.integration


def test_e2e_smoke() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
        health = client.get("/health")
        assert health.status_code in {200, 503}, f"health: {health.status_code}"

        # Триггерим ingestion (либо `make demo` уже скопировал fixtures,
        # либо файлы положены вручную в storage/inbox).
        sync = client.post("/sync-mail")
        assert sync.status_code == 200, sync.text
        body = sync.json()
        assert "processed" in body and "skipped" in body and "failed" in body

        # Должна быть ≥1 вакансия (job_seeder автосидит из /app/jobs/).
        jobs = client.get("/jobs").json()
        assert isinstance(jobs, list) and len(jobs) >= 1, "ожидалось ≥1 job"
        job_id = jobs[0]["id"]

        # Кандидаты должны быть — иначе предупреждение, тест не падает (e2e
        # требует, чтобы инжест уже отработал хотя бы раз).
        candidates = client.get("/candidates").json()
        if not candidates:
            pytest.skip(
                "no candidates ingested yet; run POST /sync-mail with files "
                "in storage/inbox/ first (or run `make demo`)"
            )

        rec = client.get(f"/recommendations?job_id={job_id}&top_k=3")
        assert rec.status_code == 200, rec.text
        results = rec.json()
        assert isinstance(results, list)
        for item in results:
            assert 0.0 <= item["score"] <= 1.0
            assert item["recommendation"] in {"interview", "consider", "pass"}
            assert item["confidence"] in {"high", "medium", "low"}
