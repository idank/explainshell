#!/bin/sh
set -e

exec gunicorn -w 2 --threads 4 -b 0.0.0.0:8080 \
  --access-logfile - \
  --access-logformat '%(t)s %({CF-Connecting-IP}i)s %({X-Forwarded-For}i)s "%(r)s" %(s)s %(b)s %(D)sμs "%(a)s"' \
  "explainshell.web:create_app()"
