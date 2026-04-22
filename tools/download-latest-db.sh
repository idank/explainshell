#!/bin/bash
# Download a DB asset from the db-latest GitHub release.
#
# Usage:
#   download-latest-db.sh [--asset <name>] [<output-path>]
#
# With --asset, downloads that specific asset and verifies its sha256
# against the release metadata. Without it, picks the newest
# explainshell-*.db.zst by created_at (for manual `make
# download-latest-db` runs).
#
# Build pipelines (do-deploy.yml, `make deploy-local`) always pass
# --asset so the image is pinned to what CI captured, not "whatever is
# newest at the moment the remote builder runs."

set -euo pipefail

ASSET=""
ASSET_GIVEN=0
OUTPUT="explainshell.db"
while [ $# -gt 0 ]; do
    case "$1" in
        --asset)
            ASSET="$2"; ASSET_GIVEN=1; shift 2 ;;
        --asset=*)
            ASSET="${1#--asset=}"; ASSET_GIVEN=1; shift ;;
        *)
            OUTPUT="$1"; shift ;;
    esac
done

# Explicit empty --asset is a mistake (the Dockerfile/Makefile/CI path
# should pass a real name). Fail loudly instead of falling back to the
# "newest" path, which would silently defeat the pinning contract.
if [ "$ASSET_GIVEN" -eq 1 ] && [ -z "$ASSET" ]; then
    echo "--asset was given but empty; pass a name or omit the flag" >&2
    exit 1
fi

REPO="idank/explainshell"
RELEASE="db-latest"
API_URL="https://api.github.com/repos/$REPO/releases/tags/$RELEASE"

RELEASE_JSON=$(wget -qO- "$API_URL")

if [ -n "$ASSET" ]; then
    EXPECTED_SHA=$(echo "$RELEASE_JSON" \
        | jq -r --arg n "$ASSET" '.assets[] | select(.name == $n) | .digest' \
        | sed 's/^sha256://')
    if [ -z "$EXPECTED_SHA" ]; then
        echo "asset $ASSET not found in release $RELEASE" >&2
        exit 1
    fi
else
    ASSET=$(echo "$RELEASE_JSON" \
        | jq -r '[.assets[] | select(.name | test("^explainshell-.*\\.db\\.zst$"))] | sort_by(.created_at) | last | .name')
    EXPECTED_SHA=$(echo "$RELEASE_JSON" \
        | jq -r '[.assets[] | select(.name | test("^explainshell-.*\\.db\\.zst$"))] | sort_by(.created_at) | last | .digest' \
        | sed 's/^sha256://')
    if [ -z "$ASSET" ] || [ "$ASSET" = "null" ]; then
        echo "No db asset found in release $RELEASE" >&2
        exit 1
    fi
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
