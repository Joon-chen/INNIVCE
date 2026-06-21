# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ARG LARK_CLI_VERSION=1.0.53

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        nodejs \
        npm \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.npm \
    npm install -g --omit=dev --no-audit --no-fund "@larksuite/cli@${LARK_CLI_VERSION}" \
    && lark-cli --version

COPY pyproject.toml requirements.lock ./

# Install project dependencies before copying application code so ordinary
# source edits do not invalidate the slow dependency layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p app \
    && touch app/__init__.py \
    && pip install --retries 10 --timeout 120 --index-url "$PIP_INDEX_URL" -c requirements.lock -e .

COPY app ./app

COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
