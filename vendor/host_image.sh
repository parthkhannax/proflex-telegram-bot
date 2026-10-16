#!/usr/bin/env bash
# Publishes a local image to the public assets repo and prints its raw.githubusercontent URL.
# Buffer's API can only attach images by public URL (no upload endpoint), so we host on GitHub.
#
# Usage: host_image.sh /path/to/local/image.png
# Output (stdout, last line): the public raw URL
set -euo pipefail

SRC="${1:?Usage: host_image.sh <path-to-image>}"
[ -f "$SRC" ] || { echo "ERROR: file not found: $SRC" >&2; exit 1; }

# Use a clone outside iCloud-synced Desktop: git ops on the Desktop copy can fail with
# "Operation timed out" when iCloud evicts files (this dropped the chart from the 2026-10-01 tweet).
REPO_DIR="$HOME/.cache/x-post-assets"
if [ ! -d "$REPO_DIR/.git" ]; then
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone -q "https://${ASSETS_TOKEN:+x-access-token:${ASSETS_TOKEN}@}github.com/parthkhannax/x-post-assets.git" "$REPO_DIR"
fi
git -C "$REPO_DIR" pull -q --ff-only origin main
RAW_BASE="https://raw.githubusercontent.com/parthkhannax/x-post-assets/main/images"

ext="${SRC##*.}"
ext="$(echo "$ext" | tr '[:upper:]' '[:lower:]')"
case "$ext" in jpg|jpeg|png|gif|webp) ;; *) echo "ERROR: unsupported image type .$ext (use jpg/png/gif/webp)" >&2; exit 1;; esac

# Content-hashed name to avoid collisions and enable caching.
hash="$( (command -v shasum >/dev/null && shasum -a 256 "$SRC" || sha256sum "$SRC") | cut -c1-12)"
fname="proflex-x-${hash}.${ext}"
dest="$REPO_DIR/images/$fname"

cp "$SRC" "$dest"
cd "$REPO_DIR"
git config user.name "parthkhannax"
git config user.email "parthbusinessofficialid@gmail.com"
git add "images/$fname"
if git diff --cached --quiet; then
  echo "image already hosted (unchanged)" >&2
else
  git commit -qm "add x-post image $fname"
fi
git push -q origin main

# Wait until the raw URL is actually publicly fetchable (raw CDN can lag a few seconds).
url="$RAW_BASE/$fname"
for i in $(seq 1 15); do
  code="$(curl -s -o /dev/null -w '%{http_code}' -L "$url")"
  [ "$code" = "200" ] && { echo "$url"; exit 0; }
  sleep 2
done
echo "WARN: raw URL not yet 200 after retries, returning anyway: $url" >&2
echo "$url"
