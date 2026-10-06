# Strata-Qwen3.8-Flash-Next-512k-v100x2

**A 125B mixture-of-experts model, uncensored, 512K tokens of context and image input, served
from two six-year-old Tesla V100 32G cards — at 74 tok/s decode on prose and 100 tok/s on code.**

Two Tesla V100-PCIE-32G (Volta, sm_70), cards NVIDIA no longer supports in the engine's
prebuilt binaries. [Strata](https://github.com/Niko1221/Strata) is a CPU+GPU hybrid MoE engine
whose release builds contain **sm_75 and above only**; its author has stated sm_70 stays a
community test path and will not ship in the prebuilt engine. We build it ourselves from the
[V100 fork](https://github.com/jmnargi/Strata-V100), and this repository is everything that
took: the build, the memory arithmetic, the tuning, the measurements, and the dead ends.

- Model: **Swift 1.5 Qwen3.8-Flash-Next**, abliterated, `IQ3_S` — 125B MoE (24,576 experts,
  ~6B active), 78.5 GiB in two shards, served through the OpenAI-compatible API
- Context: **524,288 tokens** a request (YaRN 2× extrapolation over the model's native 262,144)
- Vision: yes, the model's own encoder, run on the GPU
- Decode: **74.1 tok/s** prose, **100.0 tok/s** code, 512-token outputs (prose IQR 2.7)
- Prefill: **1,684.1 tok/s** at ~16K cold; 1,082.3 tok/s at ~4K, where fixed cost still bites
- Retrieval: correct at 8K, 30K, 121K and **241K tokens**
- Prompt reuse: **82× faster** on a repeat, and the cache survives a restart
- Memory: about **120 GiB of system RAM and 64 GiB of VRAM** — one of these cards, or one
  desktop-class GPU with 24 GB, is not enough; you need the pair
- Concurrency: the engine serves **one request at a time**; four concurrent requests queue
- One command: `./start.sh` sets up and starts it; `./stop.sh` stops it

This is the V100 counterpart to the
[double-DGX-Spark TensorFold recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold):
same shape of problem — a large MoE that does not fit one device — solved on hardware that is
five generations behind, for a fraction of the money.

## The model, and exactly where it comes from

Every file this recipe loads, and where to get it:

| Component | Source | File | Size |
| --- | --- | --- | ---: |
| Weights, shards 1–2 | [SC117/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF](https://huggingface.co/SC117/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF) | `IQ3_S/…-abliterated-IQ3_S-00001-of-00002.gguf` | 47.3 GB |
| | the same repository | `IQ3_S/…-abliterated-IQ3_S-00002-of-00002.gguf` | 28.8 GB |
| Vision encoder | [ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF](https://huggingface.co/ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF) | `mmproj-Swift-Qwen3.8-Flash-Next-BF16.gguf` | 0.91 GB |
| MTP draft head | [ukisai/Swift-1.5-Qwen3.8-Flash-Next-GGUF](https://huggingface.co/ukisai/Swift-1.5-Qwen3.8-Flash-Next-GGUF) | extracted from the full-precision shards at pack time | — |

Both weight shards are required and they are not interchangeable. **Shard 2 goes to `--ple-gguf`**
because it carries the per-layer embedding table; shard 1 goes to `--native`. Read
[the two things that cost us the most](#the-two-things-that-cost-us-the-most) before substituting
a different quantization — a merged single-file variant of this same model loads, passes every
integrity check, and then emits one token forever.

`fetch-model.sh` downloads all four, verifies each against the mirror's own `Content-Length`, and
refuses to continue if the PLE shard is the wrong shape.

### What the model is

- **Qwen3.8-Flash-Next** — [Qwen](https://huggingface.co/Qwen/Qwen3.8-Flash-Next), 125B
  parameters of mixture-of-experts across 48 layers, 24,576 experts, roughly 6B active per
  token. Text and image input, MTP head for speculative drafting.

- **Swift 1.5** — [UkisAI](https://ukisai.com/swift-1-5-flash-next)'s reasoning-efficiency
  derivative of the base. On their published comparison it emits 63.4% fewer thinking tokens at
  roughly 1.8× the speed with accuracy loss under 1% at the highest reasoning setting. That
  property is most of why this build is pleasant to serve: **a model that thinks in fewer tokens
  is faster at the same decode rate**, and it is why the reasoning budget here is left at the
  model's default rather than clamped down. The upstream specifies 89.6% on GPQA-Diamond at
  `xhigh`; we have not reproduced that and publish no accuracy figure of our own.

- **GSQ-RCO** — the mixed-precision quantization profile: per-tensor bit allocation learned on
  this model, reusing allocation budgets from the ISTA-DASLab GSQ-RCO series.
  `IQ3_S` is the tier measured here. The upstream repository publishes four:
  `IQ3_XXS` 76.0 GB, `IQ3_S` 77.9 GB, `IQ2_XS` 68.2 GB, and an experimental `Q2_0` at 66.6 GB,
  each with a development KLD. We ran only `IQ3_S` and publish numbers for nothing else —
  **if your pair has less room than ours, those tiers are the lever, and the KLD column on the
  model card is the honest way to pick one.**

- **Abliterated** — the derivation matters and is unusual, so: the abliterated build
  ([SC117](https://huggingface.co/SC117/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF))
  transplants 144 tensors across all 48 layers by **byte-level GGUF-to-GGUF transfer**, taking
  those bytes from a ready-made uncensored release while leaving every GSQ scale untouched. File
  size moves under 0.2%, and the source reports Swift's reasoning efficiency surviving the
  transplant. The refusal behaviour is gone; this is a behaviourally different model from base
  Qwen3.8-Flash-Next, and the practical consequence for serving is that nothing filters a
  request on the way in.

**Licence, and read this before deploying commercially:** the base **Qwen Community License 1.0**
carries over, and **Swift Open License v1.0** governs the derivative — commercial use is free
only below US$1M annual revenue. Model files are downloaded at first run and are not part of this
repository; nothing here grants any right to the weights. See [NOTICE](NOTICE).

### Tiers, for smaller cards

The upstream publishes the same series in four quantizations with published KLD. We measured
`IQ3_S` only:

| Tier | Two-shard total | Development KLD |
| --- | --: | --: |
| `IQ3_XXS` | 76.0 GB | 0.240 |
| **`IQ3_S`** (measured here) | **77.9 GB** | — |
| `IQ2_XS` | 68.2 GB | 0.341 |
| `Q2_0` (experimental) | 66.6 GB | 0.424 |

## Performance

Two Tesla V100-PCIE-32G, Xeon E5-2673 v3 (48 threads), 125 GiB RAM, CentOS Stream 9,
driver 580.159.04, CUDA 12.4, engine built from the V100 fork at `310e5cb`, **context 524,288**,
`--spec 4 --spec-min-p 0.70 --kv int8 --kv-resident 20480`, the on-disk conversation cache and
the pre-filled expert profile. Measured through the OpenAI API on loopback, from the same machine.

**Every figure comes from the engine's own accounting** — `/metrics` reports `prompt_ms`,
`decode_ms`, `ttft_ms`, `prompt_read` and `prompt_total` per request, so prompt processing and
decoding are separated rather than blended into one wall-clock number. `tools/bench.py` does the
reading; the method is described at the top of that file and the reasons it matters are in
[What we did not measure](#what-we-did-not-measure).

### Decode

512-token outputs, 12 runs each, the first 2 discarded, median with the interquartile range:

| | Median | IQR | Range | Reply length |
| --- | --: | --: | --: | --: |
| Prose | **74.1 tok/s** | 2.7 | 70.3 – 75.7 | ~1,600 chars |
| Code | **100.0 tok/s** | 2.9 | 98.0 – 104.6 | ~2,300 chars |

**Read the IQR before reading the median.** 2.7 tok/s of spread on a 74.1 median is 3.6%. Any
single-run comparison between two configurations on this hardware is noise unless it clears that
band.

The gap between prose and code is real and reproducible: the n-gram/PLE table is read once per
token, and code tokenizes into a much more scattered distribution — so more distinct table rows
are touched, and the difference between reading them from SSD and finding them cached is larger.
Prose reuses rows far more. It is the reason `--ple-io` is set at all, and the reason its effect
is workload-dependent rather than a flat percentage.

Concurrency, aggregate and per-request (1,024-token outputs, all requests identical):

| Concurrent | Wall clock | Aggregate | Per request | Speed-up |
| --: | --: | --: | --: | --: |
| 4 | 57.1 s | 71.7 tok/s | 76.5 – 81.9 tok/s | **0.90×** |

Aggregate is flat and the speed-up is **below 1** — the engine serialises requests. Per-request
speed does not collapse the way it does under `--batch` (see
[below](#5---batch-concurrency-is-a-loss-here-and-we-can-show-why)); each request still decodes at
its full rate, in the order it was sent. A single-request decode rate of ~78–100 tok/s *is* the
ceiling here, so four requests buy no throughput over queueing them.

**An earlier version of this section claimed a 2.48× speed-up.** That measurement used replies of
300–400 tokens, which finish early enough that the queue looks parallel. On 1,024-token replies the
ratio is 0.90×. The 2.48× was a measurement artefact, and it is recorded rather than deleted
because the same trap is easy to fall into: **a concurrency test must run every request to a full
long output before the wall clocks are compared.**

### Prefill

Prompt processing, from the engine's own `prompt_read` over its `prompt_ms`, **cold** — a fresh
prompt for every row, because a repeat is served from the conversation cache and reports a
figure that means nothing:

| Prompt | Tokens read | Engine prompt time | Throughput |
| --: | --: | --: | --: |
| ~256 | 315 | 1,736 ms | 181.5 tok/s |
| ~1K | 1,004 | 1,892 ms | 530.8 tok/s |
| ~4K | 3,707 | 3,425 ms | 1,082.3 tok/s |
| ~16K | 14,572 | 8,653 ms | **1,684.1 tok/s** |

Throughput climbs with length up to ~16K: the fixed cost of starting a request (about 1.7 s,
dominating the first row) is amortised as the prompt grows. The ~32K row is deliberately absent
from this table — the engine served it partly from cache and read only 12,657 of 29,041 tokens,
so its 1,036.5 tok/s is a cache artefact and not comparable. A cold 32K measurement needs the
cache disabled, which is a change to the configuration rather than to the benchmark.

**These prefill numbers are lower than an earlier draft of this file claimed.** That draft derived
throughput from client wall-clock and mixed in decode time and cache hits; it published 5,577
and 9,270 tok/s for 16K and 32K prompts, which are not physically available from these cards.
The engine's own counters give the figures above. If you see a double-digit-thousands prefill
number from a single-V100-class setup, suspect the measurement before believing the hardware.

### Long context

Needle retrieval — one sentence planted at the midpoint of filler, asked to quote it exactly.
The reply is quoted verbatim from the reply field, including the model's visible reasoning:

| Document | Actual prompt | Result | Prompt time |
| --: | --: | :-: | --: |
| ~8K | 7,621 tok | **found** | 2,088 ms |
| ~32K | 30,229 tok | **found** | 5,716 ms |
| ~131K | 120,665 tok | **found** | 62,734 ms |
| ~262K | 241,255 tok | **found** | 17,813 ms |

**All four depths retrieve correctly**, including a 241,255-token prompt — the deepest test this
repository runs, and past the model's native 256K window. That last row's prompt time is *lower*
than the row above it because it was partly served from the conversation cache after the 120K
run; treat it as a correctness result, not a throughput one.

512K is the configured ceiling. The largest **cold** retrieval tested is 120,665 tokens; we have
not run a full 512K-token recall test, and say so in
[What we did not measure](#what-we-did-not-measure).

### Prompt reuse

The same 3,684-token prompt, sent three times:

| Send | Engine prompt time | Client wall clock |
| --: | --: | --: |
| First | 4,258 ms | 5,138 ms |
| Second | **52 ms** | 572 ms |
| Third | **47 ms** | 586 ms |

An **82× reduction in prompt processing time** on the second send, because the KV state is
already there. This is what makes multi-turn conversation cheap: an append-only session pays
prefill once and then reads back.

The on-disk conversation cache extends that across a **restart** — `--conversation-cache-disk`,
25 GiB here. A parked 3,444-token conversation came back in 984.5 ms against 3,089 ms cold
(3.1×), and the disk record was 843 MB.

### Streaming

| | |
| --: | --: |
| Chunk interval p50 | 20 ms |
| Chunk interval p95 | **37 ms** |
| 2,048-token reply, wall clock | 34.2 s |
| Chunks delivered | 2,047 |
| Characters in the reply | 8,079 |

The p95 of 37 ms is the number that matters for perceived smoothness: it is the slowest 5% of
inter-token gaps, and it stays inside a comfortable reading budget. The 2,047 chunks for an
8,079-character reply is roughly 4 characters per chunk, which is the MTP draft being emitted in
batches rather than one token at a time.

These interval figures are measured from the client, because they describe what a client
experiences. They are **not** the decode rate: the same run's engine-side decode rate is the
64.4 tok/s in the table above, and the difference between the two is the reasoning trace, which
arrives in `reasoning_content` and inflates the chunk count without adding answer text.

### Vision

The model's own encoder, on the GPU, `--vram-reserve-mib 700`. A generated test card reading
`STRATA 512K` with a red-outlined rectangle and a filled blue ellipse comes back described
literally:

> **Text:** The string "STRATA 512K" appears in black, uppercase, sans-serif characters,
> left-aligned inside the rectangle

OCR of a synthetic image is a **smoke test, not a benchmark**. It tells you the encoder binary
ran, the image reached the model, and the text came back — it says nothing about accuracy on a
real photograph, and we publish no vision accuracy figure.

## Why these numbers

Six things decided the decode rate, in order of how much they mattered. Each was measured;
several of them are counter-intuitive.

### 0. `--ple-io mmap` — the largest single change, and it is not a flag anyone reaches for

The 28.8 GiB shard 2 is the n-gram/PLE table, read once per token. The engine's default
(`direct`) reads it unbuffered from SSD and keeps it out of RAM entirely. Mapping it into the
page cache — `--ple-io mmap`, without locking — is worth **+27 tok/s on code** and no measurable
memory cost. The full three-arm comparison, and why the obvious `ram` variant is a net loss, is in
[docs/TUNING.md](docs/TUNING.md). This one leads the list because it moved the code path more than
every draft-window and KV setting combined.

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
| `--kv-resident` | optional | **required** |

Measured decode at the two windows sits inside the run-to-run spread (the 256K configuration
measured 59.1 tok/s in one session and 63–66 in another; 512K measured 64.4 median with an IQR of
4.5). **We therefore do not claim a decode cost for 512K** — on this hardware the difference is
within noise, and asserting one from a single session would be exactly the mistake this
repository warns about elsewhere. What 512K demonstrably costs is expert slots, and that is a
memory fact rather than a speed claim.

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
  cache — a third-party 16 GB SXM2 pair measured 59–61 tok/s decode against our 64.4, and
  765 tok/s prefill at 14.5K against our 1,684, on effectively the same model and flags. **The
  32 GB pair is the recipe**; 16 GB cards are the budget version. See
  [docs/COMPARISON.md](docs/COMPARISON.md).
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
git clone https://github.com/ZackO2o/Strata-Qwen3.8-Flash-Next-512k-v100x2.git
cd Strata-Qwen3.8-Flash-Next-512k-v100x2
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
| `SPEC` | `4` | Draft window. Higher is *slower* past 4 — see [above](#1--spec-4-beats--spec-8-and--spec-min-p-070-beats-085). |
| `SPEC_MIN_P` | `0.70` | Draft acceptance floor |
| `KV` | `int8` | KV storage. `fp16` costs VRAM, `q4_0` is 6% **slower** — the dequantization outweighs the memory saved |
| `KV_RESIDENT` | `20480` | KV tokens kept in pinned host RAM. Required at 512K |
| `PLE_IO` | `mmap` | n-gram/PLE table read path. `mmap` keeps hot rows cached without pinning; `ram` locks 26.8 GiB and is a net loss — see [TUNING.md](docs/TUNING.md) |
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

- **Full 512K-token recall.** Our deepest needle test is 241,255 tokens, and it retrieved
  correctly. 512K is configured and the model loads and answers at it; recall at the very top of
  the window is untested.
- **Cold prefill beyond 16K.** The 32K row in the prefill table is contaminated by a
  conversation-cache hit (the engine read 12,657 of 29,041 tokens). Every prefill figure quoted
  as a throughput claim in this repository is from a cold prompt of 16K or less.
- **Quality benchmarks.** No GSM8K, no HumanEval, no MMLU. We did not measure accuracy, and a
  `Q2/Q3`-class quantization is exactly where you would expect to find the cost of this recipe.
  Treat decode and prefill numbers here as safe to quote and quality numbers as absent.
- **Concurrency beyond 4.** We tested 1, 2 and 4 and stopped when the aggregate proved flat.
  Nothing here tells you what happens at 16.
- **Long-run stability.** These are benchmark figures from a live server. We have not run a
  multi-day soak.
- **Anything about a third card.** Untested here.

## Three ways this document was wrong before it was right

All three errors were ours, all three produced confident and plausible numbers, and they are why
the benchmark reads the engine's counters instead of a stopwatch and why every concurrency figure
now runs to a full long output. Recording them because a reader deserves to know which figures
were once overstated.

1. **Prefill measured from client wall-clock, with cache hits mixed in.** The first version timed
   the HTTP call and divided by the prompt length. On a 32K prompt that had been partly cached it
   reported **9,270 tok/s** for 524,288-capable V100s. The engine's own `prompt_read`/`prompt_ms`
   says 1,684 tok/s at 16K cold. The published table now uses the engine's counters and a fresh
   prompt per row.

2. **Decode records matched by output-token count.** `/metrics` keeps a bounded list of recent
   requests. The first version looked up the entry whose `output_tokens` matched the reply it had
   just received — so ten runs that each produced exactly 512 tokens all matched the *same* older
   record. The result was ten identical "measurements", a median with an IQR of literally **0.0**,
   and a prefill figure that was one cached request repeated. A zero IQR on ten runs is not
   precision; it is a matching bug. Records are now matched on a request id plus a timestamp
   watermark, and `bench.py` documents both traps at the top.

3. **A concurrency speed-up measured on replies too short to reach the queue.** An interim
   write-up reported **2.48×** from four concurrent requests. Those requests produced 300–400
   tokens each and finished early enough that the aggregate looked parallel. Re-run with
   1,024-token replies the ratio is **0.90×** — the serialisation the engine's documentation
   describes. A concurrency test has to make every request run long enough to actually be in the
   queue at the same time; short replies measure the client's thread pool, not the server.

If you re-run this benchmark on your own pair and get an IQR of 0.0, you have hit the second bug,
not a perfectly stable machine.

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
