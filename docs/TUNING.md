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

### `--ple-io mmap` — the RAM-side lever, and why not `ram`

The 28.8 GiB shard 2 is the n-gram/PLE table, read once per token. `--ple-io` decides how that
read happens, and it turned out to be the only memory-side setting with room left on this
configuration:

| `--ple-io` | Prose | Code | `VmLck` | `VmSwap` | `MemAvailable` |
| --- | --: | --: | --: | --: | --: |
| `direct` (engine default) | 73.0 | 72.6 | 0 | 0 | high |
| `ram` | 75.8 | 98.8 | **26.8 GiB** | 0.95 GiB | 34.5 GiB |
| **`mmap`** | **74.1** | **100.0** | **0** | **0** | **59.5–64 GiB** |

`direct` does unbuffered SSD reads and deliberately keeps the table out of RAM and out of the
page cache — the engine's own help says so. Reading from an SSD on every token is what costs the
code path its ~27 tok/s; prose reuses rows heavily and barely notices.

`ram` is the intuitive fix — map the table and lock the whole thing — and it works, right up
until the memory accounting. Pinning 26.8 GiB drives `MemFree` to **603 MB**; the kernel then
starts reclaiming the process's *own* anonymous pages, and the counters say so:

```
VmSwap       1.45 GiB      allocstall_normal   172,228
pswpin       309,450        compact_stall       324,567
pgmajfault    73,578
```

Those stalls are worth understanding because they lie about their cause: during a stall the
engine's own decode rate stays normal, and a client sees tens of seconds of wall clock with no
slowdown recorded on the device. **The stall is page reclaim, not compute.**

`mmap` gets the same class of decode improvement without any of that. It maps the table into the
page cache and does not lock it, so the rows that are being used stay resident and the rest is
reclaimable under pressure. `VmRSS` splits as `Anon 52.3 GiB + File 29.1 GiB` — the table is
counted as clean file pages, which the kernel can drop for free instead of swapping.

**Net: `mmap` ≈ `ram` on decode, ±25 GiB on memory, and it removes the stalls entirely.** The
repository ships `PLE_IO=mmap`. Verify the mode took effect with:

```bash
grep -E "VmRSS|VmLck|VmSwap" /proc/$(pgrep -f "build/strata --serve" | head -1)/status
# mmap: VmLck 0 kB, VmSwap 0 kB   -- ram: VmLck ~26.8 GB
```

### Kernel `vm.*` tuning — measured, and a loss here

With `ram` pinning the table the pressure looked like something the kernel could be tuned out of,
so six settings were swept (`min_free_kbytes`, `watermark_scale_factor`, `swappiness`,
`THP defrag`, `vfs_cache_pressure`, `zone_reclaim_mode`). Best combination moved prose **-2.2%**
and code not at all. Two traps worth recording:

- **`watermark_scale_factor=200` makes things worse, not better.** It scales the watermarks by a
  percentage of the zone; on this 60 GiB zone it put `low` at **2 GB**, above `MemFree`, so the
  kernel sat permanently below its own low watermark and reclaimed continuously. If you touch it,
  **25 is the practical ceiling** here.
- **`swappiness=0` increases thrashing**, counter-intuitively: `pswpin` rose 59% (183k pages)
  because pages evicted under pressure are immediately needed back. Zero is not "never swap", it
  is "swap only under duress" — and this machine is under duress.

**The one clean win is `THP defrag=never`**, which took `compact_stall` from a 325k running total
to an increment of **+1** across a full benchmark run, with no downside measured. It is kept as a
separate recommendation exactly because the rest of the sweep is not worth having:

```bash
echo never > /sys/kernel/mm/transparent_hugepage/defrag
```

The general lesson: **the memory pressure here has a source (`ram` pinning 26.8 GiB) and tuning
the kernel only moves the cost around.** Fixing the source with `mmap` beat every kernel setting
tried, and by a wide margin.

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

### Calibrator settings — the defaults are right on homogeneous hardware

The engine's calibrator (`setup.sh --calibrate`, 0.1.19+) names `--pcie-frac`, `--spec-min-p` and
`--pool-workers` as the settings that depend more on the PC than on the model, and its defaults
were measured on a 6-core consumer desktop. There is a widely-quoted community result where
`--pool-workers` gave **3×** — on an Intel hybrid CPU, where E-cores were dragging the verify
window. Neither of those conditions holds here, and the defaults won every arm:

| Setting | Prose | Code | vs. default |
| --- | --: | --: | --- |
| *default* | **77.0** | **103.1** | — |
| `--pcie-frac 0` / `0.25` / `0.5` | 74.7 / 76.4 / 75.5 | 99.6 / 99.8 / 101.4 | all lower |
| `--pool-workers 12` / `16` / `23` | 76.0 / 77.0 / 76.0 | 100.0 / 100.4 / 98.2 | all lower |
| `--host-core last` | 76.3 | 101.2 | lower |
| `--lookup-chain 2` / `4` | 76.1 / 73.7 | 99.8 / 100.8 | lower |

`--host-core` exists because *Windows* routes a GPU's interrupts to one logical processor; there
is no equivalent problem on Linux. `--lookup-chain` is opt-in and only pays when the prompt
contains long repeated spans. **The two lessons: check whether a quoted speed-up came from a
hybrid CPU before chasing it, and do not shrink `--pool-workers` to one NUMA node's core count on
a dual-socket box** — the engine already handles the cross-node case, and the measurement says
cutting workers costs more than the crossing does.

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
