#!/bin/sh
set -e

exec gunicorn -w 2 --threads 4 -b 0.0.0.0:8080 \
  --access-logfile - \
  --access-logformat '%(t)s "%(r)s" %(s)s %(b)s %(D)sμs' \
  "explainshell.web:create_app()"
