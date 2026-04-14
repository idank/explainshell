#!/bin/sh
set -e

HOST_IP="${HOST_IP:-0.0.0.0}"
PORT="${PORT:-5000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
GUNICORN_THREADS="${GUNICORN_THREADS:-4}"
GUNICORN_ACCESS_LOG="${GUNICORN_ACCESS_LOG:-0}"
GUNICORN_ACCESS_LOG_FILE="${GUNICORN_ACCESS_LOG_FILE:--}"
GUNICORN_ACCESS_LOG_FORMAT="${GUNICORN_ACCESS_LOG_FORMAT:-%(t)s \"%(r)s\" %(s)s %(b)s %(D)sμs}"

export DB_PATH="${DB_PATH:-/opt/webapp/explainshell.db}"
export LOG_LEVEL="${LOG_LEVEL:-WARN}"

set -- gunicorn -w "$GUNICORN_WORKERS" --threads "$GUNICORN_THREADS" -b "$HOST_IP:$PORT"

if [ "$GUNICORN_ACCESS_LOG" = "1" ] || [ "$GUNICORN_ACCESS_LOG" = "true" ]; then
  set -- "$@" \
    --access-logfile "$GUNICORN_ACCESS_LOG_FILE" \
    --access-logformat "$GUNICORN_ACCESS_LOG_FORMAT"
fi

exec "$@" "explainshell.web:create_app()"
