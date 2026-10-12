#!/usr/bin/env bash
# Regenerate the README screenshots in docs/screenshots/.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$root/docs/screenshots"
CAPTURE_SCREENSHOTS=1 SHOTS_DIR="$root/docs/screenshots" \
  ORIGIN_REPO="${ORIGIN_REPO:-/srv/git/todo-api.git}" \
  exec "$root/scripts/e2e.sh" screenshots.spec.ts spec.spec.ts "$@"
