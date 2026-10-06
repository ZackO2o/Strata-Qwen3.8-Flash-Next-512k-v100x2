# Tuning log

What we tried, what it did, what we concluded. **The negative results are the useful part** —
they are what stops the next person spending a session on them.

Method, and it is not optional on this hardware: 12 runs per configuration, the first 2
discarded for cold-start and cache warm-up, **median reported with the IQR beside it**. One
variable changed at a time, restart between configurations.

The IQR is the point. The same configuration measured 20 minutes apart gave 71.4 and 66.2 tok/s.
On the final configuration the prose IQR is **4.5 tok/s on a 64.4 median** — 7%. Any effect
smaller than the IQR is noise, and an earlier version of this document drew conclusions from
5-run samples that a 12-run sample reversed.

---

## Winners

### `--spec 4` beats `--spec 8`

The draft head proposes `N` tokens and the engine verifies them in one forward. Deeper looks
better; it is worse, because a shallower draft has a much higher hit rate.

| `--spec` | Decode | Draft acceptance |
| --: | --: | --: |
| 8 | 72.9 tok/s | 61–65% |
| **4** | **77.8 tok/s** | **76–78%** |
| 6 | 74.2 tok/s | 72.8% |
| 12 | 67.8 tok/s | 60.2% |

Each verify forward yields more real tokens at 4 than at 8, even though it proposes fewer. This
survives the stricter measurement: **the published 512K configuration runs `--spec 4`.**

*Note for comparison:* the 16 GB reference build also runs `--spec 4`, so this is **not** the
source of any difference between the two deployments. Do not attribute our numbers to it.

### `--spec-min-p 0.70` beats higher thresholds

Counter-intuitive in the other direction: a *stricter* acceptance floor suppresses drafts, so it
raises the acceptance rate and *lowers* throughput.

| `--spec-min-p` | Decode | Acceptance |
| --: | --: | --: |
| **0.70** | **78.7 tok/s** | 75.9% |
| 0.50 | 76.7 tok/s | 65.0% |
| 0.85 | 73.8 tok/s | 82.4% |
| 0.95 | 68.9 tok/s | 89.3% |

An 89.3% acceptance rate is the *slowest* configuration tested. Acceptance rate is not the
objective; tokens per second is.

### `--kv int8` beats both alternatives

| `--kv` | Decode | GPU0 |
| --: | --: | --: |
| **int8** | **79.4 tok/s** | 27,756 MiB |
| fp16 | 76.6 tok/s | 28,996 MiB |
| q4_0 | 74.6 tok/s | 27,158 MiB |

`q4_0` saves memory and is **6% slower**: the Hadamard rotation and dequantisation cost more than
the saved bandwidth buys. `fp16` costs 1.2 GiB more and is 3% slower. int8 wins on both axes.

### The CJK draft vocabulary — largest silent win

The draft head may only propose tokens from a subset shipped as `<rt>/draft_vocab.bin`. The
engine's built-in default was built from English and code and covers **27 of the model's 55,328
Han tokens**, so Chinese answers draft almost nothing. No error, no warning, nothing in the logs.
The repository's `data/draft_vocab.bin` (425 KB, 106,299 ids) is the full-CJK subset:

```
cp data/draft_vocab.bin <rt_dir>/draft_vocab.bin
```

Effect on Chinese: draft acceptance went from near-zero to **~66%**. On overall tok/s the change
was smaller than the effect on acceptance suggests — which is itself the lesson, and the reason
the acceptance rate is reported beside the throughput everywhere in this repository.

### `--expert-profile-save` — for cold-start stability, not for speed

Requires the adaptive tier (`--adapt-every` > 0, `--adapt-swaps` > 0 and `--expert-profile`, all
defaults). It persists which experts are resident plus routing statistics, and reloads them at
start-up — the log shows `pre-filled 10240 of 10240 slots from the profile; slot 0 verified`.

Measured effect on throughput: **+3.8%, inside the noise band.** What it demonstrably fixes is
the first-request penalty — see below. Enable it for predictability, not for tok/s.

### `--kv-resident 20480` — required at 512K, not an optimisation

Keeps the KV state in pinned host RAM, freeing about 2 GiB of VRAM per card. At 256K it is a
nicety. At 512K it is the difference between the expert cache having slots and not having them.
Costs ~2 GiB of host RAM per card.

### `--conversation-cache-disk` — survives restarts

`--conversation-cache-disk <PATH>` plus `--conversation-cache-disk-gib 25`.

| | Engine prompt time |
| --: | --: |
| Cold | 3,089 ms |
| RAM hit | 285 ms (10.8×) |
| Disk restore, CPU work | 38.7 ms |
| Disk restore, disk read | 945.8 ms |
| Disk restore, total | **984.5 ms (3.1×)** |
| Same prompt, second send (live, final config) | **52 ms** |

The 52 ms figure is the one users feel: a repeat prompt costs essentially nothing. The disk tier
is what carries that across a process restart, where an in-process cache cannot.

---

## Losers

### `--batch` concurrency is a net loss

| Configuration | 4 requests | Aggregate | Per request |
| --: | --: | --: | --: |
| **No `--batch`** | 8.0 s | **53.5 tok/s** | 71–80 tok/s |
| `+ --kv-resident`, `--batch 2`, 512K | 9.5 s | 42.2 tok/s | 26–38 tok/s |
| `--batch 2`, 256K | 8.1 s | 49.6 tok/s | 29–39 tok/s |

The engine's own `docs/BATCHING.md` states the cause: *"Batch windows carry no MTP drafts."* A
slotted request decodes one token per forward instead of a verified draft batch, so per-request
speed collapses from ~77 to ~27 tok/s and the aggregate falls below plain queueing. On the final
512K configuration the aggregate is flat from 1 to 4 concurrent requests (60.3 / 63.1 / 62.3) —
**the single-request decode rate is the ceiling, and there is nothing for slots to recover.**
This trade only pays where experts fit entirely in VRAM and contexts are short.

### `--expert-cache-per-layer` — nothing to give

It fixes a real pathology: the upstream reports hit rates going from **2.97% to 70.4%**. On this
machine the cache already hits **99.4–99.8%** and the log says so on every reply. An optimisation
that targets hit rate cannot help a cache that is already hitting. What matters instead is **how
many slots fit**, which is what 512K competes for.

### Experimental native CUDA kernels — all inside the noise

| | Decode |
| --: | --: |
| `gr-native-mmvf` | 78.8 (IQR 7.8) |
| `native-moe-combine` | 76.9 |
| baseline | 76.8 |
| `native-gdn` | 76.5 |

Every one inside a 3 tok/s band whose own IQR is 7.8. `--no-fused-gr` did not start at all in
this build. Do not chase these.

### `--kv q4_0` — see the winners table

Listed twice on purpose: it is the most tempting memory saving on 512K and it costs 6% of
throughput.

---

## Method notes worth keeping

- **A parameter name that does not exist fails silently.** `--conversation-cache-disk-mib` is not
  a setting. The real ones are `--conversation-cache-disk <PATH>` and
  `--conversation-cache-disk-gib <N>`. Written wrongly: the cache does not work, no error is
  raised, and start-up looks perfect. Grep `--help` for the exact string.
- **Draft acceptance is not the objective.** The highest-acceptance configuration tested
  (89.3%) was the slowest. Report acceptance *beside* throughput, never instead of it.
- **A zero IQR is a bug, not precision.** If ten runs report identical throughput, the harness is
  matching the wrong record. Ours did.
