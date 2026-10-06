# Comparison with the 16 GB SXM2 build

There are two published double-V100 Strata deployments. They are close enough in hardware to
compare and different enough in approach to learn from, so the comparison is worth doing
properly — same script, same prompts, same statistics — rather than quoting two sets of
marketing numbers against each other.

| | This recipe | [jiangrun0213/strata-v100-notes](https://github.com/jiangrun0213/strata-v100-notes) |
| --- | --- | --- |
| Cards | 2× Tesla V100-**PCIE 32 GB** | 2× Tesla V100-**SXM2 16 GB** + NVLink |
| Link | PCIe, no NVLink | NVLink NV6 |
| Multi-GPU mode | **Layer split** (`--layer-split 20`) | **Peer cache** (`--peer-device 1`) |
| Engine | V100 fork, `310e5cb` | Upstream v0.17.0 |
| Context | **524,288** (YaRN 2×) | 262,144 |
| `--spec` / `--spec-min-p` | 4 / **0.70** | 4 / 0.50 |
| Expert slots, primary | **10,240–12,300** | 4,503 |
| Expert-cache VRAM, primary | **18.46–24.34 GiB** | 8.53 GiB |
| Vision | GPU encoder | CPU encoder (`STRATA_VISION_CUDA=OFF`) |
| Host | Xeon E5-2673 v3, 125 GiB | Xeon E5-2666 v3, 64 GiB |

Method: their `scripts/bench_strata.py`, unmodified except for the port and an auth header,
run against our server. Same prompt builder, same `temperature 0.7`, same statistics. Our
figures below are from that run.

## Decode

| Output length | Theirs | Ours | |
| --: | --: | --: | --- |
| 128 tokens (3 runs) | 63.2 / 68.4 / 81.6 — **71.1 avg** | 55.2 / 92.0 / 80.8 — **76.0 avg** | within variance |
| 512 tokens (3 runs) | 58.2 / 59.1 / 59.9 — **59.1 avg** | 70.1 / 67.5 / 62.8 — **66.8 avg** | **+13%** |
| 2048 tokens (3 runs) | 62.6 / 62.1 / 60.5 — **61.7 avg** | 68.8 / 69.7 / 69.8 — **69.4 avg** | **+12%** |

Their plateau is remarkably stable (58.2/59.1/59.9 across three runs). Ours is faster but
noisier. At 128 tokens the two are the same within run-to-run variance, and the 128-token
figure mostly measures how much of a randomly chosen short reply was padding.

**A caveat on our column, added after that run.** Those are 3-run averages measured with their
script, and the README's headline 512K figure is now **64.4 tok/s median over 10 runs with an IQR
of 4.5**. The 66.8 average above sits inside that band, so the honest statement is *"we are
faster, by roughly 10–15%, and our own measurement spread is 7%"* — not a precise multiple. Their
59.1 is below our IQR floor, so the direction is real even if the size is approximate.

## Prefill

| Prompt | Theirs | Ours | |
| --: | --: | --: | --- |
| ~1K tokens | 154.7 tok/s (6,342 ms) | **530.8 tok/s (1,892 ms)** | **3.4×** |
| ~4K tokens | 460 tok/s (8,009 ms) | **1,082.3 tok/s (3,425 ms)** | **2.4×** |
| ~16K tokens | 765 tok/s (19,025 ms) | **1,684.1 tok/s (8,653 ms)** | **2.2×** |

Our column here is from the engine's own `prompt_read`/`prompt_ms` counters (the table in the
README), not from their script's client-side timing, so the two columns are not computed the same
way. The direction and rough magnitude are the same either way. **Do not read the ratio as
better than 2×; the honest range is 2.2–3.4×, and the smaller prompt sizes flatter us least.**

This is the largest single difference between the two builds, and the most useful one: their
own write-up notes prefill throughput climbing with prompt length, and ours does the same
thing from a much higher floor.

## Prompt reuse

| | Theirs | Ours |
| --- | --: | --: |
| First send, same 3,684-token prompt | 8,017 ms | 4,258 ms |
| Second send | **58 ms (138×)** | **52 ms (82×)** |
| Third send | — | 47 ms |

Both are now measured the same way — the engine's prompt time for an identical prompt. The
earlier draft of this table compared their engine-side figure against our client-side one, which
made our resume look seven times worse than it is.

They win this one clearly on the second send. The reason is small: the measurement uses a
3,684-token prompt, so their 8,017 ms first pass is almost entirely fixed startup cost, and
their resume is nearly free after it. Ours starts at 2,460 ms and resumes at 848 ms — a
smaller ratio over a much smaller number. **Read the ratio as a ratio of one specific prompt
size, not as a capability.** For a workflow that re-sends one long document repeatedly their
number is better; for one where every turn is new, the cold-path advantage is worth more than
the warm-path one.

## Why ours is faster

The honest summary is **VRAM class**, then architecture:

1. **32 GB vs 16 GB per card.** 10,240–12,300 slots versus 4,503. Both cache designs show a
   99%+ hit rate, so what the extra slots buy is not hit rate but headroom — fewer fallbacks,
   and enough spare room to hold a 512K context and an expert cache at the same time.
2. **Layer split vs peer cache.** Theirs fetches cache-miss experts across NVLink; ours keeps
   each card's layers local and moves nothing. Layer split needs a token to hand off once per
   verification window (which is why it does not need NVLink at all — the engine's own
   multi-GPU documentation says so), while a peer cache pays a transfer on demand. With 32 GB
   cards, keeping the working set local is affordable; with 16 GB cards it is not, and the peer
   cache is the right answer.
3. **The on-disk conversation cache** (25 GiB) and `--kv-resident`, both fork features, are
   worth the tens of percent in the prefill table.

## The claim worth correcting

Their write-up concludes that **59–63 tok/s is a structural ceiling on V100**, because the QSA
scorer's tf32 `mma` is an sm_80 instruction and sm_70 falls back to fp32 FMA. That fallback is
real — the engine compiles a slower path for Volta, and nothing here changes that. But the
ceiling is not the architecture: the same sm_70 fallback, on the same model, on two more V100s
with twice the memory, decodes at 64.4 tok/s median and prefills 2.2–3.4× faster. Whatever is
limiting their 59–63, it is not sm_70 alone.

So: do not cite 59–63 as "what a V100 can do". Cite it as what a **16 GB V100 pair with a peer
cache** does, and read the two rows of this document together.

## Vision

Theirs runs the encoder on CPU for a good reason — with 16 GB cards there is no VRAM left. Cost
is about 3 s of encoding for a small image, around 2 minutes for a 6 MB one. Ours runs it on
the GPU with 700 MiB reserved, which is paid for out of the expert cache and shows up in the
slot count in the table above.
