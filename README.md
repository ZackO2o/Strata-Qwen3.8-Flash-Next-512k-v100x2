# Qwen3.8-Flash-Next 125B MoE on Tesla V100 32G × 2, 512K context

**A 125B mixture-of-experts model, uncensored, 512K tokens of context and image input, served
from two six-year-old Tesla V100 32G cards — at 64–79 tok/s decode and up to 1,863 tok/s prefill.**

Two Tesla V100-PCIE-32G (Volta, sm_70), cards NVIDIA no longer supports in the engine's
prebuilt binaries. [Strata](https://github.com/Niko1221/Strata) is a CPU+GPU hybrid MoE engine
whose release builds contain **sm_75 and above only**; its author has stated sm_70 stays a
community test path and will not ship in the prebuilt engine. We build it ourselves from the
[V100 fork](https://github.com/jmnargi/Strata-V100), and this repository is everything that
took: the build, the memory arithmetic, the tuning, the measurements, and the dead ends.

- Model: **Qwen3.8-Flash-Next** 125B MoE (24,576 experts, ~6B active), `abliterated`, `IQ3_S`,
  **77.9 GiB** in two shards — served through the OpenAI-compatible API
- Context: **524,288 tokens** a request (YaRN 2× extrapolation over the model's native 262,144)
- Vision: yes, the model's own encoder, run on the GPU
- Decode: **71.3 tok/s** prose, **69.4 tok/s** code (512-token outputs)
- Prefill: **1,611.8 tok/s** at 14.5K, **1,862.6 tok/s** at 29K
- Memory: about **120 GiB of system RAM and 64 GiB of VRAM** — one of these cards, or one
  desktop-class GPU with 24 GB, is not enough; you need the pair
- One command: `./start.sh` sets up and starts it; `./stop.sh` stops it

This is the V100 counterpart to the
[double-DGX-Spark TensorFold recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold):
same shape of problem — a large MoE that does not fit one device — solved on hardware that is
five generations behind, for a fraction of the money.

## Performance

Two Tesla V100-PCIE-32G, Xeon E5-2673 v3 (48 threads), 125 GiB RAM, CentOS Stream 9,
driver 580.159.04, CUDA 12.4, engine built from the V100 fork at `310e5cb`, context 524,288,
`--kv int8 --kv-resident 20480`, `--spec 4 --spec-min-p 0.70`. Measured through the
OpenAI API on the loopback interface, from the same machine.

### Decode

Aggregate is what a caller sees across all in-flight requests; per-request is what each one gets.

| Concurrent requests | Prose, aggregate | Prose, per request | Code, aggregate | Code, per request |
| --: | --: | --: | --: | --: |
| 1 | **64.1 tok/s** | 64.1 tok/s | — | — |
| 2 | 63.1 tok/s | 48.2 tok/s | — | — |
| 4 | 64.2 tok/s | 33.4 tok/s | — | — |

Single-request decode by output length, medians of three runs each, prompt 133 tokens:

| Output | Prose | Code |
| --: | --: | --: |
| 128 tokens | 78.5 tok/s | 69.5 tok/s |
| 512 tokens | 55.0–62.7 tok/s (57.5) | 61.2–66.0 tok/s (64.4) |
| 2048 tokens | 73.2–81.8 tok/s (78.7) | 64.8–66.8 tok/s (66.2) |

Code is slower than prose, consistently, by about 15% at 2048 tokens: code is where the draft
head earns least (see [Why these numbers](#why-these-numbers)).

### Prefill

| Prompt | Prefill | Wall time |
| --: | --: | --: |
| 186 tokens | 159.8 tok/s † | 1.16 s |
| 981 tokens | 489.9 tok/s | 2.00 s |
| 3,684 tokens | 993.7 tok/s | 3.71 s |
| 14,549 tokens | 1,611.8 tok/s | 9.03 s |
| 29,018 tokens | **1,862.6 tok/s** | 15.58 s |

† Short prompts are dominated by fixed startup cost — 1.2 s of it. Read that row as "even a
one-line question costs about a second before the first token", not as a throughput figure.

### Long context

Needle in a haystack, one sentence planted in the middle of filler, asked to quote it exactly:

| Document | Actual prompt | Result | Wall time |
| --: | --: | :-: | --: |
| ~8K tokens | 7,621 | found | 7.6 s |
| ~32K tokens | 30,229 | found | 12.6 s |
| ~131K tokens | 120,665 | found | 56.7 s |

512K is the configured ceiling; the largest *tested* retrieval is 120,665 tokens. We have not
run a full 512K-token recall test — see [What we did not measure](#what-we-did-not-measure).

### Prompt reuse

| Prompt | First time | Next time |
| --: | --: | --: |
| The same 3,684-token prompt, sent again | 2,460 ms | **848 ms** |
| The same prompt a third time | — | **561 ms** |

Resuming is a real 3–4× at this size, and it is the reason multi-turn conversation is cheap:
an append-only session pays prefill once and then reads back. Strata's own conversation cache
also survives a **restart** here (`--conversation-cache-disk`, 25 GiB on disk): a parked
3,444-token conversation came back in 984.5 ms against 3,089 ms cold.

### Streaming

| | |
| --: | --: |
| Time to first token | **88 ms** (short prompt, warm) |
| Chunk interval p50 | 21 ms |
| Chunk interval p95 | **36 ms** |
| 2,048-token reply, wall clock | 38.8 s |

### Vision

The model's own encoder, on the GPU, `--vram-reserve-mib 700`. A generated test card reading
`STRATA 512K` with a red-outlined rectangle and a blue ellipse comes back described
literally — the string read character for character, both shapes named with their colours and
positions. OCR of a synthetic image is a smoke test, not a benchmark: it tells you the tower is
wired up and the image reached the model, nothing about accuracy on hard images.

## Why these numbers

Five things decided the decode rate, in order of how much they mattered. Each was measured;
three of them are counter-intuitive.

### 1. `--spec 4` beats `--spec 8`, and `--spec-min-p 0.70` beats 0.85

The engine drafts tokens with the model's own MTP head and verifies them in one forward. The
intuition is that a deeper draft window wins. It does not:

| Draft window | Decode | Draft acceptance |
| --: | --: | --: |
| `--spec 8` | 72.9 tok/s | 61–65% |
| **`--spec 4`** | **79.4 tok/s** | **76–78%** |
| `--spec 6` | 74.2 tok/s | 72.8% |
| `--spec 12` | 67.8 tok/s | 60.2% |

A shallower window proposes fewer tokens but a much higher fraction of them land, so each
verify forward yields more real output. `--spec 8` proposes more and wastes more. The same
inversion holds for the acceptance threshold — a *stricter* `min-p` produces a higher acceptance
rate and a *slower* server, because it also suppresses the number of drafts:

| `--spec-min-p` | Decode | Acceptance |
| --: | --: | --: |
| **0.70** | **78.7 tok/s** | 75.9% |
| 0.50 | 76.7 tok/s | 65.0% |
| 0.85 | 73.8 tok/s | 82.4% |
| 0.95 | 68.9 tok/s | 89.3% |

### 2. The CJK draft vocabulary — the single largest silent win

The draft head may only propose tokens from a **subset of the vocabulary**, shipped as
`rt/draft_vocab.bin`. The engine's built-in default was built from English and code and contains
**27 of the model's 55,328 Han tokens**. Chinese answers therefore draft almost nothing.

The repository ships a full-CJK subset (`data/draft_vocab.bin`, 106,299 ids, all 55,328 Han
covered) — but **the engine reads `<rt_dir>/draft_vocab.bin`, not the repository copy**, so
unless you put it where the runtime directory is, you silently run on the English-only default.
This is one `cp`:

```
cp data/draft_vocab.bin <Strata-data>/rt/draft_vocab.bin
```

Build your own for another script with `tools/draft_vocab.py --add {han,kana,hangul,cjk_punct,cjk,cyrillic}`,
and check what you have with `--stats`. Ours reports `han 55328/55328, kana 5476/5476,
hangul 6807/6807, cjk_punct 231/231`.

### 3. Expert cache — hit rate is already 99.5%, so the remaining lever is slots

The model keeps 24,576 experts in RAM and a working set in VRAM. On this machine the cache hits
**99.4–99.8%**, and the log says so on every reply. Anything advertised as an expert-cache
optimisation that targets *hit rate* therefore has nothing to give here — we measured
`--expert-cache-per-layer` (which fixes a real 2.97% pathology on other configurations) and it
changed nothing for us. What did matter was **how many slots fit**, because that is what the
512K context competes for.

### 4. Context is not free: 512K costs you decode speed

KV state is resident VRAM and so is the expert cache; on one card they are zero-sum.

| | 256K | **512K** |
| --: | --: | --: |
| `--max-context` | 262,144 | **524,288** |
| RoPE | none | **yarn, scale 2** |
| Expert slots (primary) | 12,300 | **10,240** |
| Expert-cache VRAM | 24.34 GiB | **18.46 GiB** |
| Decode, prose 512 | 57.5 tok/s | 57.5 tok/s |
| Prefill, 14.5K | 1,611.8 tok/s | **1,611.8 tok/s** |

The two rows you would expect to differ are the same because `--kv-resident` moves the KV
cache into pinned host RAM and leaves only the attention window on the card. It is **not
optional at 512K**: without it the KV state alone takes the slots the expert cache needs.
`--kv-resident 20480` costs about 2 GiB of host RAM per card in swap space and buys back
2 GiB of VRAM per card.

### 5. `--batch` concurrency is a loss here, and we can show why

The engine can run several requests in parallel slots. On this configuration it is **strictly
worse** than queueing them:

| Configuration | 4 requests, wall clock | Aggregate | Per request |
| --: | --: | --: | --: |
| **No `--batch`** | 8.0 s | **53.5 tok/s** | 71–80 tok/s |
| `--kv-resident` + `--batch 2`, 512K | 9.5 s | 42.2 tok/s | 26–38 tok/s |
| `--batch 2`, 256K | 8.1 s | 49.6 tok/s | 29–39 tok/s |

The engine's own `docs/BATCHING.md` names the reason: *"Batch windows carry no MTP drafts"*. A
request in a slot decodes one token per forward instead of a drafted batch, so per-request
speed collapses from ~77 to ~27 tok/s, and the slots compete with the expert cache on top. Our
aggregate stays flat at ~64 tok/s from 1 to 4 concurrent requests — the queue is not the
bottleneck, the single-request decode rate is, so there is nothing for slots to recover. This
trade only pays where experts fit entirely in VRAM *and* contexts are short.

## Requirements

- **Two Tesla V100 (or any sm_70 part) with 32 GB each.** 16 GB cards work but halve the expert
  cache — a 16 GB pair measured 59–61 tok/s decode and 765 tok/s prefill against our 64–79 and
  1,611–1,863 on 32 GB cards with the same model and nearly the same flags. **The V100 32G pair
  is the recipe**; 16 GB cards are the budget version.
- **~120 GiB of system RAM.** The expert arena is memory-mapped and computed by CPU and GPU
  together; this is where the model lives. 64 GiB works for smaller quantizations at smaller
  context.
- **~180 GiB of disk**: 79 GiB of weights, ~1.5 GiB of packed data, ~0.9 GiB of vision encoder,
  ~800 MiB of MTP runtime, and room for the build.
- **CUDA 12.x — not 13.** CUDA 13 dropped sm_70 entirely. We use 12.4; the community fork is
  verified on 12.8 and 12.9.1.
- **Driver 580 or newer.**
- A C++ toolchain and `cmake`; the build takes 30–60 minutes on 10 cores, about 2 on 48.

CentOS Stream 9 is what we run. The engine is only officially automated on Ubuntu 22.04/24.04,
so on RHEL-family systems you lay the dependencies out yourself — that is the one part of this
repository that is distribution-specific.

## Quick start

```bash
git clone https://github.com/ZackO2o/v100-qwen38-flashnext-512k.git
cd v100-qwen38-flashnext-512k
cp scripts/local.sh.example scripts/local.sh   # edit the paths
./start.sh
```

`start.sh` checks the toolchain, builds the engine and the vision encoder if they are not
built, downloads and packs the model on first run, writes both configurations, runs the
smoke test, and prints the endpoint. `./start.sh restart` after a settings change;
`./stop.sh` to stop. The service runs under systemd with `Restart=always`, so a crash comes
back by itself.

Any OpenAI client works against `http://127.0.0.1:8080/v1`, model
`swift-1.5-flash-next-abliterated`:

```bash
curl -s http://127.0.0.1:8080/v1/models
curl -s http://127.0.0.1:8080/v1/chat/completions -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $STRATA_API_KEY" -d '{
  "model": "swift-1.5-flash-next-abliterated",
  "messages": [{"role": "user", "content": "Write a Python fibonacci function."}],
  "max_tokens": 2000
}'
curl -s http://127.0.0.1:8080/metrics -H "Authorization: Bearer $STRATA_API_KEY"   # live state
```

An **API key is mandatory** the moment this is reachable from anywhere but localhost — the
engine will otherwise happily serve anyone who can route to it. `start.sh` generates one into
`~/.strata_api_key`; `/v1/models`, `/metrics` and `/props` all need it too, so it is a real
gate and not decoration. Put it in front of TLS or a private network for anything public.

## Configuration

All settings live in `scripts/config.sh` (which writes `configs/*.json`); nothing is baked into
the image or the scripts.

| Setting | Default | What it does |
| --- | --- | --- |
| `CTX_512K` | `1` | Write the 512K config, or the 256K one with the expert cache at full size |
| `SPEC` | `4` | Draft window. Higher is *slower* past 4 — see [above](#1---spec-4-beats---spec-8-and---spec-min-p-070-beats-085). |
| `SPEC_MIN_P` | `0.70` | Draft acceptance floor |
| `KV` | `int8` | KV storage. `fp16` costs VRAM, `q4_0` is 6% **slower** — the dequantization outweighs the memory saved |
| `KV_RESIDENT` | `20480` | KV tokens kept in pinned host RAM. Required at 512K |
| `LAYER_SPLIT` | `20` | First layer on the second card. 48 layers, so this is 20/28 — see [below](#why-layer-split-and-not-a-peer-cache) |
| `CONV_CACHE_DISK_GIB` | `25` | On-disk conversation cache, survives restarts |
| `EXPERT_PROFILE_SAVE` | `1` | Persist what the adaptive tier learned; reload it on the next start |
| `VRAM_RESERVE_MIB` | `700` | Held for the vision encoder |
| `PORT` | `8080` | API port |

### Why layer split, and not a peer cache

The engine's other multi-GPU mode, `--peer-device`, uses the second card as a *second-level
expert cache* reached over NVLink. We use layer splitting instead — each card owns its own
layers' experts and a token never crosses between them during decode. The comparison against
the published 16 GB SXM2 build is in [docs/COMPARISON.md](docs/COMPARISON.md): same model, same
engine family, same measurement script, roughly 15–35% faster decode and 2.2–3.7× faster
prefill here. Two V100 32G cards have room to hold the working set locally, and a
no-peer-traffic design spends that room; 16 GB cards do not, so they need the peer cache to
have anywhere to put the rest.

## What we did not measure

Stated plainly so nobody cites this as more than it is:

- **Full 512K-token recall.** Our deepest needle test is 120,665 tokens. 512K is configured and
  the model loads and answers at it; recall quality across the whole window is untested.
- **Quality benchmarks.** No GSM8K, no HumanEval, no MMLU. We did not measure accuracy, and a
  `Q2/Q3`-class quantization is exactly where you would expect to find the cost of this recipe.
  Treat decode and prefill numbers here as safe to quote and quality numbers as absent.
- **Concurrency beyond 4.** We tested 1, 2 and 4 and stopped when it was clear the aggregate was
  flat and `--batch` was a loss.
- **Long-run stability.** These are benchmark figures from a live server. We have not run a
  multi-day soak.
- **Anything about a third card.** Untested here.

## The two things that cost us the most

Recorded because they are the expensive ones, and neither is in the engine's documentation:

1. **A merged single-file quantized model can load, pass every integrity check, and still be
   broken.** We spent an entire session on a third-party `-merged.gguf` that loaded cleanly,
   passed a bit-exact cross-check on the quantized kernel, showed 100% draft acceptance and
   115 tok/s — and emitted the same token, `!` (id 0), for every reply. The cause is layout, not
   corruption: the engine reads the PLE table from **the start of the file named by
   `--ple-gguf`**, and a merged file has that table 2.5 GB in with `output.weight` at the front,
   so the engine read the output head as the PLE table and the residual stream collapsed. The
   standard two-shard release, same engine, same flags, worked immediately. **Check the shard
   before you check the flags**: the PLE shard must contain exactly one tensor, at offset 192,
   sized as the PLE table. The integrity checks that the wrong file passes are listed in
   [docs/FAILURE_MODES.md](docs/FAILURE_MODES.md).
2. **A parameter name that does not exist fails silently.** `--conversation-cache-disk-mib`
   is not a setting. The real ones are `--conversation-cache-disk <PATH>` and
   `--conversation-cache-disk-gib <N>`. Written wrongly, the cache does not work, no error is
   raised, and start-up looks perfect. Grep the engine's `--help` for the exact string before
   you trust a remembered flag name.

## Repository layout

```
start.sh          set up and start (build, model, configs, smoke test)
stop.sh           stop it
switch.sh         switch between the 512K and 256K configurations
scripts/          config.sh (all settings), local.sh.example (this machine's paths),
                  build.sh (engine + vision, sm_70), fetch-model.sh (download, pack, MTP
                  runtime, draft vocabulary), write-configs.sh, install-service.sh,
                  systemd/strata-v100.service.in
configs/          512k.json, 256k.json — written from config.sh, safe to read
patches/          what we had to change in the engine source, and why
tools/            bench.py (every number in this README, one run),
                  needle.py, vision_smoke.py, check_ple_shard.py, draft_vocab_stats.py
docs/             COMPARISON.md, FAILURE_MODES.md, BUILD_NOTES.md, TUNING.md
CHANGELOG.md      what changed in each revision
CREDITS.md        who and what this builds on
LICENSE           Apache License 2.0
NOTICE            third-party notices
```

## License

Apache License 2.0, see [`LICENSE`](LICENSE). [`NOTICE`](NOTICE) carries the third-party
notices: this repository contains scripts, configuration and patches, and the patches modify
[Strata](https://github.com/Niko1221/Strata) and its [V100 fork](https://github.com/jmnargi/Strata-V100)
without redistributing either. Both remain under their own terms.

The model files are downloaded, not included: the `Swift-1.5` `IQ3_S` GGUF and its vision
encoder from their Hugging Face repositories, and the MTP draft head, which is extracted from
the base `Qwen/Qwen3.8-Flash-Next` checkpoint. Each carries its own license — read them, and
note that a **re-quantized or abliterated derivative can carry terms the base model does not**.
Nothing here grants rights to the weights.

## Credits

Built on [Strata](https://github.com/Niko1221/Strata) by Niko1221 — the engine, and the idea
that a 125B MoE belongs on hardware you already own. The **V100 fork**
[jmnargi/Strata-V100](https://github.com/jmnargi/Strata-V100) by jmnargi supplies the Volta
prompt-attention kernels, the concurrent serving slots and the on-disk conversation cache;
without it none of this runs at these numbers. [llama.cpp](https://github.com/ggml-org/llama.cpp)
provides the GGUF toolchain and quantized kernels (`3cf03257f`). The quantized weights come
from the ISTA-DASLab GSQ-RCO series; the `Swift-1.5` abliterated derivative is the specific
build used here. The measurement discipline — one script, one prompt set, cross-checked
figures, and results reported against an independent build —
follows [jiangrun0213/strata-v100-notes](https://github.com/jiangrun0213/strata-v100-notes),
whose 16 GB SXM2 build we compare against directly in [docs/COMPARISON.md](docs/COMPARISON.md).

Full list in [`CREDITS.md`](CREDITS.md).
