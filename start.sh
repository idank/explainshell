#!/bin/bash
set -e

# Caddy fronts gunicorn on :8080 and buffers responses so slow-read clients
# cannot park gthread workers in wsgi.write(). Gunicorn binds localhost only.
# Access logging lives in caddy (structured json with real client_ip from the
# CF-Connecting-IP header); gunicorn only emits warnings/errors to stderr.
gunicorn -w 2 --threads 4 -b [::1]:8081 \
  "explainshell.web:create_app()" &
GUNI_PID=$!

caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
CADDY_PID=$!

trap 'kill -TERM $GUNI_PID $CADDY_PID 2>/dev/null || true; wait' TERM INT
wait -n
kill -TERM $GUNI_PID $CADDY_PID 2>/dev/null || true
wait
