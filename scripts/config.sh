#!/usr/bin/env bash
# All settings for this recipe. Edit here; start.sh writes configs/*.json from these.
# Every value below is the one this repository's measurements were taken with unless noted.

# ── Paths ────────────────────────────────────────────────────────────────────
# Where everything lives. Override in scripts/local.sh rather than here.
MODEL_DIR="${MODEL_DIR:-/data/models/qwen38-flash-next}"
STRATA_DIR="${STRATA_DIR:-/data/build/strata-v100}"     # the V100 fork checkout
UPSTREAM_DIR="${UPSTREAM_DIR:-/data/build/strata}"       # upstream checkout (expert-profile.bin lives here)
LLAMA_DIR="${LLAMA_DIR:-/data/build/llama.cpp}"          # local llama.cpp (short-circuits the network fetch)
DATA_DIR="${DATA_DIR:-/data/build/Strata-data}"          # MTP runtime and packed data
CONV_CACHE_DIR="${CONV_CACHE_DIR:-/data/strata-conv-cache}"

# ── Model files ──────────────────────────────────────────────────────────────
# The two-shard release. Shard 2 IS the PLE table and must be passed to --ple-gguf.
# See docs/FAILURE_MODES.md before substituting a merged single-file variant.
MODEL_GGUF="${MODEL_GGUF:-$MODEL_DIR/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_S-00001-of-00002.gguf}"
PLE_GGUF="${PLE_GGUF:-$MODEL_DIR/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_S-00002-of-00002.gguf}"
MMPROJ="${MMPROJ:-$MODEL_DIR/mmproj-Swift-Qwen3.8-Flash-Next-BF16.gguf}"
MODEL_NAME="${MODEL_NAME:-swift-1.5-flash-next-abliterated}"

# Hugging Face repositories the fetch script pulls from. Point these at whatever you like; the
# recipe only assumes a two-shard GGUF plus a matching mmproj.
#   weights: SC117's abliterated transplant of UkisAI's Swift 1.5 GSQ-RCO quants.
#            The IQ3_S tier lives in an IQ3_S/ subdirectory of that repo.
#   mmproj:  taken from UkisAI's own GSQ-RCO repository -- it is a standard CLIP GGUF shared
#            across every tier, and in the SC117 repo it sits at the root.
#   MTP:     the draft head is not in the quantized checkpoint at all; mtp_fetch.py reads it
#            from the base full-precision release.
HF_GGUF_REPO="${HF_GGUF_REPO:-SC117/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF}"
HF_GGUF_SUBDIR="${HF_GGUF_SUBDIR:-IQ3_S}"
HF_MMPROJ_REPO="${HF_MMPROJ_REPO:-ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF}"
HF_MTP_REPO="${HF_MTP_REPO:-ukisai/Swift-1.5-Qwen3.8-Flash-Next-GGUF}"
HF_MIRROR="${HF_MIRROR:-https://huggingface.co}"          # swap for hf-mirror.com if that is faster

# ── Context ──────────────────────────────────────────────────────────────────
CTX_512K="${CTX_512K:-1}"            # 1 → 524,288 tokens with YaRN 2×; 0 → 262,144
CTX_MAX="${CTX_MAX:-262144}"         # the 256K configuration's window

# ── Decoding ─────────────────────────────────────────────────────────────────
# 4 is the measured optimum, not a conservative default: acceptance is 76–78% at 4 and 61–65%
# at 8, so the deeper window loses. See docs/TUNING.md before changing these.
SPEC="${SPEC:-4}"
SPEC_MIN_P="${SPEC_MIN_P:-0.70}"

# ── Memory ───────────────────────────────────────────────────────────────────
# int8 is the fastest of the three options here (q4_0 is 6% slower, fp16 3% slower and 1.2 GiB
# bigger). KV_RESIDENT is required at 512K: it keeps the KV in pinned host RAM so the expert
# cache keeps its slots. ~2 GiB of host RAM per card.
KV="${KV:-int8}"
KV_RESIDENT="${KV_RESIDENT:-20480}"
VRAM_RESERVE_MIB="${VRAM_RESERVE_MIB:-700}"    # held for the vision encoder

# ── Multi-GPU ────────────────────────────────────────────────────────────────
# 48 layers; 20 means layers 0–19 on card 0 and 20–47 on card 1. Layer split rather than a peer
# cache: each card keeps its own layers' experts and no token crosses during decode. See
# docs/COMPARISON.md for the measured difference.
LAYER_SPLIT="${LAYER_SPLIT:-20}"
GPU0="${GPU0:-0}"
GPU1="${GPU1:-1}"

# ── Caching ──────────────────────────────────────────────────────────────────
# The on-disk conversation cache survives restarts; 25 GiB holds a good number of long sessions.
CONV_CACHE_DISK_GIB="${CONV_CACHE_DISK_GIB:-25}"
# Persist what the adaptive expert tier learned, and start from it next time.
EXPERT_PROFILE_SAVE="${EXPERT_PROFILE_SAVE:-1}"
EXPERT_PROFILE_SAVE_EVERY_MIN="${EXPERT_PROFILE_SAVE_EVERY_MIN:-5}"

# ── Server ───────────────────────────────────────────────────────────────────
PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"            # put TLS or a private network in front before going public
API_KEY_FILE="${API_KEY_FILE:-$HOME/.strata_api_key}"
SERVICE_NAME="${SERVICE_NAME:-strata-v100}"

# ── Overrides ────────────────────────────────────────────────────────────────
# scripts/local.sh is git-ignored and sourced last, so machine-specific paths live there
# and never get committed.
if [ -f "$(dirname "${BASH_SOURCE[0]}")/local.sh" ]; then
  # shellcheck disable=SC1091
  . "$(dirname "${BASH_SOURCE[0]}")/local.sh"
fi
