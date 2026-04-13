#!/bin/sh
set -e

exec gunicorn -w 4 -b 0.0.0.0:8080 "explainshell.web:create_app()"
