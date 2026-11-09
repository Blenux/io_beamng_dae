#!/bin/bash
# Build io_beamng_dae, publish to the Blender extensions index repo, and push.
#
# Usage: ./scripts/release.sh [index-repo-dir]
# Env:   INDEX_REPO  override index repo path (default: ../blender-extensions)
#        DRY_RUN=1   build + stage only, no commit/push

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INDEX_REPO="${1:-${INDEX_REPO:-$ROOT_DIR/../blender-extensions}}"

"$SCRIPT_DIR/package_zip.sh" >/dev/null
ZIP=$(ls -t "$ROOT_DIR"/io_beamng_dae_v*.zip | head -1)
[ -n "$ZIP" ] || { echo "no zip produced"; exit 1; }

mkdir -p "$INDEX_REPO"
cp "$ZIP" "$INDEX_REPO/"
# Regenerate index.json from the zip manifests (Blender repo_generate)
"$SCRIPT_DIR/create_index.sh" "$INDEX_REPO"

if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "DRY_RUN: staged $(basename "$ZIP") in $INDEX_REPO"; exit 0
fi

git -C "$INDEX_REPO" add -A
git -C "$INDEX_REPO" commit -m "io_beamng_dae $(basename "$ZIP" .zip | sed 's/io_beamng_dae_//')"
git -C "$INDEX_REPO" push || git -C "$INDEX_REPO" push main main
echo "Published $(basename "$ZIP") -> $INDEX_REPO"
