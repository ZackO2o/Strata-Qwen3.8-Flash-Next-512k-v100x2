#!/usr/bin/env bash
# Switch between the 512K and 256K configurations.
#
# The two differ only in --max-context, the YaRN pair, --kv-resident and the conversation cache.
# Everything else (weights, pack, MTP runtime, spec settings) is shared, so switching is a
# config path change plus a restart.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$HERE"
. "$HERE/scripts/config.sh"

case "${1:-}" in
  512k|512K) CONFIG="$ROOT/configs/512k.json"; LABEL="512K  (YaRN 2x, kv-resident, conversation cache)" ;;
  256k|256K) CONFIG="$ROOT/configs/256k.json"; LABEL="256K  (expert cache at full size, no conversation cache)" ;;
  *) echo "usage: $0 {512k|256k}" >&2
     echo "current: $(grep -oE 'configs/[a-z0-9]+\.json' /etc/systemd/system/${SERVICE_NAME}.service 2>/dev/null | head -1)" >&2
     exit 2 ;;
esac

[ -f "$CONFIG" ] || { echo "missing $CONFIG — run ./start.sh once to generate it" >&2; exit 1; }

uint="$(systemctl cat "$SERVICE_NAME" 2>/dev/null || true)"
if [ -n "$uint" ]; then
  CONFIG="$CONFIG" bash "$HERE/scripts/install-service.sh" >/dev/null
  systemctl restart "$SERVICE_NAME"
  printf 'switched to %s\n' "$LABEL"
  printf 'loading'
  for _ in $(seq 1 60); do sleep 5; printf '.'; ss -tln 2>/dev/null | grep -q ":$PORT" && break; done
  printf '\n'
  grep -a ready "$(python3 -c "import json;print(json.load(open('$CONFIG'))['log'])")" 2>/dev/null | tail -1 | sed 's/^/   /' || true
  systemctl is-active "$SERVICE_NAME" | sed 's/^/   service: /'
else
  echo "no systemd unit installed; run with CONFIG=$CONFIG python3 -m serve.server ... manually" >&2
  exit 1
fi
