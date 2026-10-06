#!/usr/bin/env bash
# Stop the server and free both cards.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/scripts/config.sh"

used() { nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | sed 's/^/   /'; }

echo "before:"; used

if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"; then
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  echo "   stopped $SERVICE_NAME (still enabled — it starts again on boot)"
else
  pkill -9 -f serve.server  2>/dev/null || true
  pkill -9 -f strata-vision 2>/dev/null || true
  pkill -9 -f 'build/strata --serve' 2>/dev/null || true
  echo "   killed the processes directly"
fi

sleep 8
echo "after:"; used
