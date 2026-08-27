#!/usr/bin/env bash
# Run the dsh-im-bridge on Ubuntu/Linux.
# Usage: ./scripts/run_bridge.sh [--config config.yaml] [--verbose]
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "[run_bridge] creating venv ..."
  python3 -m venv .venv
  .venv/bin/pip install -e . >/dev/null
fi

exec .venv/bin/python -m dsh_im_bridge "$@"
