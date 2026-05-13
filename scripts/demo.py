"""End-to-end demo: populate систему за одну команду.

Шаги:
  [1/6] /health — sanity на стек (api + postgres + qdrant).
  [2/6] /candidates, /jobs — текущее состояние. Если candidates>0,
        пропускаем copy+sync (idempotent re-run).
  [3/6] Копировать fixtures `tests/fixtures/golden/resumes/` → `storage/inbox/`
        с сохранением mtime (`shutil.copy2`). mtime стабилен → `message_id` в
        FolderSource (`folder:<sha1(path|mtime)>`) детерминирован → повторный
        запуск не вызывает re-ingest.
  [4/6] POST /sync-mail — синхронный (возвращает counts после полной обработки).
        Под Tier 1 OpenAI + real LLM-extract на 20 резюме ~60-120 сек.
  [5/6] Финальное состояние: jobs / candidates / quarantine / qdrant points.
  [6/6] Showcase: top-5 рекомендаций под первую вакансию.

Использование: `make demo` или `python scripts/demo.py`.
Env overrides: `HCB_API_URL`, `HCB_QDRANT_URL`, `HCB_QDRANT_COLLECTION`.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import httpx

BASE_URL = os.getenv("HCB_API_URL", "http://localhost:8000")
QDRANT_URL = os.getenv("HCB_QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("HCB_QDRANT_COLLECTION", "resumes")
TIMEOUT = 600.0  # cold-start ingestion 20 резюме × LLM-extract под Tier 1

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "golden" / "resumes"
INBOX_DIR = REPO_ROOT / "storage" / "inbox"

ALLOWED_EXTS = {".pdf", ".docx", ".txt"}


def _print_row(cells: list[str], widths: list[int]) -> None:
    print(" | ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)))


def _copy_fixtures() -> int:
    """Копировать все fixture-резюме в inbox.

    `shutil.copy2` сохраняет mtime → FolderSource message_id стабилен между
    запусками (`folder:<sha1(path|mtime)>`). Повторный `make demo` не вызывает
    re-ingest.
    """
    if not FIXTURES_DIR.exists():
        print(f"   ⚠ fixtures dir not found: {FIXTURES_DIR}")
        return 0
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sorted(FIXTURES_DIR.iterdir()):
        if src.is_file() and src.suffix.lower() in ALLOWED_EXTS:
            shutil.copy2(src, INBOX_DIR / src.name)
            count += 1
    return count


def _qdrant_points(client: httpx.Client) -> int | None:
    """Получить points_count из Qdrant REST API; None при ошибке."""
    try:
        r = client.get(f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}")
        if r.status_code != 200:
            return None
        result = r.json().get("result", {})
        # points_count надёжнее vectors_count (последний может быть None)
        return int(result.get("points_count") or 0)
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def main() -> int:  # noqa: C901
    print("== HCB Recruiting Agent demo ==")
    print(f"API: {BASE_URL}  Qdrant: {QDRANT_URL}\n")

    with httpx.Client(timeout=TIMEOUT) as client:
        # [1/6] health
        try:
            health = client.get(f"{BASE_URL}/health")
            status = health.json().get("status", "?")
            print(f"[1/6] /health → {health.status_code} {status}")
        except httpx.HTTPError as exc:
            print(f"[1/6] /health → FAIL: {exc}")
            print("\nАpi недоступен. Поднимите стек: `make up`.")
            return 1

        # [2/6] current state
        jobs_list = client.get(f"{BASE_URL}/jobs").json()
        candidates_list = client.get(f"{BASE_URL}/candidates").json()
        print(
            f"[2/6] current state: jobs={len(jobs_list)} "
            f"candidates={len(candidates_list)}"
        )

        # [3/6] + [4/6] populate if empty
        if not candidates_list:
            copied = _copy_fixtures()
            print(f"[3/6] copy fixtures: {copied} files → {INBOX_DIR}")

            if copied == 0:
                print("   ⚠ нет файлов для ingestion, пропуск sync-mail")
            else:
                eta = max(60, copied * 6)
                print(
                    f"[4/6] POST /sync-mail (ETA ~{eta} сек для {copied} резюме "
                    f"под Tier 1)..."
                )
                t0 = time.monotonic()
                sync = client.post(f"{BASE_URL}/sync-mail")
                sync.raise_for_status()
                counts = sync.json()
                elapsed = time.monotonic() - t0
                print(f"      done in {elapsed:.1f}s: {counts}")
        else:
            print("[3/6] candidates уже есть — copy пропущен (idempotent)")
            print("[4/6] sync-mail пропущен (idempotent)")

        # [5/6] final state
        candidates_list = client.get(f"{BASE_URL}/candidates").json()
        jobs_list = client.get(f"{BASE_URL}/jobs").json()
        quarantine_list = client.get(f"{BASE_URL}/quarantine").json()
        qdrant_pts = _qdrant_points(client)

        print("\n[5/6] Final state:")
        print(f"   jobs:           {len(jobs_list)}")
        print(f"   candidates:     {len(candidates_list)}")
        print(f"   quarantine:     {len(quarantine_list)}")
        print(
            f"   qdrant points:  "
            f"{qdrant_pts if qdrant_pts is not None else '? (collection not ready)'}"
        )

        if not candidates_list or not jobs_list:
            print("\n⚠ Система не готова к showcase: нет кандидатов или вакансий.")
            print(f"   Swagger: {BASE_URL}/docs   |   Streamlit: http://localhost:8501")
            return 0

        # [6/6] showcase
        first_job = jobs_list[0]
        job_id = first_job["id"]
        title = first_job["title"]
        print(
            f"\n[6/6] showcase: GET /recommendations?job_id={job_id} ({title})"
        )
        rec = client.get(
            f"{BASE_URL}/recommendations?job_id={job_id}&top_k=5"
        )
        rec.raise_for_status()
        body = rec.json()
        # API возвращает либо {results: [...]}, либо list напрямую
        results = body.get("results", body) if isinstance(body, dict) else body

    print()
    headers = ["cand_id", "score", "rec", "conf", "matched skills"]
    widths = [8, 6, 10, 7, 50]
    _print_row(headers, widths)
    _print_row(["-" * w for w in widths], widths)
    for r in results:
        skills = ", ".join(r.get("matched_skills", [])[:3]) or "—"
        _print_row(
            [
                str(r["candidate_id"]),
                f"{r['score']:.2f}",
                r["recommendation"],
                r["confidence"],
                skills[: widths[4]],
            ],
            widths,
        )
    print(f"\nSwagger: {BASE_URL}/docs   |   Streamlit: http://localhost:8501")
    return 0


if __name__ == "__main__":
    sys.exit(main())
