#!/usr/bin/env bash
# Push MCmodAgent updates to remote receiver

set -euo pipefail
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
else
    PYTHON="$(command -v python)"
fi

cd "$ROOT_DIR"
exec "$PYTHON" "$DEPLOY_DIR/cli.py" deploy "$@"
