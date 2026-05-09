# HCB Recruiting Agent

> AI-агент для подбора кандидатов: автоматический intake резюме с почты, парсинг
> в структурированный профиль, ранжирование под текст вакансии с объяснением.

**Состояние:** MVP в разработке. Закрыты БЛОК 0–3 (foundations + ingestion +
parsing). В работе — БЛОК 4 (embeddings + Qdrant). Полный план —
[`hcb-recruiting-agent-plan.md`](./hcb-recruiting-agent-plan.md).

## Quickstart

Требуется Docker Desktop (≥24.x) с поддержкой compose v2.

```bash
git clone <repo>
cd ai_rec
cp .env.example .env       # USE_MOCKS=True по умолчанию — OpenAI ключ не нужен
docker compose up -d --build
```

Подъём стека из 4 сервисов (postgres + qdrant + api + streamlit) — ~1–2 мин
после первого билда. Проверка:

```bash
curl http://localhost:8000/health           # → 200, status=ok|degraded
open http://localhost:8000/docs             # Swagger
open http://localhost:8501                  # Streamlit (placeholder до БЛОКа 7)
```

## Демо ingestion (БЛОК 2 + 3)

```bash
mkdir -p storage/inbox
printf "John Smith\njohn.smith@example.com\nPython, Django, AWS, 7 years experience.\nMSc in CS from MIT.\n" > storage/inbox/sample.txt
docker compose exec api python -c "import asyncio; from src.ingestion.pipeline import poll_once_default; print(asyncio.run(poll_once_default()))"
docker compose exec postgres psql -U hcb -d hcb -c "SELECT id, language, parsed_data->>'full_name', parsed_data->>'email', jsonb_array_length(parsed_data->'skills') AS skills FROM candidates;"
```

Поддержанные форматы: `.pdf` (через pdfplumber), `.docx` (через mammoth), `.txt`.

## Стек (MVP)

- **API:** FastAPI 0.119 + uvicorn + APScheduler в lifespan
- **БД:** PostgreSQL 15 + SQLAlchemy 2.0 async + asyncpg + Alembic
- **Vector DB:** Qdrant 1.11 (matching — БЛОК 4)
- **Парсинг:** pdfplumber + mammoth + langdetect
- **LLM:** OpenAI gpt-4o-mini для extraction (mock-режим в dev) — БЛОК 3
- **UI:** Streamlit (БЛОК 7)
- **Lint/types:** ruff + mypy --strict (на `src/schemas.py` и `src/parsing/`)

Layout-aware парсер (LlamaParse / LandingAI ADE) — roadmap для production.

## Документы

- [`CLAUDE.md`](./CLAUDE.md) — правила написания кода и workflow
- [`DATA_POLICY.md`](./DATA_POLICY.md) — обязательства по PII (RK 152-V)
- [`hcb-recruiting-agent-plan.md`](./hcb-recruiting-agent-plan.md) — план реализации
- [`PROGRESS.md`](./PROGRESS.md) — рабочий блокнот, решения принятые в процессе

## Known limitations (MVP)

- Нет аутентификации API (`БЛОК 10.3 / production`)
- Нет VLM-OCR для PDF-сканов — `len(text) < 200` в PDF → quarantine `vlm_extract_failed` (БЛОК 10.2)
- `SpacyExtractor` — stub; в `USE_MOCKS=True` extract идёт через regex-mock LLMExtractor
- Юнит-тесты — БЛОК 9
- README развёрнутый (mermaid + cost+latency table) — БЛОК 9.1
