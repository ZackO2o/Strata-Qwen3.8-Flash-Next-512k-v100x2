#!/usr/bin/env bash
# Set up (first run) and start the server. Idempotent — re-running skips what exists.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ROOT="$(dirname "$HERE")"
. "$HERE/scripts/config.sh"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32mok\033[0m  %s\n' "$*"; }
warn() { printf '   \033[33m!\033[0m   %s\n' "$*"; }
die()  { printf '   \033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

MODE="${1:-start}"

printf '\n\033[1mStrata · Qwen3.8-Flash-Next 125B MoE · 2× Tesla V100\033[0m\n'
printf 'context %s · spec %s · kv %s · layer-split %s\n' \
  "$([ "$CTX_512K" = 1 ] && echo 524288 || echo "$CTX_MAX")" "$SPEC" "$KV" "$LAYER_SPLIT"

# ── 1. Local overrides ───────────────────────────────────────────────────────
if [ ! -f "$HERE/scripts/local.sh" ]; then
  warn "scripts/local.sh is absent — using the paths in config.sh"
  warn "copy scripts/local.sh.example and edit it if those are wrong"
fi

# ── 2. API key ───────────────────────────────────────────────────────────────
say "API key"
if [ ! -f "$API_KEY_FILE" ]; then
  umask 077; head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40 > "$API_KEY_FILE"
  chmod 600 "$API_KEY_FILE"
  ok "generated $API_KEY_FILE"
else
  ok "using $API_KEY_FILE"
fi
# An unkeyed engine serves anyone who can route to it. Do not skip this for a public bind.
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  warn "binding $HOST — make sure a firewall or a private network is in front, and see the README"
fi

# ── 3. Build ─────────────────────────────────────────────────────────────────
say "Engine"
if [ -x "$STRATA_DIR/build/strata" ]; then ok "already built"; else warn "building (30-60 min)"; bash "$HERE/scripts/build.sh"; fi

say "Vision encoder"
if [ -x "$STRATA_DIR/build/strata-vision" ]; then ok "already built"; else bash "$HERE/scripts/build.sh"; fi

# ── 4. Model ─────────────────────────────────────────────────────────────────
say "Model"
if [ -f "$MODEL_DIR/pack/index.txt" ] && [ -f "$DATA_DIR/rt/experts.bin" ]; then
  ok "weights, pack and MTP runtime present"
else
  bash "$HERE/scripts/fetch-model.sh"
fi

# ── 5. Configs ───────────────────────────────────────────────────────────────
say "Configuration"
EXPERT_PROFILE="${EXPERT_PROFILE:-$STRATA_DIR/data/expert-profile.bin}"
if [ ! -f "$EXPERT_PROFILE" ] && [ -f "$UPSTREAM_DIR/data/expert-profile.bin" ]; then
  EXPERT_PROFILE="$UPSTREAM_DIR/data/expert-profile.bin"
fi
export EXPERT_PROFILE
bash "$HERE/scripts/write-configs.sh"

if [ "${1:-}" = "256k" ] || [ "${CTX_512K}" = "0" ]; then
  SERVED_CONFIG="$ROOT/configs/256k.json"
else
  SERVED_CONFIG="$ROOT/configs/512k.json"
fi
ok "serving $(basename "$SERVED_CONFIG")"

# ── 6. Service ───────────────────────────────────────────────────────────────
say "Service"
if [ "$(id -u)" != "0" ]; then
  warn "not root — starting in the foreground instead of installing a unit"
  exec python3 -m serve.server --engine strata --config "$SERVED_CONFIG" --port "$PORT" --host "$HOST"
fi

CONFIG="$SERVED_CONFIG" bash "$HERE/scripts/install-service.sh"
systemctl restart "$SERVICE_NAME"

printf '   loading (~50 s with the profile pre-filled)'
for _ in $(seq 1 60); do
  sleep 5; printf '.'
  if ss -tln 2>/dev/null | grep -q ":$PORT"; then break; fi
done
printf '\n'

[ "$(systemctl is-active "$SERVICE_NAME")" = "active" ] || { journalctl -u "$SERVICE_NAME" -n 30 --no-pager; die "the service did not come up"; }
sleep 15
ok "listening"

# ── 7. Smoke test ────────────────────────────────────────────────────────────
say "Smoke test"
KEY="$(cat "$API_KEY_FILE")"
for _ in $(seq 1 30); do
  if curl -sf -m 5 -H "Authorization: Bearer $KEY" "http://127.0.0.1:$PORT/v1/models" >/dev/null; then break; fi
  sleep 5
done

curl -s -m 30 -H "Authorization: Bearer $KEY" "http://127.0.0.1:$PORT/v1/models" \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)['data'][0]
print('   model: %s' % d['id'])
print('   context: %s' % d['meta']['n_ctx'])
print('   input: %s' % d['architecture']['input_modalities'])"

NO_KEY="$(curl -s -o /dev/null -w '%{http_code}' -m 20 -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":4}')"
[ "$NO_KEY" = "401" ] && ok "unauthenticated requests are refused (401)" || warn "a request with no key returned $NO_KEY, not 401"

printf '   generating'
t0=$(date +%s)
RESP="$(curl -s -m 300 "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $KEY" \
  -d "{\"model\":\"$MODEL_NAME\",\"messages\":[{\"role\":\"user\",\"content\":\"In one sentence, what is a mixture-of-experts model?\"}],\"max_tokens\":200,\"temperature\":0.3}")"
dt=$(( $(date +%s) - t0 ))
printf '\n'
printf '%s' "$RESP" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    m=d['choices'][0]['message']
    txt=(m.get('content') or '').strip()
    if not txt: print('   !  empty content (the reply may be all reasoning; raise max_tokens)')
    else: print('   reply (%ds): %s' % ($dt, txt[:120].replace(chr(10),' ')))
except Exception as e:
    print('   !  could not parse the reply: %s' % e)"

say "LIVE"
printf '   endpoint: http://127.0.0.1:%s/v1\n' "$PORT"
printf '   model:    %s\n' "$MODEL_NAME"
printf '   key:      %s\n' "$API_KEY_FILE"
printf '   metrics:  curl -H "Authorization: Bearer $(cat %s)" http://127.0.0.1:%s/metrics\n' "$API_KEY_FILE" "$PORT"
printf '   logs:     journalctl -u %s -f   |   tail -f %s\n' "$SERVICE_NAME" "$(python3 -c "import json;print(json.load(open('$SERVED_CONFIG'))['log'])")"
printf '   switch:   ./switch.sh 256k && ./switch.sh 512k\n'
printf '   stop:     ./stop.sh\n'
