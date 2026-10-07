# Tuning log

What we tried, what it did, what we concluded. **The negative results are the useful part** —
they are what stops the next person spending a session on them.

Method, and it is not optional on this hardware: **interleave the arms you are comparing** (A, B,
A, B — never A to completion then B), 12 runs per arm, the first 2 discarded for cold-start and
cache warm-up, **median reported, with a reproduction round before the number is treated as a
baseline**. One variable changed at a time, restart between configurations.

Two reasons the protocol is this strict, both learned the hard way. First, the same configuration
measured twice gave 71.4 and 66.2 tok/s — 8% — so any effect smaller than the spread is noise, and
an earlier version of this document drew conclusions from 5-run samples that a 12-run sample
reversed. Second, and worse: comparing arms that were *not* interleaved lets the difference between
two runs of the machine be read as the difference between two configurations. **Several tables in
this file predate that rule and are marked as withdrawn where it matters.**

Reproduction is the other half. Once a configuration has a stable median, re-running it later
should land within ~5%; the current interleaved three-content baseline does (`84.8 / 80.8` for
Chinese prose, `82.8 / 79.3` for code, `70.9 / 68.3` for English prose, two rounds, same ordering).

---

## Winners

### Turn on the engine's own profiler before tuning anything

**The single highest-value thing in this document.** Strata 0.1.40 (and up) can break down its own
decode loop, and until you have that breakdown you are guessing about where time goes. Set the two
environment variables on the service and every request logs a line:

```bash
# systemd drop-in — do not edit the main unit
mkdir -p /etc/systemd/system/strata-v100.service.d
printf '[Service]\nEnvironment=STRATA_DECODE_TIMING=1\nEnvironment=STRATA_VERIFY_PROFILE=1\n' \
  > /etc/systemd/system/strata-v100.service.d/10-profiler.conf
systemctl daemon-reload && systemctl restart strata-v100
# then, after a few requests:
grep -a "decode timing" /root/engine-server.log | tail
```

Output, one line per request:

```
strata decode timing: 281 windows, avg T 2.04, 1.82 tokens/window, 25.80 ms/window
  = verify 23.56 (GPU-reach wait 0.00 + per-layer host 0.00 [plan 0.05 actq 0.07 jobs 0.00 CPU 0.51]
    + stage 0.06) + commit/emit 0.37 + draft 1.59
  per layer-window: CPU experts 0.10 (0.13 entries), VRAM hits 11.74, PCIe 0.01
```

That is the line that resolved this repository's decode investigation: `ms/token` is flat at
**12.0–15.7 ms**, the CPU expert pool is **0.5–4%** of window time, and PCIe is **0.01 ms**. Without
it, the numbers were being explained by inference and the inference was wrong.

**Remove the drop-in when you are done measuring.** The engine's own documentation says to use the
profiler to *compare*, not to measure speed — it costs rate. Leaving it on in production is a
silent performance tax:

```bash
rm /etc/systemd/system/strata-v100.service.d/10-profiler.conf
rmdir /etc/systemd/system/strata-v100.service.d
systemctl daemon-reload && systemctl restart strata-v100
```

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

### `--ple-io mmap` — why it is preferred, and why the number is gone

The 28.8 GiB shard 2 is the n-gram/PLE table, read once per token. `--ple-io` decides how that read
happens. `direct` does unbuffered SSD reads and deliberately keeps the table out of RAM and out of
the page cache — the engine's own help says so. `mmap` maps it into the page cache without locking
it, so rows in use stay resident and the rest remains reclaimable.

**This section previously published a three-arm table — `direct` 73.0/72.6, `ram` 75.8/98.8,
`mmap` 74.1/100.0 — and that table is withdrawn.** The arms were measured at three different
points in one service's lifetime, not interleaved, and this repository's later work found the
swing between measurement windows to be larger than the effect the table claimed. Whatever ordering
those arms have cannot be separated from when they happened to run. (An earlier revision of this
file put that swing at 2.35×; see [below](#correction-the-235-forward-variance-was-an-arithmetic-error)
— the swing was real at the time of measurement but the figure was computed with the wrong
denominator.)

**What is still defensible, in order of confidence:**

1. **`ram` is wrong.** Not because of its decode rate — because locking 26.8 GiB drives `MemFree`
   to **603 MB**, and the kernel then reclaims the process's *own* anonymous pages:

   ```
   VmSwap       1.45 GiB      allocstall_normal   172,228
   pswpin       309,450        compact_stall       324,567
   pgmajfault    73,578
   ```

   Those stalls lie about their cause: the engine's own decode rate stays normal during one, and a
   client sees tens of seconds of wall clock with nothing slow recorded on the device. **The stall
   is page reclaim, not compute.** These costs are structural — they do not depend on when the
   measurement ran.

2. **`mmap` over `direct` is preferred**, because `direct` pays an SSD read on every token's table
   lookup and `mmap` lets the kernel avoid most of them. The mechanism is clear; **the magnitude is
   not measured**, so no percentage is quoted.

`mmap` also costs nothing in memory: `VmLck 0`, `VmSwap 0`, and `VmRSS` splits as
`Anon 52.3 GiB + File 29.1 GiB` — the table is counted as clean file pages the kernel can drop for
free. Verify the mode took effect with:

```bash
grep -E "VmRSS|VmLck|VmSwap" /proc/$(pgrep -f "build/strata --serve" | head -1)/status
# mmap: VmLck 0 kB, VmSwap 0 kB   -- ram: VmLck ~26.8 GB
```

### Interleave your arms — and check the denominator before you believe a swing

Two separate corrections live here, and both cost a round of investigation. The rule that survives
is short: **interleave the arms of any A/B inside one service lifetime (A, B, A, B), and report a
median from a reproduction round.** Never run arm A to completion and then arm B.

**Correction: the "2.35× forward variance" was an arithmetic error.**

An earlier revision of this section reported that the duration of a single forward varied **2.35×**
and that this was the effect to watch. It was arrived at by normalising decode milliseconds by the
engine's `drafts_offered` counter:

| decode ms | tok/s | accept rate | "ms/forward" |
| --: | --: | --: | --: |
| 6,127 | 83.6 | 82.2% | **75.4** |
| 6,750 | 75.8 | 83.4% | 95.4 |
| 7,337 | 69.8 | 83.7% | 129.3 |
| 11,017 | 46.5 | 83.1% | **177.0** |

**`drafts_offered` counts verify *windows*, not forwards.** Deep acceptance means *fewer* windows,
so dividing by it inflates the quotient and manufactures a slowdown that is not there. The correct
per-token cost is flat: **12.0–15.7 ms** across seven profiled runs.

The engine will tell you this directly if you ask it. Set `STRATA_DECODE_TIMING=1` (0.1.40+, #610)
and each request logs its own breakdown:

```
strata decode timing: 281 windows, avg T 2.04, 1.82 tokens/window, 25.80 ms/window
  = verify 23.56 (GPU-reach wait 0.00 + per-layer host 0.00 [plan 0.05 actq 0.07 jobs 0.00 CPU 0.51]
    + stage 0.06) + commit/emit 0.37 + draft 1.59
  per layer-window: CPU experts 0.10 (0.13 entries), VRAM hits 11.74, PCIe 0.01
```

| tokens/window | ms/window | **ms/token** | CPU experts | VRAM hits |
| --: | --: | --: | --: | --: |
| 3.09 | 37.89 | **12.26** | 0.50 | 18.99 |
| 2.79 | 33.49 | **12.00** | 0.41 | 17.08 |
| 1.84 | 25.80 | **14.02** | 0.20 | 12.18 |
| 1.50 | 23.58 | **15.72** | 0.12 | 9.41 |

`ms/window` is **sub-linear** in tokens/window — ~23 ms fixed plus ~9 ms per accepted token — so
throughput is set by *tokens per window*, which is a property of the content, not of the machine.
The CPU expert pool is 0.5–4% of window time; PCIe is 0.01 ms. **Neither is a bottleneck, and there
is no drift to explain.**

**There is a second lesson in how long this took.** The 46–127 tok/s spread that started the
investigation came from comparing runs of **different content and different length** across
sessions. Twelve rounds of three content types, interleaved, in two separate runs:

| Content | Round 1 (median) | Round 2 (median) | Acceptance |
| --: | --: | --: | --: |
| prose (zh) | **84.8** | **80.8** | 78.8% |
| code | **82.8** | **79.3** | 83.0% |
| prose (en) | **70.9** | **68.3** | 81.5% |

Content-to-content spread is **1.2×**; the same content across rounds differs by ≤5%. Note the
ordering — Chinese prose beat code, against the usual expectation — and note that acceptance rate
does not predict tok/s. **Measure the rate for the load you actually serve rather than assuming
which workload is fastest.**

Hypotheses ruled out along the way (they were genuinely tested, and re-testing them is wasted
effort, but they are no longer evidence of an unknown cause):

| Hypothesis | Test | Result |
| --- | --- | --- |
| Chinese prompt is slower | EN/ZH interleaved, one process | 68.8 vs 67.7 — no (and reversed in a later interleaved run) |
| Draft acceptance collapses | engine counters, EN vs ZH | 78.1% vs 77.2% — no |
| Expert cache degrades | log line, both languages | 99.8% vs 99.9% — no |
| Conversation cache fills up | clean restart, slots empty | still 61–84 — no |
| GPU throttling | `nvidia-smi` under load | 44–47 °C, no throttle — no |
| PLE table not resident | pre-warmed all 26.82 GiB | no change — no |
| NUMA placement | `numactl --cpunodebind=0` | **worse** (49–69) — no |

The last one still carries a usable fact: **`--pool-affinity` has no "one NUMA node" mode** — only
`all` / `auto` / `p-cores` — so there is no supported way to stop workers crossing QPI, and pinning
the whole service to node 0 was slower.

**A related trap, from the report side:** this repository has carried decode figures from several
eras with different protocols. Comparing an old arm's *maximum* against a new arm's *median* reads
as a drop where there was a rise. Always state n, whether arms were interleaved, and whether the
figure is a median or a raw value — next to the number.

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
