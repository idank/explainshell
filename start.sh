#!/bin/sh
set -e

DB_PATH="${DB_PATH:-/data/explainshell.db}"
DB_URL="${DB_URL:-}"

if [ -n "$DB_URL" ] && [ ! -f "$DB_PATH" ]; then
    echo "Downloading database from $DB_URL..."
    wget -q -O "$DB_PATH.new" "$DB_URL"
    mv "$DB_PATH.new" "$DB_PATH"
    echo "Database downloaded."
fi

exec gunicorn -w 4 -b 0.0.0.0:8080 "explainshell.web:create_app()"
