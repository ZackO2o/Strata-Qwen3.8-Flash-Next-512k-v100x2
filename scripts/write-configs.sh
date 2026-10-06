#!/usr/bin/env bash
# Write configs/512k.json and configs/256k.json from scripts/config.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
. "$HERE/config.sh"

mkdir -p "$ROOT/configs" "$CONV_CACHE_DIR"

write_config() {
  local out="$1" ctx="$2" extra="$3"
  python3 - "$out" "$ctx" "$extra" <<'PY'
import json, os, sys

out, ctx, extra = sys.argv[1], int(sys.argv[2]), sys.argv[3]
g = os.environ

args = [
    "--pack",            f"{g['MODEL_DIR']}/pack",
    "--native",          g["MODEL_GGUF"],
    "--ple-gguf",        g["PLE_GGUF"],
    "--expert-profile",  g["EXPERT_PROFILE"],
    "--expert-cache",    "auto",
    "--prefill",         "auto",
    "--spec",            g["SPEC"],
    "--spec-min-p",      g["SPEC_MIN_P"],
    "--mtp",             f"{g['DATA_DIR']}/rt",
    "--max-context",     str(ctx),
    "--kv",              g["KV"],
    "--layer-split",     g["LAYER_SPLIT"],
    "--split-device",    "1",
    "--vision",          "--vram-reserve-mib", g["VRAM_RESERVE_MIB"],
]

# 512K only: YaRN 2x extrapolation over the model's native 262,144 window.
if g.get("CTX_512K") == "1" and ctx > 262144:
    args += ["--rope-scaling", "yarn", "--rope-scale", "2"]

# Keep the KV in pinned host RAM so the expert cache keeps its slots. Only meaningful when the
# window is large enough that the KV would otherwise take that room.
if g.get("KV_RESIDENT") not in ("", "0"):
    args += ["--kv-resident", g["KV_RESIDENT"]]

# The on-disk conversation cache survives restarts; the in-process one does not.
if g.get("CONV_CACHE_DISK_GIB") not in ("", "0"):
    args += ["--conversation-cache-disk", g["CONV_CACHE_DIR"],
             "--conversation-cache-disk-gib", g["CONV_CACHE_DISK_GIB"]]

# Persist and reload what the adaptive expert tier learned.
if g.get("EXPERT_PROFILE_SAVE") == "1":
    args += ["--expert-profile-save", g["EXPERT_PROFILE"],
             "--expert-profile-save-every", g["EXPERT_PROFILE_SAVE_EVERY_MIN"]]

# The shard the engine reads the PLE table from. See docs/FAILURE_MODES.md before changing it.
cfg = {
    "exe": f"{g['STRATA_DIR']}/build/strata",
    "cwd": g["STRATA_DIR"],
    "gpu": [int(g["GPU0"]), int(g["GPU1"])],
    "port": int(g["PORT"]),
    "host": g["HOST"],
    "engine_silence_s": 0,
    "log": f"{g['DATA_DIR']}/{g['SERVICE_NAME']}.log",
    "api_key": open(g["API_KEY_FILE"]).read().strip() if os.path.exists(g["API_KEY_FILE"]) else "",
    "model_name": g["MODEL_NAME"],
    "tokenizer": f"{g['MODEL_DIR']}/pack/tokenizer",
    # child_env() takes LD_LIBRARY_PATH from here and custom variables from env. Without these
    # the engine cannot find CUDA and exits instantly with an empty log.
    "lib_dirs": [f"{g['CUDA_HOME']}/lib64"],
    "env": {"STRATA_GGUF_PY": f"{g['LLAMA_DIR']}/gguf-py",
            "PYTHONPATH": f"{g['LLAMA_DIR']}/gguf-py"},
    "vision": {
        "exe": f"{g['STRATA_DIR']}/build/strata-vision",
        "mmproj": g["MMPROJ"],
        "model": g["MODEL_GGUF"],
        "gpu": True, "max_tokens": 1024, "reserve_mib": int(g["VRAM_RESERVE_MIB"]),
    },
    "args": args,
}
json.dump(cfg, open(out, "w"), indent=2)
print(f"wrote {out}: context {ctx}, {len(args)} args")
PY
}

export MODEL_DIR STRATA_DIR UPSTREAM_DIR LLAMA_DIR DATA_DIR CONV_CACHE_DIR
export MODEL_GGUF PLE_GGUF MMPROJ MODEL_NAME SERVICE_NAME API_KEY_FILE CUDA_HOME
export SPEC SPEC_MIN_P KV KV_RESIDENT VRAM_RESERVE_MIB LAYER_SPLIT GPU0 GPU1
export CONV_CACHE_DISK_GIB EXPERT_PROFILE_SAVE EXPERT_PROFILE_SAVE_EVERY_MIN
export CTX_512K PORT HOST

# The expert profile ships with the engine's own data/ directory; the fork has a copy too.
if [ ! -f "$UPSTREAM_DIR/data/expert-profile.bin" ] && [ ! -f "$STRATA_DIR/data/expert-profile.bin" ]; then
  echo "   !  expert-profile.bin not found in either checkout; the engine will build one on first use" >&2
fi
export EXPERT_PROFILE="${EXPERT_PROFILE:-$STRATA_DIR/data/expert-profile.bin}"

write_config "$ROOT/configs/512k.json" 524288 ""
write_config "$ROOT/configs/256k.json" "$CTX_MAX" ""
