#!/bin/bash
# Serve the league site with gunicorn. Settings come from .env (see .env.example).
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${VENV:-venv}"
PORT="${PORT:-5000}"
HOST="${HOST:-127.0.0.1}"
exec "$VENV/bin/gunicorn" --bind "$HOST:$PORT" --workers 2 --timeout 180 web.wsgi:app
