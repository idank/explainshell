#!/bin/bash
set -euo pipefail

DB="${1:?Usage: $0 <db-file>}"
REPO="idank/explainshell"
RELEASE="db-latest"
DATE=$(date -u +%Y-%m-%d-%H%M%S)
ASSET="explainshell-${DATE}.db.zst"

test -f "$DB" || { echo "$DB not found"; exit 1; }

# TODO: consider running `sqlite3 "$DB" VACUUM` after the digest check but
# before compressing for upload, so the uploaded DB has no freelist bloat.

# --- Compress ---
zstd -1 -f "$DB" -o "$ASSET"

# --- Check if upload is needed by comparing to the newest existing asset ---
newest_asset=$(gh api "repos/$REPO/releases/tags/$RELEASE" \
    --jq '[.assets[] | select(.name | test("^explainshell-.*\\.db\\.zst$"))] | sort_by(.created_at) | last | .name' 2>/dev/null || true)

if [ -n "$newest_asset" ]; then
    remote_sha=$(gh api "repos/$REPO/releases/tags/$RELEASE" \
        --jq ".assets[] | select(.name == \"$newest_asset\") | .digest" 2>/dev/null \
        | sed 's/^sha256://')
    local_sha=$(sha256sum "$ASSET" | awk '{print $1}')

    if [ "$local_sha" = "$remote_sha" ]; then
        echo "Local DB digest ($local_sha) matches latest release asset ($newest_asset). Nothing to upload."
        rm -f "$ASSET"
        exit 0
    fi

    echo "Local digest:  $local_sha"
    echo "Latest asset:  $newest_asset (digest: ${remote_sha:-<none>})"
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

rm -f "$ASSET"
echo "Uploaded $ASSET to release $RELEASE."
echo
echo "Next steps:"
echo "  1. Deploy (push, or 'make deploy-local') to bake the new DB into the image."
echo "  2. After the deploy lands, purge Cloudflare so the edge stops serving the"
echo "     old DB's responses."
