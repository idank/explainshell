#!/usr/bin/env bash
# Post-deploy smoke test for the production explainshell service.
#
# Discovers supported distros from /health, then probes the root page,
# the /explain?cmd= query path, and the /explain/<program> manpage
# path — plus per-distro variants of both — and fails if any response
# is non-200 or contains "Internal Server Error" or "missing man page".
#
# Usage: BASE=https://explainshell.com prod/smoke-test.sh
set -uo pipefail

BASE="${BASE:-https://explainshell.com}"

echo "==> smoke test against $BASE"
echo "==> fetching /health to discover supported distros"
pairs=$(curl -fsS --max-time 10 "$BASE/health" \
        | jq -r '.distros[] | "\(.distro)/\(.release)"') || {
  echo "FAIL: /health unreachable or malformed"; exit 1;
}
if [ -z "$pairs" ]; then
  echo "FAIL: /health reported no distros"; exit 1
fi
pair_count=$(printf '%s\n' "$pairs" | wc -l | tr -d ' ')
echo "    found $pair_count distro/release pair(s):"
printf '      %s\n' $pairs

urls=("$BASE/" "$BASE/explain?cmd=ls" "$BASE/explain/ls")
while IFS= read -r p; do
  [ -z "$p" ] && continue
  urls+=("$BASE/explain/$p/ls" "$BASE/explain/$p?cmd=ls")
done <<< "$pairs"

echo "==> probing ${#urls[@]} url(s)"
fail=0
for u in "${urls[@]}"; do
  echo "    testing $u"
  code=$(curl -sS -o /tmp/body -w '%{http_code}' --max-time 15 "$u" || echo "000")
  msg=""
  if [ "$code" != "200" ]; then
    msg="status=$code"
  elif grep -q "Internal Server Error" /tmp/body; then
    msg="body contains 'Internal Server Error'"
  elif grep -q "missing man page" /tmp/body; then
    msg="body contains 'missing man page'"
  fi
  if [ -n "$msg" ]; then
    echo "      FAIL -- $msg"
    fail=1
  else
    echo "      ok (status=$code, $(wc -c </tmp/body | tr -d ' ') bytes)"
  fi
done

if [ $fail -eq 0 ]; then
  echo "==> all ${#urls[@]} probes passed"
else
  echo "==> FAILED"
fi
exit $fail
