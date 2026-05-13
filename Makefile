.PHONY: up down seed sync demo eval test test-int lint clean

# Build + lift docker stack (api / streamlit / postgres / qdrant).
up:
	docker compose up -d --build
	@echo "API:    http://localhost:8000/docs"
	@echo "UI:     http://localhost:8501"
	@echo "Health: curl http://localhost:8000/health"

down:
	docker compose down

# Jobs auto-seed on api startup via lifespan (src/workers/job_seeder.py).
# Резюме: `make demo` сам копирует fixtures из tests/fixtures/golden/resumes/
# в ./storage/inbox/ и триггерит ingestion. Можно положить свои файлы вручную.
seed:
	@echo "Jobs автосидятся при старте api из ./jobs/*.json."
	@echo "Резюме: 'make demo' (auto-copy fixtures + sync) или вручную в ./storage/inbox/."

# Триггер ingestion вручную (если положили свои файлы в ./storage/inbox/).
sync:
	curl -sX POST http://localhost:8000/sync-mail | python -m json.tool

# One-command setup: copy fixtures → POST /sync-mail → final state + showcase.
# Идемпотентен: повторный запуск пропускает ingestion (candidates уже есть).
demo:
	python scripts/demo.py

# Полный eval по golden датасету (dense + tfidf + llm + hybrid). 30-40 мин под Tier 1.
eval:
	docker compose exec api python -m src.eval.runner --methods dense tfidf llm hybrid

# Unit-тесты (моки, без поднятого стека).
test:
	pytest tests/unit -v

# Integration smoke (требует `make up` + кандидатов в БД).
test-int:
	pytest tests -v -m integration

# Lint + types на src/.
lint:
	ruff check src/ tests/
	ruff format --check src/ tests/
	mypy src

# Полная очистка: down + volumes + образы (postgres + qdrant + models_cache).
clean:
	docker compose down -v
