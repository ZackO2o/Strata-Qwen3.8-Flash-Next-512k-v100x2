# Build notes

## Toolchain

| | |
| --- | --- |
| GPU | 2× Tesla V100-PCIE-32GB (Volta, compute capability 7.0) |
| Driver | 580.159.04 |
| CUDA | 12.4.131 — **not 13.x** |
| OS | CentOS Stream 9 |
| Python | 3.9 (see the `strata_tokenizer.py` note below) |
| CPU | Xeon E5-2673 v3, 48 threads |
| RAM | 125 GiB |
| Build time | ~2 minutes on 48 threads, 30–60 on 10 |

## The two switches that decide whether this builds

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DSTRATA_ENABLE_CUDA=ON \        # ★ defaults to OFF
  -DSTRATA_EXPERIMENTAL_SM60=ON \  # ★ required for sm_60/sm_70
  -DSTRATA_GGML_DIR=<local llama.cpp>
cmake --build build --target strata -j"$(nproc)"
```

The first one is the trap. `-DSTRATA_ENABLE_CUDA=ON` **defaults to OFF**, and when it is off the
`strata` target is never declared — so configuration succeeds, the target list looks plausible,
and the only symptom is that the executable you asked for does not exist. Check it explicitly:

```bash
cmake --build build --target help | grep -E '^\.\.\. strata$'
```

The fork also uses `-DSTRATA_GGML_DIR` to point at a local llama.cpp checkout, which is a
*different* variable from the upstream `-DFETCHCONTENT_SOURCE_DIR_STRATA_LLAMACPP`. Using the
wrong one makes the build clone llama.cpp from the network, which on a slow or filtered link
appears to hang.

## Verify the architecture of the artifact

A build that reports success can still have been compiled for the wrong architecture, so check
the binary rather than the log:

```bash
cuobjdump build/strata 2>/dev/null | grep -oE 'sm_[0-9]+' | sort -u   # expect sm_70
strings build/strata | grep -oE 'sm_[0-9]+' | sort -u                 # fallback without cuobjdump
```

Success looks like this in the configure output:

```
-- Strata: CUDA enabled, arch 70
-- ggml version: 0.24.0
-- ggml commit: 3cf03257f
```

The ggml version and commit lines are also the proof that the local-checkout short-circuit
worked: if the configure step reached the network, those lines will not be there.

A `CMP0169` deprecation warning about `FetchContent_Populate` is expected. It is a note to the
upstream developer, not a build error.

## CUDA 13 will not work

CUDA 13 removed sm_70 support entirely. Pin 12.x. Verified by the community on 12.8 and 12.9.1;
we run 12.4.

## The vision encoder is a separate project

It has its own `CMakeLists.txt` under `tools/vision/` and is not part of the main target list —
`--target strata-vision` against the main build directory fails with "no rule to make target".

```bash
cmake -S tools/vision -B build-vision -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_DIR=<local llama.cpp> \
  -DSTRATA_VISION_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70
cmake --build build-vision --target strata-vision -j"$(nproc)"
```

It takes a few minutes (it pulls in the full `mtmd` machinery) and lands in
`build-vision/bin/strata-vision`. Copy it next to the engine. Self-test: it prints
`READY <n>` and has no `--help`.

`STRATA_VISION_CUDA=OFF` builds a CPU encoder, which is what you want with 16 GB cards and no
spare VRAM; it costs about 3 s per small image against a fraction of a second on GPU.

**Enabling it takes three edits, not one.** The `vision` block in the config starts the encoder
process; the *engine* also needs `--vision` in its argument list (without it, image requests
come back `400 this engine was started without --vision`); and `--vram-reserve-mib` should
match what the encoder will take. Miss the second one and only the image path breaks.

## Python 3.9 and the packer

`tools/strata_tokenizer.py` calls `Path.write_text(newline=...)`, which is Python 3.10+. On 3.9
the packing step fails at its last stage with a `TypeError` — after writing most of the pack, so
the directory looks populated. Drop the keyword argument or use a newer Python. Do not replace
the interpreter for this.

Patch the tool, then run:

```bash
export STRATA_GGUF_PY=<llama.cpp>/gguf-py
export PYTHONPATH=$STRATA_GGUF_PY:$PYTHONPATH
python3 tools/iq_pack.py --gguf <shard1>.gguf --out <model-dir>/pack --compat-bf16
```

Note the named arguments: `--gguf` and `--out` are required and positional use fails. Success
prints the tensor and native-served counts plus `compat-bf16: ... expert and PLE table bytes
unchanged`.

## The MTP draft head is not in the quantized checkpoint

The engine's speculative decoding needs the model's MTP tensors, which live only in the base
BF16 checkpoint — 31 tensors, ~5.21 GB, fetched by HTTP range without downloading all ~360 GB.
If that fetch is blocked, the community approach is to pull the BF16 shards from a mirror and
extract the `mtp.*` tensors locally, producing `tensors/*.bin` plus a manifest the official
tool then validates. The output is a `rt/` directory with `dense.bin`, `experts.bin`, `dense.txt`
(~786 MB) — and it is also where `draft_vocab.bin` must land (see the README).

## Service behaviour

- **systemd does not inherit your shell.** `PATH`, `LD_LIBRARY_PATH`, `STRATA_GGUF_PY` and
  `PYTHONPATH` must be set both in the unit's `Environment=` and in the config's
  `lib_dirs`/`env` — the second is what the launcher passes to the engine it spawns.
- **`ExecStartPre=` with a `pkill` that matches nothing exits 1.** systemd logs it as
  `status=1/FAILURE` and it is *not* an error. Cleaning up stragglers before start is what
  brings load time from ~200 s down to ~50 s, so leave it in.
- `KillMode=control-group` is what takes the `strata-vision` child down with the engine.
