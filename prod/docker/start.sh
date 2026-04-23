#!/bin/bash
set -e

# Caddy fronts gunicorn on :8080 and buffers responses so slow-read clients
# cannot park gthread workers in wsgi.write(). Gunicorn binds localhost only.
# Access logging lives in caddy (structured json with real client_ip from the
# CF-Connecting-IP header); gunicorn only emits warnings/errors to stderr.
# --timeout 15 bounds worker liveness so a wedged request recycles in 15s
# instead of the 30s default; during an abuse storm this lets the pool
# recover rather than accumulating a queue we can't drain. Gotcha: on
# gthread this kills sibling threads in the same worker on expiry —
# acceptable because healthy /explain is well under a second, so a 15s
# stall is already a failed request.
gunicorn -w 2 --threads 4 \
  --timeout 15 --graceful-timeout 5 \
  --max-requests 1000 --max-requests-jitter 200 --preload \
  -b [::1]:8081 \
  "explainshell.web:create_app()" &
GUNI_PID=$!

caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
CADDY_PID=$!

trap 'kill -TERM $GUNI_PID $CADDY_PID 2>/dev/null || true; wait' TERM INT
wait -n
kill -TERM $GUNI_PID $CADDY_PID 2>/dev/null || true
wait
