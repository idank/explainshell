#!/bin/bash
set -euo pipefail

DB="${1:?Usage: $0 <db-file>}"
REPO="idank/explainshell"
RELEASE="db-latest"
ASSET="explainshell.db.zst"
CDN_URL="https://github.com/$REPO/releases/download/$RELEASE/$ASSET"

test -f "$DB" || { echo "$DB not found"; exit 1; }

# --- Compress and check if upload is needed ---
zstd -1 -f "$DB" -o "$ASSET"
local_sha=$(sha256sum "$ASSET" | awk '{print $1}')
remote_sha=$(gh api "repos/$REPO/releases/tags/$RELEASE" \
    --jq ".assets[] | select(.name == \"$ASSET\") | .digest" 2>/dev/null \
    | sed 's/^sha256://')

if [ "$local_sha" = "$remote_sha" ]; then
    echo "Local DB digest ($local_sha) matches release. Nothing to upload."
    rm -f "$ASSET"
    exit 0
fi

echo "Local digest:  $local_sha"
echo "Release digest: ${remote_sha:-<none>}"

# --- Archive existing asset ---
asset_id=$(gh api "repos/$REPO/releases/tags/$RELEASE" --jq ".assets[] | select(.name == \"$ASSET\") | .id")
if [ -n "$asset_id" ]; then
    upload_date=$(gh api "repos/$REPO/releases/tags/$RELEASE" \
        --jq ".assets[] | select(.name == \"$ASSET\") | .updated_at" \
        | tr -d 'Z' | tr 'T:' '-')
    archive_name="explainshell-${upload_date}.db.zst"
    echo "Renaming existing asset to $archive_name..."
    gh api "repos/$REPO/releases/assets/$asset_id" -X PATCH -f name="$archive_name" --silent
fi

# --- Upload ---
upload_url=$(gh api "repos/$REPO/releases/tags/$RELEASE" --jq '.upload_url' | sed 's/{.*}//')
token=$(gh auth token)

echo "Uploading $ASSET..."
curl --progress-bar \
    -H "Authorization: token $token" \
    -H "Content-Type: application/octet-stream" \
    --data-binary @"$ASSET" \
    "${upload_url}?name=$ASSET" | cat

# --- Wait for CDN ---
expected_size=$(wc -c < "$ASSET")
rm -f "$ASSET"

echo "Waiting for CDN to serve the new file ($expected_size bytes)..."
while true; do
    cdn_size=$(curl -sI -L "$CDN_URL" | grep -i content-length | tail -1 | tr -d '[:space:]' | cut -d: -f2)
    if [ "$cdn_size" = "$expected_size" ]; then
        echo "CDN updated."
        break
    fi
    echo "  CDN still serving $cdn_size bytes, expected $expected_size. Retrying in 10s..."
    sleep 10
done
