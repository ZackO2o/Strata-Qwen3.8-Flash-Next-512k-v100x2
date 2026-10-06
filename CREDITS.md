# Credits

## The engine

- **[Strata](https://github.com/Niko1221/Strata)** by **Niko1221** — a CPU+GPU hybrid MoE
  inference engine that keeps the expert set in host memory, computes experts on the CPU and the
  GPU at once, and drafts with the model's own MTP head. The idea that a 125B MoE belongs on
  hardware you already own is theirs, and so is every kernel here except the Volta ones.

- **[jmnargi/Strata-V100](https://github.com/jmnargi/Strata-V100)** by **jmnargi** — the V100
  fork this recipe builds. It supplies the Volta prompt-attention kernels (using all four
  `mma.m8n8k4` matrix ops rather than two), the reduced-shuffle Volta decode-attention path,
  concurrent serving slots, the on-disk conversation cache and `--kv-resident`. Builds here are
  from `310e5cb`. Several findings in this repository — including the expert profile that
  survives restarts — are features this fork added.

- **[llama.cpp](https://github.com/ggml-org/llama.cpp)** by the ggml authors — the GGUF
  container, the quantization kernels and the packing tools. Pinned at `3cf03257f`, which is the
  commit both the fork and the upstream engine expect for their ggml dependency.

## Weights and model

- **Qwen3.8-Flash-Next** by **Qwen / Alibaba** — 125B MoE, 24,576 experts, ~6B active per token.
- **ISTA-DASLab GSQ-RCO** — the quantization scheme and process behind the GGUF series.
- **The `Swift-1.5` abliterated `IQ3_S` derivative** — the two-shard build used here.
  Abliteration modifies the weights; read its model card for its own terms rather than assuming
  the base model's apply.

## Measurement discipline

- **[jiangrun0213/strata-v100-notes](https://github.com/jiangrun0213/strata-v100-notes)** by
  **jiangrun0213** — a double-V100 SXM2 16G build with a careful published methodology. The
  comparison in [docs/COMPARISON.md](docs/COMPARISON.md) runs *their* benchmark script against
  this server so the two sets of numbers are commensurable, and their write-up is why we tested
  `--spec-min-p` at all. Their conclusion that V100 has a 59–63 tok/s ceiling is the specific
  claim this repository argues against — for 32G cards. The comparison document explains why the
  difference is memory class rather than architecture, and why we could be wrong.

- **[MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold)**
  — not a dependency: a different model on different hardware. It is the model for how this
  repository is written and documented — state the configuration, give the table, name what was
  not measured, credit everything underneath. Their independent-qualification section, which
  records a community result as a reproducible finding rather than claiming it as a win, is the
  standard we tried to hold ourselves to.

## Tools used in the build and the measurements

`cmake`, `nvcc`, `aria2c`, `Pillow` (test images), `curl`, `systemd`.

## Ours

The configuration, the tuning matrix, the failure-mode write-ups, the measurement harness and
the packaging scripts in this repository. Apache-2.0, see [LICENSE](LICENSE).
