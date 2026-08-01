#!/usr/bin/env bash
# Bring up the prod Docker image locally and verify the two botshed
# request paths through real Caddy + real Flask:
#   - happy path:  /explain?cmd=ls passes through to Flask.
#   - bad path:    a 4-token shed-shaped query is intercepted with a
#                  canned response.
#
# The two paths are distinguished by the Cache-Control max-age value:
# Flask emits max-age=300 (views.py:_EXPLAIN_CACHE_CONTROL), botshed
# emits max-age=86400 (module.go ServeHTTP).
#
# Usage: prod/integration-test.sh [image-tag]
set -uo pipefail

IMAGE="${1:-explainshell-prod:test}"
PORT="${PORT:-18080}"
NAME="explainshell-it-$$"

cleanup() {
  echo "==> container logs"
  docker logs "$NAME" 2>&1 | sed 's/^/    /' || true
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> running $IMAGE on :$PORT"
docker run -d --name "$NAME" \
  -p "$PORT:8080" \
  "$IMAGE" >/dev/null

# Wait for /health: proves caddy + flask are wired up.
echo "==> waiting for /health"
deadline=$(( $(date +%s) + 60 ))
while true; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/health" || echo 000)
  [ "$code" = "200" ] && break
  if [ "$(date +%s)" -ge $deadline ]; then
    echo "FAIL: /health did not return 200 within 60s"; exit 1
  fi
  sleep 1
done

# Wait for botshed canned-loader: it runs in a goroutine after Provision
# and only completes once it has fetched all 8 seed cmds. Until then
# bad-path requests would silently pass through — masking a real failure.
echo "==> waiting for botshed loaders"
deadline=$(( $(date +%s) + 60 ))
while true; do
  if docker logs "$NAME" 2>&1 | grep -q "botshed: loaded canned responses"; then
    break
  fi
  if [ "$(date +%s)" -ge $deadline ]; then
    echo "FAIL: botshed canned-loader did not complete within 60s"; exit 1
  fi
  sleep 1
done

fail=0

echo "==> happy path: /explain?cmd=ls"
hdrs=$(curl -sS -D- -o /dev/null "http://localhost:$PORT/explain?cmd=ls")
status=$(echo "$hdrs" | head -1 | awk '{print $2}')
if [ "$status" != "200" ]; then
  echo "    FAIL: status=$status"; fail=1
elif ! echo "$hdrs" | grep -i '^cache-control' | grep -q 'max-age=300'; then
  echo "    FAIL: expected Flask cache header (max-age=300)"
  echo "$hdrs" | sed 's/^/      /'
  fail=1
else
  echo "    ok (Flask render)"
fi

echo "==> bad path: shed-shaped /explain?cmd=..."
bot_cmd='rc.byron.1 rc.1plan9 cd.1posix ls.1'
hdrs=$(curl -sS -D- -o /dev/null --data-urlencode "cmd=$bot_cmd" -G "http://localhost:$PORT/explain")
status=$(echo "$hdrs" | head -1 | awk '{print $2}')
if [ "$status" != "200" ]; then
  echo "    FAIL: status=$status"; fail=1
elif ! echo "$hdrs" | grep -i '^cache-control' | grep -q 'max-age=86400'; then
  echo "    FAIL: expected botshed cache header (max-age=86400)"
  echo "$hdrs" | sed 's/^/      /'
  fail=1
else
  echo "    ok (botshed shed)"
fi

if [ $fail -eq 0 ]; then
  echo "==> all integration probes passed"
else
  echo "==> FAILED"
fi
exit $fail
