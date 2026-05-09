# PROGRESS — HCB Recruiting Agent

> Рабочий блокнот. Для меня и Claude Code, не для защиты.
> После закрытия задачи — обновить "Сейчас работаю" + поставить `[x]` в "Что готово".

**Последнее обновление:** 2026-05-09 — закрыт БЛОК 3: Parsing. PDF — `pdfplumber` (pdfminer.six, текст по X/Y), DOCX — `mammoth.extract_raw_text` (whole-document conversion), TXT — `Path.read_text`. Sync→async через `asyncio.to_thread`. Truncate 30 000 chars; `MIN_TEXT_CHARS=200`. Sanitize: 9 regex injection-паттернов. langdetect с `DetectorFactory.seed=0` → `Language.RU/EN`/None. `LLMExtractor` (USE_MOCKS=True → regex-derived Resume; USE_MOCKS=False → `AsyncOpenAI.beta.chat.completions.parse(response_format=Resume)` с tenacity 3-retry exp 1→8s). `SpacyExtractor` — stub в MVP. `parse_resume()` оркестратор: extract→length→language→injection→extract; ошибки мапятся на `QuarantineReason`. Интеграция в `ingestion.process_email`: success → Candidate с заполненными raw_text/parsed_data/language; failure → record_quarantine. Verification: TXT RU/EN happy paths, DOCX (mammoth), quarantine для text_too_short и prompt_injection_suspected (pattern=ignore_previous), worker resilience — unhandled = 0.

---

## Сейчас работаю

Готовлюсь к БЛОКу 4 — Embeddings + Vector DB (`EmbeddingProvider` Protocol: e5 multilingual + OpenAI option, кэш hash(text) в `embedding_cache`, Qdrant collection cosine).

---

## Что дальше

1. БЛОК 4 — Embeddings + Vector DB (`EmbeddingProvider` Protocol: e5 multilingual + OpenAI option, кэш `embedding_cache`, Qdrant collection cosine)
2. БЛОК 5 — Matching (3 retrievers: dense / TF-IDF / LLM-judge, RRF fusion, anti-hallucination, cache, оркестратор `find_candidates`)
3. БЛОК 6 — API (`GET/POST /recommendations`, `POST /sync-mail`, `DELETE /candidates/{id}`, `GET /quarantine`, FastAPI Depends DI, job seeder)
4. БЛОК 7 — UI + объяснимость (Streamlit: top-5, expand-карточки, confidence badges, min_score slider, quarantine review)

---

## Что готово

### БЛОК 0 — Foundations
- [x] `pyproject.toml` (pydantic, pydantic-settings, ruff, mypy strict на schemas.py)
- [x] `.env.example` (USE_MOCKS=True + комментарии-плейсхолдеры будущих блоков)
- [x] `.gitignore` (.env, __pycache__, .venv, кэши, storage/, reports/)
- [x] `src/__init__.py` (пакет)
- [x] `src/config.py`: pydantic-settings `Settings(use_mocks: bool = True)` + синглтон
- [x] `src/schemas.py`: Pydantic v2 — `Resume`, `Job`, `MatchResult`, `ExperienceItem`, `EducationItem`, enums `Language`/`Confidence`/`Recommendation` (StrEnum), `_StrictModel` с `extra='forbid'`, Field descriptions для Swagger

### БЛОК 1 — Инфраструктура
- [x] `Dockerfile` multi-stage, non-root user `hcb`, healthcheck `curl /health`
- [x] `.dockerignore`
- [x] `docker-compose.yml`: postgres:15-alpine + qdrant:v1.11.0 + api + streamlit; healthchecks для postgres/api/streamlit; api зависит от postgres healthy; streamlit override healthcheck на `/_stcore/health` :8501
- [x] Alembic init (alembic.ini + async env.py + script.py.mako) + первая миграция `0001_initial` (Candidate, Job, Match, ProcessedEmail, Quarantine, DeadLetter, AuditLog, MatchCache, EmbeddingCache + индексы FK/source_message_id/target_id)
- [x] `src/db.py` — все 9 SQLAlchemy 2.0 моделей (Mapped/mapped_column, JSONB) + `engine` + `session_factory` + `get_session()` для FastAPI Depends
- [x] `src/main.py` — FastAPI skeleton + `CorrelationIdMiddleware` + global exception handler через `register_exception_handlers`
- [x] `GET /health` (`src/api/health.py`) — пинг postgres (SELECT 1) + qdrant (`/readyz`) + версия из `settings.app_version`
- [x] APScheduler `AsyncIOScheduler` в FastAPI lifespan (jobs: `ingestion_tick` IntervalTrigger(60s), `retention_cleanup` CronTrigger(hour=3))

### БЛОК 2 — Ingestion
- [ ] Yandex.Почта заведена, креды в `.env` (для прод-демо; в dev — `USE_MOCKS=True` → FolderSource)
- [x] `IngestionSource` Protocol (`src/ingestion/base.py`) + `IMAPSource` (asyncio.to_thread + tenacity 3 attempts exp 1→8s) + `FolderSource` (scan `inbox_dir` по ALLOWED_EXTS), единый `poll_once_default()`
- [x] Idempotency: `processed_emails(message_id PK)`, статусы `ingested/quarantined/dead_letter` терминальные; `is_terminal()` skip; `upsert_processed()` через `pg_insert.on_conflict_do_update`
- [x] Dead-letter: `record_failure()` атомарно `attempts = attempts + 1`; при `>=3` → INSERT `dead_letter_emails` + статус dead_letter; quarantine table принимает `unsupported_mime`/`too_large` в этом блоке (`text_too_short`/`vlm_extract_failed`/etc — БЛОК 3); `ingestion_tick` обёрнут в try/except — worker не падает
- [x] `_save_attachment()` сохраняет в `<resumes_dir>/<safe(message_id)>/<filename>`; `Candidate(file_path, source_message_id, raw_text=NULL, parsed_data=NULL)` — поля `raw_text`/`parsed_data` заполняет БЛОК 3
- [x] Volume permissions: Dockerfile mkdir+chown `/app/storage/{resumes,inbox}` под user `hcb` ДО `USER hcb`, чтобы named volume инициализировался корректно
- [x] Bind-mount `./storage/inbox:/app/storage/inbox` в compose — разработчик кладёт PDF/DOCX/TXT на хост, контейнер видит мгновенно

### БЛОК 3 — Parsing
- [x] **pdfplumber (PDF) + mammoth (DOCX)** — MVP стек (~30 MB pip, MIT/BSD). Image ~250 MB, см. запись 2026-05-09. `asyncio.to_thread` для sync→async. Layout-aware парсер (LlamaParse / LandingAI ADE) — roadmap.
- [x] TXT через `Path.read_text(encoding="utf-8", errors="replace")`
- [x] Truncate до `MAX_TEXT_CHARS=30 000`; `MIN_TEXT_CHARS=200`
- [ ] **GPT-4o Vision API OCR fallback** — отложено в roadmap (БЛОК 10.2). В MVP при `len(text) < 200` для PDF → quarantine `vlm_extract_failed`; для txt/docx → `text_too_short`
- [x] Pydantic схема `Resume` (БЛОК 0) — переиспользована, `extra="forbid"` совместима со structured output
- [x] `ResumeExtractor` Protocol: `LLMExtractor` (gpt-4o-mini + `AsyncOpenAI.beta.chat.completions.parse(response_format=Resume)` + tenacity AsyncRetrying 3 attempts exp 1→8s; USE_MOCKS=True → regex-derived Resume) + `SpacyExtractor` (stub, raise в MVP)
- [x] `SYSTEM_PROMPT` с 7 safety rules + `build_user_prompt()` оборачивает в `<resume_content>...</resume_content>`
- [x] Regex-санитайзер с 9 паттернами: `ignore previous`, `disregard instructions`, `[system:`, `you are now`, `system prompt:`, `forget instructions`, `### system`, `<\|im_start\|>`, `<\|endofprompt\|>`. `detect_injection()` возвращает `(pattern_name, snippet ±40 chars)` для quarantine.details
- [x] `langdetect` с `DetectorFactory.seed=0` (детерминизм по README), маппинг `ru`/`en` → `Language` enum, прочее → None
- [x] Интеграция в `ingestion.process_email`: success → `Candidate(raw_text, parsed_data, language)`; failure → `record_quarantine(reason=parse.reason, details=parse.details)`

### БЛОК 4 — Embeddings + Vector DB
- [ ] `EmbeddingProvider` Protocol: `intfloat/multilingual-e5-large` primary + OpenAI `text-embedding-3-large` option
- [ ] **Префиксы** `query:` / `passage:` при encoding (обязательно по model card e5)
- [ ] Кэш эмбеддингов hash(text) → Postgres `embedding_cache(text_hash PK, vector, model_version)`
- [ ] Загрузка эмбеддингов резюме в Qdrant при ingestion (один раз)
- [ ] Эмбеддинг вакансии — кэшируется в `jobs.embedding_cached`

### БЛОК 5 — Matching
- [ ] `DenseRetriever` (Qdrant cosine, top-50)
- [ ] `TfidfRetriever` (sklearn `TfidfVectorizer(ngram_range=(1,2), max_features=50_000)` + cosine, top-50) — **в production hybrid**, закрывает букву ТЗ
- [ ] `BM25Retriever` (rank_bm25) — **только в research notebook (БЛОК 8)**, не в production hybrid
- [ ] RRF fusion `rrf_merge([dense, tfidf], k=60)` → top-20
- [ ] LLM-judge с Pydantic structured output (gpt-4o + asyncio.gather)
- [ ] Anti-hallucination: matched_skills ⊆ raw_text (substring + RapidFuzz)
- [ ] Кэш LLM-judge по `hash(resume_id, job_id, model_version, prompt_version)` с TTL 24h — версионирование инвалидирует stale кэш
- [ ] `find_candidates(job, top_k=5, method="hybrid", min_score=0.3)` — оркестратор; method="dense"/"tfidf"/"llm"/"hybrid" branching

### БЛОК 6 — API
- [ ] `GET /recommendations?job_id=X&top_k=5&method={dense|tfidf|llm|hybrid}&min_score=0.3` (default method=hybrid, min_score=0.3)
- [ ] `POST /recommendations` — ad-hoc по тексту вакансии, те же params в body
- [ ] `POST /sync-mail` — принудительный триггер IMAP poll (`{"processed": N}`); снимает «минуту тишины» на live-demo
- [ ] `DELETE /candidates/{id}` каскадно (DB + Qdrant + file + audit_log)
- [ ] `GET /quarantine` для UI review
- [ ] Job seeder из `/jobs/*.txt` на startup
- [ ] DI через FastAPI `Depends` (LLMExtractor, EmbeddingProvider, IngestionSource); `app.dependency_overrides` в тестах

### БЛОК 7 — UI + Объяснимость
- [ ] Streamlit главная: текстовая область / dropdown с job_id → таблица top-5 (имя / score / confidence badge / recommendation badge)
- [ ] Expand карточки: matched_skills (зелёные чипы) | gaps (красные) | extras (серые) | explanation | quotes
- [ ] Anti-hallucination как продуктовая фича: confidence={high,medium,low} + иконка предупреждения при low
- [ ] Sidebar: слайдер `min_score` (0.0–1.0, default 0.3) + info-блок «найдено N с релевантностью ≥ X»; явное сообщение «нет подходящих» при 0 результатов
- [ ] Streamlit-страница «Quarantine»: таблица + кнопки «mark as legitimate» / «delete»
- [ ] Кнопка «Скачать оригинал PDF»
- [ ] Sidebar: метрики pipeline (latency stages, embeddings provider, LLM cache hit rate)

### БЛОК 8 — Research + Eval
- [ ] Golden dataset 30-50 пар (`tests/fixtures/golden/labels.json`) — синтетика через LLM + ручная проверка subset
- [ ] `src/eval/metrics.py`: NDCG@5, Hit@5, MRR, Recall@10
- [ ] Notebook `notebooks/methods_comparison.ipynb`: **5 подходов** — dense / TF-IDF / **BM25 (modern baseline для сравнения с TF-IDF)** / LLM-judge / hybrid
- [ ] NER demo на одном резюме (spaCy ru/en) — закрывает требование ТЗ
- [ ] Keyword extraction comparison: KeyBERT + RAKE + LLM-skills
- [ ] Графики matplotlib (per method, RU vs EN, latency vs quality, ablation)
- [ ] **Error Analysis** cell: 3-5 кейсов где система ошиблась (false negative / hallucination / keyword stuffing / cross-lingual)
- [ ] Вывод: hybrid > отдельных, главный вклад — LLM-rerank
- [ ] `make eval` CLI → `reports/eval_YYYY-MM-DD.md`

### БЛОК 9 — Docs + Tests
- [ ] README: quickstart + mermaid + стек + метрики из make eval + cost+latency + limitations + demo + data governance + **Roadmap (production hardening)** отдельной секцией
- [ ] tests/fixtures/: 15-20 синтетических резюме (LLM-generated), из них **минимум 5 с complex layouts** (двухколоночный sidebar, таблицы, нестандартные секции) — на pdfplumber MVP покажут ограничения; **1-2 fixture-скана** для quarantine `vlm_extract_failed` пути; 5 вакансий
- [ ] Тесты: `test_text_extract.py`, `test_rrf.py`, `test_anti_hallucination.py`, `test_sanitize.py`, `test_pii.py`, `test_pipeline.py` (e2e smoke)
- [ ] Coverage 50-60% на `parsing/ + matching/`
- [ ] ARCHITECTURE.md (опционально): подробная диаграмма + 4 ADR-секции (embedder, vector store, hybrid, LLM-judge vs cross-encoder)
- [ ] FastAPI Swagger работает на `/docs`
- [ ] `mask_pii()` utility в `src/utils/pii.py` + unit-тест (email, phone, ФИО)
- [ ] Структурное логирование (JSON + correlation_id) с `mask_pii()` перед PII
- [ ] `RESUME_RETENTION_DAYS=180` configurable
- [ ] Audit log таблица (`actor, action, target_id, timestamp`)
- [ ] `USE_MOCKS=True` по умолчанию в `.env.example` — `LLMExtractor` и `IMAPSource` возвращают реалистичные mock-данные
- [ ] Synthetic fixtures (через LLM в `scripts/generate_fixtures.py`)
- [ ] `Makefile` (up / seed / demo / eval / test / lint / clean)
- [ ] `scripts/demo.py` end-to-end (healthcheck → seed → ingest → recommend → print)
- [ ] `scripts/seed_jobs.py` (5 вакансий из `/jobs/*.txt`, RU+EN)
- [ ] `scripts/generate_fixtures.py` (LLM-генерация 15-20 резюме + 30-50 golden пар)

### БЛОК 10 — Production hardening / Nice-to-have (4 задачи)
- [ ] 10.1 SMTP auto-reply кандидатам (Jinja2 RU/EN templates)
- [ ] 10.2 **Локальная VLM (Qwen2.5-VL-7B)** через Ollama/vLLM вместо GPT-4o Vision API — PII compliance (требует GPU 16-24GB VRAM, multilingual ru/en/kaz, окупается при scale ~50k резюме/мес)
- [ ] 10.3 Optional API key auth + rate limiting (slowapi)
- [ ] 10.4 Retention nightly cleanup через APScheduler (`src/workers/retention_cleanup.py`, тот же каскад что DELETE)

---

## Решения принятые в процессе

_Сюда писать короткие записи "выбрал X не Y, потому что...". Чтобы через 2 дня помнить, почему именно так._

- **2026-05-07**: Embedder primary — `intfloat/multilingual-e5-large` (HuggingFace), не OpenAI. Закрывает букву ТЗ ("Sentence-BERT/USE/HuggingFace"), оффлайн, бесплатно. OpenAI остаётся как опция через `EmbeddingProvider` Protocol (`EMBEDDER=e5|openai`) — на защите А/B сравнение в notebook. Обязательны префиксы `query:`/`passage:` согласно model card (без них -3-7 NDCG@10).
- **2026-05-07**: Vector store — Qdrant (по выбору пользователя). pgvector упомянут в ADR как simpler alt для <100k. Plan agent рекомендовал pgvector для упрощения compose, но Qdrant более production-grade.
- **2026-05-07**: TF-IDF — **unsupervised cosine similarity baseline**, не supervised LogisticRegression. На 30-50 golden парах (которые сами генерили LLM) supervised classifier = data leak + методологически некорректно. ML-компонент в системе представлен LLM-judge как Learning-to-Rank без потребности в labels.
- **2026-05-07**: spaCy NER — **в research notebook + ResumeExtractor fallback**, не как обязательный первый этап pipeline. Для домена резюме классические NER labels (PER/ORG/LOC/DATE) покрывают только 20% полезного; skills/должности достаёт LLM. На защите: «NER рассмотрен, для production LLM extraction точнее».
- **2026-05-07**: APScheduler — **в FastAPI процессе** (CLAUDE.md), не отдельный worker контейнер. Меньше точек отказа на демо. Защитная реплика: «для прод-масштаба — выношу в arq/celery».
- **2026-05-07**: Quarantine pattern перенесён из бонусов в **must-have**. Битые PDF, prompt injection, неизвестный язык — должны быть видимы рекрутёру; fail-loud для batch не вариант.
- **2026-05-07**: Prompt injection defense возвращён в must-have отдельным блоком. Это НЕ то же что anti-hallucination — anti-halluc проверяет output, prompt injection защищает от вредоносного input. ~30 строк кода (regex + XML tags + system safety).
- **2026-05-07**: Drop'нуты: Repository pattern, отдельный worker контейнер, Prometheus/Grafana, CI workflow, NeMo Guardrails, multi-vector embeddings, FileStorage/VectorStorage Protocols, отдельные docs-файлы (`error-matrix.md`, `cost-latency.md`). Все по причине overengineering для MVP / противоречие CLAUDE.md.
- **2026-05-07**: `min_score: float = 0.3` в `find_candidates()` + слайдер в Streamlit. Спасает edge case на live-demo: если в БД 100 кандидатов, но под вакансию подходящих 0 — без порога UI покажет топ-5 «случайных» с низкими score. С порогом — короткий/пустой список + warning «найдено N с релевантностью ≥ X». ~5 строк кода.
- **2026-05-08**: План переструктурирован в БЛОК 0 → БЛОК 10 (раньше 19 разделов с подсекциями 6.1, 7.1 …). Каждый БЛОК содержит задачи `X.Y` + acceptance. Преемственность: gap matrix, mermaid, стек, Q&A, DoD, Verification, Критичные файлы — на месте.
- **2026-05-08**: OCR — **GPT-4o Vision API** (план для БЛОК 3 OCR-fallback), не pytesseract. Причины: (1) уже в стеке для LLM-judge — ноль новых dependencies; (2) multilingual ru/en/kaz из коробки vs Tesseract где казахский требует training; (3) accuracy ~90-95% на структурированных документах vs 70-85% у Tesseract. Стоимость ~$0.001-0.003 на резюме при low detail. Trigger: PDF имеет image blocks но <200 chars текста после text-extract. Локальная VLM (Qwen2.5-VL-7B) — в roadmap для PII compliance. (Имплементация Vision OCR — БЛОК 10.2 roadmap, в MVP при коротком тексте — quarantine `vlm_extract_failed`.)
- **2026-05-08**: Truncate text до 30000 chars при extraction. Защита от senior резюме на 8 страниц + контроль latency LLM-extract. Quarantine reasons обновлены: `text_too_short` (битый файл, не резюме), `vlm_extract_failed` (скан + GPT-4o не справился), `unsupported_mime`, `too_large`.
- **2026-05-08**: В production hybrid pipeline — **TF-IDF + dense → RRF**, BM25 убран из production, остался только в research notebook как modern baseline. Причина: ТЗ требует «TF-IDF + ML классификатор как baseline для сравнения», использую TF-IDF в production hybrid чтобы закрыть букву ТЗ. BM25 в notebook демонстрирует знание современного стандарта, в roadmap — production upgrade.
- **2026-05-08**: Версионирование match-кэша по `hash(resume_id, job_id, model_version, prompt_version)`. Без этого после смены модели или промпта стейл кэш будет возвращать старые ответы. TTL 24h — компромисс для cost-control.
- **2026-05-08**: Q&A защиты расширены до 9 ключевых вопросов (+3): сложные резюме (multi-column / таблицы — pdfplumber MVP + LlamaParse в roadmap), сканы (GPT-4o Vision эвристика), TF-IDF vs BM25 (буква ТЗ vs современный стандарт). README roadmap: 8 пунктов (ConFit / Lightcast / cross-encoder reranker / Fairness audit / BM25 upgrade / VLM Qwen / VLM-парсеры / OpenAI Enterprise ZDR).
- **2026-05-08**: БЛОК 10 ужат с 7 до **4 задач** — удалены Multi-vector embeddings, Continuous learning loop, отдельная audit_log таблица. Причина: первые две — design-only слайды (не код), их место в roadmap README, не в task list; audit_log уже закрыт в БЛОК 9 (must-have). Остались: SMTP, локальная VLM, API auth, Retention nightly cleanup.
- **2026-05-08**: Бонус Q «scale на 1М резюме» переписан с конкретикой: e5 на on-prem GPU (не CPU), LLM-judge → дистилляция в cross-encoder bge-reranker-v2-m3 (LinkedIn JUDE methodology, -70-90% LLM вызовов), worker → arq/celery при росте throughput. APScheduler в FastAPI работает до ~100 резюме/мин — численная граница для перехода на отдельный worker.
- **2026-05-08**: Все упоминания «4 подхода» в плане заменены на «5 подходов» (gap matrix, структура репо, вступление БЛОК 8, DoD must-have). Согласованность с notebook задачей 8.2 — dense / TF-IDF / BM25 / LLM-judge / hybrid.
- **2026-05-09**: **PDF/DOCX парсинг — pdfplumber (PDF) + mammoth (DOCX).** Лицензии MIT/BSD, ~30 MB pip footprint, image ~250 MB, build 1-2 мин — поддерживает обещание plan §1 «`make up` за 60 секунд». pdfplumber через pdfminer.six даёт reading order по X/Y-координатам; mammoth — whole-document `extract_raw_text` без markdown-обёрток. Layout-aware парсер для production (multi-column через ML, table extraction) — roadmap (LlamaParse / LandingAI ADE) через тот же `extract_text` интерфейс, downstream LLM-extract инвариантен. Q&A 7 переформулирован: senior-defense через protocol-thinking + сознательный trade-off на скорость setup-а.
- **2026-05-09**: **Audit перед БЛОКом 4** — найдено 4 HIGH / 6 MEDIUM / 9 LOW. Исправлены сейчас:
  - H1 docstring `process_email` (статус письма quarantined wins при mixed success/failure — сохраняем сигнал что было review-кандидата);
  - H3 `_PHONE_RE` ужесточён (lookaround `(?<!\w)`/`(?!\w)` + структура `+? country code (3) 3-2-2`) — больше не ловит «2021-2026» как телефон;
  - H4 skill detection через `_skill_pattern` (compiled `(?<![\w-])skill(?![\w-])`, кэш через `lru_cache`) — больше нет «go» в «django/google»;
  - M2 `default_extractor` стал lazy (`get_default_extractor()` через `lru_cache`) — api стартует даже при USE_MOCKS=False+пустом OPENAI_API_KEY (фейл откладывается до первого реального запроса);
  - M3 `retry_if_exception_type` в `_extract_real` сужен до `(APIConnectionError, APITimeoutError, RateLimitError, APIError, TimeoutError)` — не ретраим detereministic-фейлы;
  - M4 `/health` теперь возвращает 503 при недоступном Postgres (Docker корректно помечает unhealthy); Qdrant down → 200+degraded (некритично до БЛОКа 4);
  - M5 streamlit-сервис больше не получает `env_file: .env` со всеми секретами — только `APP_VERSION`+`API_URL` через `environment:`;
  - M6 README.md stub создан с quickstart и ссылками; полноценный README — БЛОК 9.1;
  - L3 `RawEmail.received_at: datetime | None` (некоторые письма без Date-header);
  - L6 FolderSource `received_at` теперь tz-aware UTC;
  - L7 `CorrelationIdMiddleware.dispatch` получил полные type hints;
  - I4 PROGRESS.md «Что дальше» обновлён на БЛОК 4-7.
  Отложено как TODO в коде / roadmap: H2 (race is_terminal↔INSERT — закроется при появлении `POST /sync-mail` в БЛОКе 6 через `SELECT ... FOR UPDATE` или capture-pattern «processing»-status); M1 (mask_pii в логах — БЛОК 9.3); реальный SpacyExtractor — БЛОК 8 / roadmap.

---

## Заметки и TODO

_Сюда — что вспомнил по дороге, чтобы не забыть. Баги, идеи, мелочи._

- Защитные реплики Q&A — в плане раздел «Защита перед тим-лидом» (9 ключевых вопросов + бонус про scale на 1М).
- Перед защитой — отрепетировать demo: `make up && make seed && make demo` за 60 секунд.
- Cost+latency table в README заполняется после первого `make eval` с реальными числами.
- Известные ограничения в README (быть честным): retention configurable, нет auth, не масштабировано >10k резюме, OCR через OpenAI (для PII compliance — локальная VLM в roadmap).
- Полный план реализации — `c:\Develop\ai_rec\hcb-recruiting-agent-plan.md`.
- Критичные fixtures: 5 резюме с complex layouts (двухколоночный sidebar, таблицы) — для проверки edge-cases pdfplumber + 1-2 скана для quarantine `vlm_extract_failed` пути.
