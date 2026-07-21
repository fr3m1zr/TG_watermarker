#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ".env" ]; then
  echo "Missing .env file. Create it from .env.example first."
  echo "Example: cp .env.example .env"
  exit 1
fi

echo "[1/4] Stop old containers and remove orphans..."
docker compose down --remove-orphans || true

echo "[2/4] Build latest image..."
docker compose build --pull

echo "[3/4] Start bot services..."
docker compose up -d --force-recreate

echo "[4/4] Clean unused dangling images..."
docker image prune -f

echo "Done. Use 'docker compose logs -f' to watch runtime logs."
