# syntax=docker/dockerfile:1.7
# HCB Recruiting Agent — multi-stage образ для api и streamlit.
# Stage 1: установка зависимостей в venv. Stage 2: runtime non-root.

FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# curl для healthcheck-ов
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash hcb

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=hcb:hcb pyproject.toml ./
COPY --chown=hcb:hcb alembic.ini ./
COPY --chown=hcb:hcb alembic ./alembic
COPY --chown=hcb:hcb src ./src

# Точки монтирования volume-ов: создать заранее и chown,
# чтобы named volume инициализировался с правильным владельцем.
# /home/hcb/.cache — резерв под кэши Python-зависимостей.
RUN mkdir -p /app/storage/resumes /app/storage/inbox /home/hcb/.cache \
    && chown -R hcb:hcb /app/storage /home/hcb/.cache

USER hcb

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["sh", "-c", "alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port 8000"]
