#!/bin/sh
set -e

DB_PATH="${DB_PATH:-/data/explainshell.db}"
DB_URL="${DB_URL:-}"
DB_VERSION="${DB_VERSION:-}"
VERSION_FILE="$(dirname "$DB_PATH")/.db_version"

if [ -n "$DB_URL" ] && [ -n "$DB_VERSION" ]; then
    current_version=""
    if [ -f "$VERSION_FILE" ]; then
        current_version=$(cat "$VERSION_FILE")
    fi

    if [ "$current_version" != "$DB_VERSION" ]; then
        echo "DB version changed ($current_version -> $DB_VERSION), downloading..."
        wget -q -O "$DB_PATH.new" "$DB_URL"
        # Remove stale WAL/SHM files before replacing the DB — an old WAL
        # replayed against a new DB can cause corruption.
        rm -f "$DB_PATH-wal" "$DB_PATH-shm"
        mv "$DB_PATH.new" "$DB_PATH"
        echo "$DB_VERSION" > "$VERSION_FILE"
        echo "Database updated to version $DB_VERSION."
    else
        echo "Database version $DB_VERSION is current, skipping download."
    fi
fi

exec gunicorn -w 4 -b 0.0.0.0:8080 "explainshell.web:create_app()"
