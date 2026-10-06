#!/usr/bin/env bash
# Render the systemd unit from the template and install it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
. "$HERE/config.sh"

CONFIG="${CONFIG:-$ROOT/configs/512k.json}"
UNIT_SRC="$HERE/systemd/strata-v100.service.in"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"

[ -f "$CONFIG" ] || { echo "no config at $CONFIG — run write-configs.sh first" >&2; exit 1; }
[ "$(id -u)" = "0" ] || { echo "installing a system unit needs root" >&2; exit 1; }

LOG="$(python3 -c "import json,sys;print(json.load(open('$CONFIG'))['log'])")"
mkdir -p "$(dirname "$LOG")"

sed -e "s#__STRATA_DIR__#$STRATA_DIR#g" \
    -e "s#__CUDA_HOME__#$CUDA_HOME#g" \
    -e "s#__LLAMA_DIR__#$LLAMA_DIR#g" \
    -e "s#__CONFIG__#$CONFIG#g" \
    -e "s#__PORT__#$PORT#g" \
    -e "s#__HOST__#$HOST#g" \
    -e "s#__LOG__#$LOG#g" \
    "$UNIT_SRC" > "$UNIT_DST"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
printf 'installed %s (config: %s)\n' "$UNIT_DST" "$CONFIG"
printf 'start with: systemctl start %s\n' "$SERVICE_NAME"
