#!/usr/bin/env bash
# Install dsh-im-bridge as a systemd service on Ubuntu.
# Usage:  sudo ./scripts/install-systemd.sh /abs/path/to/dsh-im-bridge
set -euo pipefail

ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVICE="dsh-im-bridge.service"
UNIT_SRC="$ROOT/scripts/systemd/$SERVICE"

if [ ! -f "$UNIT_SRC" ]; then
  echo "unit file not found: $UNIT_SRC" >&2
  exit 1
fi

if [ ! -d "$ROOT/.venv" ]; then
  echo "[install] creating venv ..."
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -e "$ROOT" >/dev/null
fi

if [ ! -f "$ROOT/config.yaml" ]; then
  cp "$ROOT/config.example.yaml" "$ROOT/config.yaml"
  echo "[install] created config.yaml from example — edit it, then re-run."
fi

# Generate a unit with the real absolute paths baked in.
sed -e "s|/path/to/dsh-im-bridge|$ROOT|g" "$UNIT_SRC" > "/etc/systemd/system/$SERVICE"

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
systemctl --no-pager status "$SERVICE"

echo
echo "[install] done. Commands:"
echo "  sudo systemctl status $SERVICE"
echo "  sudo journalctl -u $SERVICE -f"
echo "  sudo systemctl stop $SERVICE"
