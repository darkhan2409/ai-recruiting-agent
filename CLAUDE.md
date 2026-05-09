# Правила написания кода — HCB Recruiting Agent

## Workflow — пошаговое выполнение

- Переход к следующей задаче/шагу — **только по явной команде пользователя**
- После завершения шага — отчитаться о результате и ждать команды на следующий
- Утверждение плана ≠ команда начать реализацию
- Auto mode не отменяет это правило для крупных шагов реализации
- "Делай шаг за шагом" значит: одна логическая единица → отчёт → пауза → команда → следующая единица

## Документация — всегда первым делом

- Перед использованием любой библиотеки — найти актуальную документацию через MCP Context7
- Перед изменением кода — получить свежую документацию, не полагаться на память
- Вносить изменения только при 100% уверенности. Есть сомнения — сначала читай документацию

## Документация проекта

- `README.md` — единственный live-файл документации. Quickstart, архитектурная диаграмма (mermaid), стек, известные ограничения, demo-сценарий
- `ARCHITECTURE.md` — опционально, если в README становится тесно. Подробные диаграммы и trade-offs
- История проекта — через `git log` с осмысленными commit messages, не через md-журналы
- `notebooks/methods_comparison.ipynb` — research-часть, сравнение трёх подходов матчинга
- `hcb-recruiting-agent-plan.md` — implementation plan с чек-боксами по задачам
- `PROGRESS.md` — рабочий блокнот: "Сейчас работаю", "Что готово", "Решения принятые в процессе"
- `DATA_POLICY.md` — обязательства по обработке PII (mask_pii, retention, DELETE)

## Трекинг прогресса

После закрытия каждой подзадачи — в том же коммите:
1. Поставить `[x]` в `hcb-recruiting-agent-plan.md` напротив задачи.
2. Обновить `PROGRESS.md`: "Сейчас работаю", "Что готово", при необходимости "Решения принятые в процессе" (формат: `<дата>: <решение> — <причина>`).
3. Перед началом новой задачи — прочитать `PROGRESS.md` для контекста.

Формат коммита: `<тип>(<область>): <что сделано>`. Примеры:
- `feat(ingestion): IMAP source с idempotency через processed_emails`
- `feat(parsing): text extract для PDF/DOCX/TXT`
- `test(matching): unit-тест RRF fusion`

## Структура проекта

Файлы по **доменам**, не по слоям. Не плодить искусственные `models/ schemas/ repositories/ services/`.

```
src/
  ingestion/      # IMAP poller, FolderSource fallback
  parsing/        # text extraction (PDF/DOCX) + LLM extraction
  matching/       # 3 retrievers, RRF, LLM judge, pipeline
  api/            # FastAPI routes
  ui/             # streamlit_app.py
  db.py           # SQLAlchemy модели + session factory
  schemas.py      # Pydantic схемы (Resume, Job, MatchResult)
  config.py       # pydantic-settings
  main.py         # FastAPI entrypoint + APScheduler setup
```

## Код

- 1 функция = 1 действие, максимум 30–40 строк
- Названия говорящие: `extract_resume_from_pdf`, не `proc_data`, не `handle_v2`
- Линейный код лучше умного
- Не создавать классы там, где хватит функции
- Порядок в файле: импорты → константы → модели/схемы → функции → точка входа
- Не дробить на много файлов то, что помещается в 2

## Python

- Type hints — **обязательно** для всех публичных функций
- `mypy --strict` проходит на `schemas.py`, `parsing/`, `matching/`
- Для I/O (HTTP, файлы, БД, IMAP) — всегда `async/await`
- Исключение: если async-обёртка тянет лишнюю гимнастику (sklearn, sync-only Qdrant методы) — допустим sync внутри узла
- Не смешивать sync и async без явной причины
- Pydantic для всего что приходит извне (LLM output, API request, конфиг)

## Докстринги и комментарии

- Публичные функции — с докстрингами на русском, **Google-style** (Args / Returns / Raises)
- Комментировать **почему**, а не **что**
- Не комментировать очевидное

## Ошибки

- `try/except` на верхнем уровне pipeline (worker tick, API endpoint) — достаточно
- Одно битое резюме не должно ронять worker — логируем + skip + продолжаем
- В FastAPI — глобальный exception handler для unhandled errors
- Кастомные исключения — **только** для пользовательских сценариев (`JobNotFoundError`, `InvalidResumeError`). Не плодить иерархию `AppError → IngestionError → IMAPError → ...`
- При ошибке: залогировать с контекстом (resume_id, job_id, message_id) + вернуть осмысленный fallback
- Retry — только там, где реальная нужда: IMAP connection, OpenAI rate limits. Через `tenacity` с exponential backoff. Не на каждый чих

## Логирование

- Стандартный `logging` (или `loguru`) с контекстом
- Никаких `print()` в production-коде
- Никогда не логировать содержимое `.env`, ключи API, полные тексты резюме, ФИО, email кандидатов, телефоны
- PII в логах — маскировать (`иван***@gmail.com`, последние 4 цифры телефона)

## Секреты

- Никогда не хардкодить ключи — только через `pydantic-settings` + `os.getenv`
- Всегда создавать `.env.example` с фейковыми значениями
- `.env` в `.gitignore`

## Внешние API

- OpenAI и IMAP — сначала мок (`USE_MOCKS = True` в config) для разработки
- Реальный вызов — только когда логика проверена
- Мок возвращает реалистичный payload (валидная Pydantic-схема `Resume` для extractor, не "hello world")
- Это экономит и квоты, и время на отладке

## Линтеры

- **ruff** — настраивается с первого коммита (format + check)
- **pre-commit hook** с ruff — обязателен
- **mypy** — для критичных модулей (`schemas.py`, `parsing/`, `matching/`), не для всего подряд

## Тесты — только где реально нужно

**Главный принцип:** тесты должны защищать от реальных регрессий и быть запускаемыми за разумное время. Не гнаться за coverage числом.

**Делаем тесты для:**
- Парсинг PDF/DOCX → text (несколько fixtures, проверяем что не падает на edge cases)
- RRF fusion (чистая функция, легко протестировать, критична для матчинга)
- Output validation LLM-judge (anti-hallucination проверка — критична)
- Один integration-тест на full pipeline `email → recommendation` (smoke check что всё связано)

**НЕ делаем тесты для:**
- Тривиальных getter'ов и Pydantic схем (Pydantic сам валидирует)
- Streamlit UI (визуальная проверка достаточна)
- Реальных вызовов OpenAI и IMAP (моки покрывают, реальные вызовы — медленные и flaky)
- 100% coverage ради числа в README

**Целевое покрытие:** 50-60% на бизнес-логике в `parsing/` и `matching/`. Остальное — по факту необходимости.

## LLM-специфика

### Промпты
- Промпты живут в `src/parsing/prompts.py` и `src/matching/prompts.py` как Python-константы или Jinja2 шаблоны
- Для тестового Langfuse Prompt Management не нужен — это +сервис +сложность ради функции, которая в MVP не оправдана
- Промпт длиннее 2000 токенов — повод задуматься, возможно часть должна быть few-shot example, а не инструкцией
- Подстановка переменных — через `.format()` или Jinja2, без жёстких f-string в коде

### Гиперпараметры
- Параметры (`temperature`, `model`) — рядом с промптом в том же модуле, как константа
- Для evals и research notebook — `temperature=0` (детерминизм)
- Для production LLM-judge — `temperature=0.0` или `0.1`, не выше

### Структурные выходы
- Все LLM-вызовы которые возвращают данные — через Pydantic structured output (`response_format=...`)
- Никакого `json.loads(response.content)` с try/except и парсингом руками
- Если модель не поддерживает structured output — `instructor` библиотека или Pydantic + retry

### Cost-контроль (минимум)
- В development — мок (`USE_MOCKS=True`)
- Кэш эмбеддингов в Postgres по `hash(text)` — не пересчитывать если уже считали
- Кэш LLM-judge результатов по `hash(resume_id, job_id)` с TTL 24h — не дёргать LLM повторно

## Не делать без реальной нужды

- Абстрактные базовые классы (ABC)
- Protocols ради одной реализации (Protocol оправдан только когда реально две: `ResumeExtractor` = LLM + spaCy fallback; `IngestionSource` = IMAP + FolderSource)
- Repository pattern с отдельным слоем
- Dependency injection ради DI (FastAPI `Depends` — нативный механизм, использовать где помогает тестам)
- Паттерны ради паттернов (фабрики, стратегии, декораторы)
- Оптимизации до подтверждённого bottleneck
- Кастомные исключения (кроме пользовательских сценариев)
- Кастомные retry-механизмы (только `tenacity` для внешних API)
- Собственный HTTP-клиент вместо httpx
- Собственный векторный поиск вместо Qdrant
