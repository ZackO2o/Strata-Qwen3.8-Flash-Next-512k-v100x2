#!/usr/bin/env bash
# Build the engine (sm_70) and the vision encoder. Safe to re-run; skips what is built.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/config.sh"

: "${CUDA_HOME:=/usr/local/cuda}"
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32mok\033[0m  %s\n' "$*"; }
warn() { printf '   \033[33m!\033[0m   %s\n' "$*"; }
die()  { printf '   \033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# ── Preconditions ────────────────────────────────────────────────────────────
say "Checking the toolchain"
command -v cmake   >/dev/null || die "cmake is not installed"
command -v nvcc    >/dev/null || die "nvcc is not on PATH (set CUDA_HOME)"
command -v nvidia-smi >/dev/null || die "nvidia-smi is not installed"

CUDA_VER="$(nvcc --version | sed -n 's/.*release \([0-9]*\)\..*/\1/p' | head -1)"
case "$CUDA_VER" in
  13) die "CUDA 13 removed sm_70 support entirely. Install CUDA 12.x." ;;
  12) ok "CUDA 12.x (major $CUDA_VER)" ;;
  *)  warn "CUDA major version $CUDA_VER is untested here; 12.x is what this recipe uses" ;;
esac

DRIVER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
ok "driver $DRIVER"
nvidia-smi --query-gpu=index,name,memory.total,compute_cap --format=csv,noheader | sed 's/^/      /'

N_GPU="$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)"
[ "$N_GPU" -ge 2 ] || warn "only $N_GPU GPU(s) visible; this recipe expects two"

[ -d "$STRATA_DIR" ]  || die "STRATA_DIR not found: $STRATA_DIR
   git clone https://github.com/jmnargi/Strata-V100.git $STRATA_DIR"
[ -d "$LLAMA_DIR" ]   || die "LLAMA_DIR not found: $LLAMA_DIR
   git clone https://github.com/ggml-org/llama.cpp $LLAMA_DIR && git -C $LLAMA_DIR checkout 3cf03257f"
ok "sources in place"

# ── Engine ───────────────────────────────────────────────────────────────────
say "Building the engine (sm_70)"
cd "$STRATA_DIR"

# The two switches that decide whether this works at all:
#   STRATA_ENABLE_CUDA defaults to OFF, and with it off the `strata` target is never declared.
#   STRATA_EXPERIMENTAL_SM60 is the community gate for sm_60/sm_70.
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DSTRATA_ENABLE_CUDA=ON \
  -DSTRATA_EXPERIMENTAL_SM60=ON \
  -DSTRATA_GGML_DIR="$LLAMA_DIR"

if ! cmake --build build --target help 2>/dev/null | grep -qE '^\.\.\. strata$'; then
  die "the 'strata' target was not declared — STRATA_ENABLE_CUDA=ON did not take effect"
fi
ok "target 'strata' is declared"

cmake --build build --target strata -j"$(nproc)"

# Verify the artifact's architecture rather than the build log's
if command -v cuobjdump >/dev/null; then
  ARCHS="$(cuobjdump build/strata 2>/dev/null | grep -oE 'sm_[0-9]+' | sort -u | tr '\n' ' ')"
else
  ARCHS="$(strings build/strata | grep -oE 'sm_[0-9]+' | sort -u | tr '\n' ' ')"
fi
case "$ARCHS" in
  *sm_70*) ok "artifact architectures: $ARCHS" ;;
  *) die "the artifact does not contain sm_70: '$ARCHS'" ;;
esac

# ── Vision encoder ───────────────────────────────────────────────────────────
say "Building the vision encoder"
# Separate CMake project — not a target of the main build.
cmake -S tools/vision -B build-vision -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_DIR="$LLAMA_DIR" \
  -DSTRATA_VISION_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70
cmake --build build-vision --target strata-vision -j"$(nproc)"
cp -f build-vision/bin/strata-vision build/strata-vision
ok "build/strata-vision"

say "Build complete"
printf '   engine: %s\n   vision: %s\n' "$STRATA_DIR/build/strata" "$STRATA_DIR/build/strata-vision"
