#!/bin/sh
set -e

DB_PATH="${DB_PATH:-/data/karen_finder.db}"
mkdir -p "$(dirname "$DB_PATH")"

if [ -n "$R2_ACCESS_KEY_ID" ] && [ -n "$R2_BUCKET" ]; then
  echo "[entrypoint] litestream restore (if replica exists)"
  litestream restore -if-replica-exists -config /app/litestream.yml "$DB_PATH" || true

  echo "[entrypoint] running alembic migrations"
  alembic -c /app/alembic.ini upgrade head

  echo "[entrypoint] starting litestream replicate in background"
  litestream replicate -config /app/litestream.yml &
  LITESTREAM_PID=$!
  trap "kill $LITESTREAM_PID 2>/dev/null || true" TERM INT
else
  echo "[entrypoint] R2 not configured — running without Litestream (local/dev)"
  alembic -c /app/alembic.ini upgrade head
fi

echo "[entrypoint] exec python -m karen_finder"
exec python -m karen_finder
