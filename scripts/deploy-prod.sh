#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR=${PROJECT_DIR:-/opt/digital-advisor}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.prod.yml}

cd "$PROJECT_DIR"

if [ ! -f .env ]; then
  echo "Missing .env. Copy .env.prod.example to .env and fill production secrets first." >&2
  exit 1
fi

git pull --ff-only

docker compose -f "$COMPOSE_FILE" up -d --build

docker compose -f "$COMPOSE_FILE" ps
