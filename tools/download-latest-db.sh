#!/bin/bash
set -euo pipefail

OUTPUT="${1:-explainshell.db}"
REPO="idank/explainshell"
RELEASE="db-latest"
API_URL="https://api.github.com/repos/$REPO/releases/tags/$RELEASE"

RELEASE_JSON=$(wget -qO- "$API_URL")
ASSET=$(echo "$RELEASE_JSON" \
    | jq -r '[.assets[] | select(.name | test("^explainshell-.*\\.db\\.zst$"))] | sort_by(.created_at) | last | .name')
EXPECTED_SHA=$(echo "$RELEASE_JSON" \
    | jq -r '[.assets[] | select(.name | test("^explainshell-.*\\.db\\.zst$"))] | sort_by(.created_at) | last | .digest' \
    | sed 's/^sha256://')

if [ -z "$ASSET" ] || [ "$ASSET" = "null" ]; then
    echo "No db asset found in release $RELEASE" >&2
    exit 1
fi

echo "Downloading $ASSET..."
wget -q -O "$ASSET" "https://github.com/$REPO/releases/download/$RELEASE/$ASSET"

ACTUAL_SHA=$(sha256sum "$ASSET" | awk '{print $1}')
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
    echo "SHA256 mismatch: expected $EXPECTED_SHA, got $ACTUAL_SHA" >&2
    rm -f "$ASSET"
    exit 1
fi
echo "$ACTUAL_SHA" > "$OUTPUT.sha256"
echo "SHA256 verified: $ACTUAL_SHA"

zstd -d -f "$ASSET" -o "$OUTPUT"
rm -f "$ASSET"
echo "Wrote $OUTPUT"
