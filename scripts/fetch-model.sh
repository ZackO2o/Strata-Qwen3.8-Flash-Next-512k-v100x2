#!/usr/bin/env bash
# Download the model, pack it for the engine, and build the MTP draft head runtime.
# Large downloads: aria2c multi-connection where available, resumable either way.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/config.sh"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32mok\033[0m  %s\n' "$*"; }
warn() { printf '   \033[33m!\033[0m   %s\n' "$*"; }
die()  { printf '   \033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p "$MODEL_DIR" "$DATA_DIR"

# ── Fetch ────────────────────────────────────────────────────────────────────
# Sizes are checked against the mirror's own Content-Length rather than a hand-computed
# byte count: a wrong expected size is how you end up re-downloading a 52 GiB file.
fetch() {
  local url="$1" out="$2"
  local want
  want="$(curl -sIL -m 60 "$url" | awk 'BEGIN{IGNORECASE=1} /^content-length:/{v=$2} END{gsub(/\r/,"",v); print v}')"
  [ -n "$want" ] || die "could not read Content-Length for $url"

  if [ -f "$out" ]; then
    local have; have="$(stat -c%s "$out")"
    if [ "$have" = "$want" ]; then ok "$(basename "$out") already complete ($have bytes)"; return 0; fi
    warn "$(basename "$out") is $have of $want bytes — resuming"
  else
    printf '   downloading %s (%s bytes)\n' "$(basename "$out")" "$want"
  fi

  if command -v aria2c >/dev/null; then
    aria2c -x 16 -s 16 -k 4M --continue=true --file-allocation=none \
      --max-tries=50 --retry-wait=5 --auto-file-renaming=false --allow-overwrite=true \
      --console-log-level=warn --summary-interval=30 \
      -d "$(dirname "$out")" -o "$(basename "$out")" "$url"
  else
    warn "aria2c not installed — single-stream wget (expect ~4x slower); dnf install aria2 to fix"
    wget -c -T 120 -t 20 --progress=dot:giga -O "$out" "$url"
  fi

  local got; got="$(stat -c%s "$out")"
  [ "$got" = "$want" ] || die "$(basename "$out"): got $got bytes, expected $want"
  ok "$(basename "$out") ($got bytes)"
}

say "Downloading the model"
fetch "$HF_MIRROR/$HF_GGUF_REPO/resolve/main/$(basename "$MODEL_GGUF")" "$MODEL_GGUF"
fetch "$HF_MIRROR/$HF_GGUF_REPO/resolve/main/$(basename "$PLE_GGUF")"  "$PLE_GGUF"
fetch "$HF_MIRROR/$HF_MMPROJ_REPO/resolve/main/$(basename "$MMPROJ")"  "$MMPROJ"

# ── Sanity check: the PLE shard's shape ──────────────────────────────────────
# This is the check that would have saved us a session. The engine reads the PLE table from the
# START of the file given to --ple-gguf; a merged single-file variant passes every other
# integrity test and still produces garbage. See docs/FAILURE_MODES.md.
say "Checking the PLE shard's shape"
python3 - "$PLE_GGUF" "$LLAMA_DIR/gguf-py" <<'PY'
import sys
ple, gguf_py = sys.argv[1], sys.argv[2]
sys.path.insert(0, gguf_py)
from gguf import GGUFReader
r = GGUFReader(ple)
print(f"   tensors: {len(r.tensors)}")
for t in r.tensors:
    print(f"     {t.name}  offset={t.data_offset}  bytes={t.n_bytes}  {t.tensor_type}")
if len(r.tensors) != 1:
    print("   !  expected exactly one tensor (the per-layer embedding table).")
    print("   !  A merged single-file model will fail here — use the two-shard release.")
    sys.exit(1)
t = r.tensors[0]
if t.data_offset > 4096:
    print(f"   !  the table starts at offset {t.data_offset}, not at the front of the file.")
    print("   !  This is the merged-file layout and the engine will misread it.")
    sys.exit(1)
print("   ok  single tensor at the start of the file")
PY

# ── Pack ─────────────────────────────────────────────────────────────────────
if [ -f "$MODEL_DIR/pack/index.txt" ]; then
  ok "pack already present"
else
  say "Packing for the engine"
  command -v python3 >/dev/null || die "python3 is required"
  # tools/strata_tokenizer.py uses Path.write_text(newline=...), which is Python 3.10+.
  # The packer applies the compatibility patch itself; see patch-notes in docs/BUILD_NOTES.md.
  ( cd "$STRATA_DIR" && \
    STRATA_GGUF_PY="$LLAMA_DIR/gguf-py" PYTHONPATH="$LLAMA_DIR/gguf-py:${PYTHONPATH:-}" \
    python3 tools/iq_pack.py --gguf "$MODEL_GGUF" --out "$MODEL_DIR/pack" --compat-bf16 )
  [ -f "$MODEL_DIR/pack/index.txt" ] || die "packing did not produce index.txt"
  ok "pack written"
fi

# The engine's tokenizer lookup defaults to <strata>/pack/full/tokenizer.
if [ ! -e "$STRATA_DIR/pack/full/tokenizer" ]; then
  mkdir -p "$STRATA_DIR/pack/full"
  ln -sfn "$MODEL_DIR/pack/tokenizer" "$STRATA_DIR/pack/full/tokenizer"
  ok "linked the tokenizer to where the server looks for it"
fi

# ── MTP draft head ───────────────────────────────────────────────────────────
# The speculative-decoding tensors are NOT in the quantized checkpoint — only the base BF16 one.
if [ -f "$DATA_DIR/rt/experts.bin" ]; then
  ok "MTP runtime already present"
else
  say "Building the MTP draft head runtime"
  ( cd "$STRATA_DIR" && \
    STRATA_GGUF_PY="$LLAMA_DIR/gguf-py" PYTHONPATH="$LLAMA_DIR/gguf-py:${PYTHONPATH:-}" \
    python3 tools/mtp_fetch.py inventory --out "$DATA_DIR/mtp" )
  ( cd "$STRATA_DIR" && \
    STRATA_GGUF_PY="$LLAMA_DIR/gguf-py" PYTHONPATH="$LLAMA_DIR/gguf-py:${PYTHONPATH:-}" \
    python3 tools/mtp_fetch.py fetch     --out "$DATA_DIR/mtp" )
  ( cd "$STRATA_DIR" && \
    STRATA_GGUF_PY="$LLAMA_DIR/gguf-py" PYTHONPATH="$LLAMA_DIR/gguf-py:${PYTHONPATH:-}" \
    python3 tools/mtp_fetch.py verify    --out "$DATA_DIR/mtp" )
  ( cd "$STRATA_DIR" && \
    STRATA_GGUF_PY="$LLAMA_DIR/gguf-py" PYTHONPATH="$LLAMA_DIR/gguf-py:${PYTHONPATH:-}" \
    python3 tools/mtp_pack.py --src "$DATA_DIR/mtp" --experts q2_0 --out "$DATA_DIR/mtp-q2_0.gguf" )
  ( cd "$STRATA_DIR" && \
    STRATA_GGUF_PY="$LLAMA_DIR/gguf-py" PYTHONPATH="$LLAMA_DIR/gguf-py:${PYTHONPATH:-}" \
    python3 tools/mtp_rt.py --gguf "$DATA_DIR/mtp-q2_0.gguf" --out "$DATA_DIR/rt" )
  ok "MTP runtime at $DATA_DIR/rt"
fi

# ── The silent one: the CJK draft vocabulary ─────────────────────────────────
# The engine reads <rt>/draft_vocab.bin. Without it, the built-in default covers 27 of 55,328
# Han tokens and Chinese answers draft almost nothing — with no error anywhere.
say "Installing the draft vocabulary"
if [ -f "$DATA_DIR/rt/draft_vocab.bin" ]; then
  ok "already present"
else
  if [ -f "$STRATA_DIR/data/draft_vocab.bin" ]; then
    cp "$STRATA_DIR/data/draft_vocab.bin" "$DATA_DIR/rt/draft_vocab.bin"
    ok "copied the full-CJK subset into the runtime directory"
  else
    warn "no data/draft_vocab.bin in the checkout — the English/code default will be used"
  fi
fi
python3 "$ROOT/tools/draft_vocab_stats.py" "$DATA_DIR/rt/draft_vocab.bin" 2>/dev/null || true

say "Model ready"
printf '   weights: %s\n   pack:    %s\n   runtime: %s\n' "$MODEL_DIR" "$MODEL_DIR/pack" "$DATA_DIR/rt"
