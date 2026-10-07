# Changelog

## v1.3 — no drift to explain: ~80 tok/s was there all along

v1.2 concluded that this machine's decode rate swings by 2.35× inside a single forward, that the
engine's own counters could not see it, and that the cause was unidentified (leading candidates:
QPI contention between sockets, PCIe Gen 3 ×16 saturation). **That conclusion was wrong, and this
release withdraws it.**

**What the engine actually said.** Strata 0.1.40 can profile its own decode loop — set
`STRATA_DECODE_TIMING=1` (with `STRATA_VERIFY_PROFILE=1`, #610) and every request logs a per-window
breakdown:

```
strata decode timing: 281 windows, avg T 2.04, 1.82 tokens/window, 25.80 ms/window
  = verify 23.56 (GPU-reach wait 0.00 + per-layer host 0.00 [plan 0.05 actq 0.07 jobs 0.00 CPU 0.51]
    + stage 0.06) + commit/emit 0.37 + draft 1.59
  per layer-window: CPU experts 0.10 (0.13 entries), VRAM hits 11.74, PCIe 0.01
```

Across seven profiled runs:

| tokens/window | ms/window | **ms/token** | CPU experts | VRAM hits |
| --: | --: | --: | --: | --: |
| 3.09 | 37.89 | **12.26** | 0.50 | 18.99 |
| 2.79 | 33.49 | **12.00** | 0.41 | 17.08 |
| 2.66 | 33.65 | **12.65** | 0.49 | 16.73 |
| 1.84 | 25.80 | **14.02** | 0.20 | 12.18 |
| 1.59 | 24.06 | **15.13** | 0.22 | 9.86 |
| 1.50 | 23.58 | **15.72** | 0.12 | 9.41 |

**Where the 2.35× came from.** v1.2 normalised decode milliseconds by the engine's `drafts_offered`
counter and called the result "ms/forward":

| decode ms | tok/s | accept rate | "ms/forward" |
| --: | --: | --: | --: |
| 6,127 | 83.6 | 82.2% | 75.4 |
| 11,017 | 46.5 | 83.1% | **177.0** |

**`drafts_offered` counts verify *windows*, not forwards.** Deeper acceptance produces *fewer*
windows, so the quotient inflates as acceptance improves — a slowdown manufactured by the
denominator. `ms/token` (`ms/window ÷ tokens_per_window`) is flat at **12.0–15.7 ms**, and
`ms/window` is sub-linear in tokens/window (~23 ms fixed plus ~9 ms per accepted token).

**What actually moves the number: content.** Twelve rounds × three content types, interleaved,
first two dropped, run twice:

| Content | Round 1 (median) | Round 2 (median) | Acceptance |
| --: | --: | --: | --: |
| prose (zh) | **84.8** | **80.8** | 78.8% |
| code | **82.8** | **79.3** | 83.0% |
| prose (en) | **70.9** | **68.3** | 81.5% |

Content-to-content spread is **1.2×**; the same content across rounds differs ≤5% and the ordering
reproduced. Acceptance rate does *not* predict tok/s — the lowest-acceptance arm was fastest. Note
that the ordering is not the intuitive one (Chinese prose beat code), which is precisely why the
rate should be measured for the load you serve rather than assumed.

**Headline corrected to `~80 tok/s`**, from "65–105, expect ~70 from a cold start". The lower
figures in v1.2's band were real measurements of runs that were not comparable to each other.

**Nothing else in the recipe changes.** The bottlenecks that were measured and rejected — language,
draft acceptance, expert cache, conversation-cache fill, GPU throttling, PLE residency, NUMA
placement — remain settled and are still worth not re-testing. `--ple-io mmap` remains the
recommended arm for the reasons in v1.1 (it is structural: `ram` locks 26.8 GiB and drives the
kernel into reclaiming the process's own pages), still with **no percentage quoted**.

**And a reporting rule, since it cost a round here.** This repository has carried decode figures
from several eras under different protocols. Comparing an old arm's maximum against a new arm's
median reads as a drop where there was a rise. State n, whether the arms were interleaved, and
whether the figure is a median or a raw value — beside the number.

## v1.2 — the decode numbers were a window, not a property

v1.1 published **74.1 tok/s prose / 100.0 tok/s code** and attributed the code figure to
`--ple-io mmap`. Both need amending, and the more useful amendment is the second one: the spread
across sessions on this machine is larger than every tuning effect this repository documents.

**What the numbers actually do.** On one service, one configuration, one prompt, across a single
log:

| Output length | runs | median | best |
| --: | --: | --: | --: |
| <10 tok | 231 | 59.0 | 114.2 |
| 50–149 tok | 23 | 85.3 | 112.2 |
| 150–299 tok | 78 | 91.1 | 112.2 |
| 512+ tok | 517 | 77.1 | **127.0** |

Across one continuous service lifetime the same 512-token request ran **86–90 tok/s in the middle
segments and 70.8 at the end**. After a clean restart, 12 runs of the code prompt gave
**69.5, 70.3, 70.3, 70.3, 69.0, 68.7, 72.8, 71.3, 65.7, 73.7** — median ~70.3, not 100.

**So the decode figures belong in a band, not on a point.** Treat this configuration as
**65–105 tok/s on 512-token outputs**, with ~70 the number to expect from a cold start and 100+
reachable but not repeatable on demand. The published 100.0 was real when measured and is not
reproducible now, which makes it the wrong thing to have put in the headline.

**The `--ple-io` attribution is withdrawn.** The three-arm comparison (direct 73.0/72.6, ram
75.8/98.8, mmap 74.1/100.0) was measured at three different points in this service's lifetime,
**not interleaved**. Given the spread below, the ordering between those arms cannot be separated
from the drift between their measurement windows. What survives:

- **`ram` is still wrong**, for a reason that is not about speed: it pins 26.8 GiB, drives
  `MemFree` to 603 MB, and forces the kernel to reclaim the process's own pages (`VmSwap` 1.45
  GiB, `allocstall` 172k, `compact_stall` 325k). Those costs are structural and timing-independent.
- **`mmap` over `direct` is still defensible** — `direct` reads the 28.8 GiB table unbuffered from
  SSD on every token, `mmap` lets the kernel keep hot rows. But the size of the win is unmeasured,
  and **the honest statement is "prefer `mmap`", not "+37%"**.

**Where the 2.35× lives, and where it does not.** The engine's own per-request accounting isolates
it. Eight 512-token requests from one log, normalised by the engine's own `drafts_offered`:

| decode ms | tok/s | drafts offered | accepted | accept rate | ms/forward |
| --: | --: | --: | --: | --: | --: |
| 6,127 | 83.6 | 325 | 267 | 82.2% | **75.4** |
| 6,750 | 75.8 | 283 | 236 | 83.4% | 95.4 |
| 7,337 | 69.8 | 227 | 190 | 83.7% | 129.3 |
| 11,017 | 46.5 | 249 | 207 | 83.1% | **177.0** |

**Acceptance rate is flat at 82–83% and work per forward is flat — the length of a forward itself
varies 2.35×.** That is what the user-visible number tracks. Ruled out by measurement, one at a
time:

| Hypothesis | Test | Result |
| --- | --- | --- |
| Chinese prompt is slower | EN/ZH interleaved, same process | 68.8 vs 67.7 — no |
| Draft acceptance collapses | engine counters, EN vs ZH | 78.1% vs 77.2% — no |
| Expert cache degrades | log line, both languages | 99.8% vs 99.9% — no |
| Conversation cache fills up | clean restart, slots empty | still 61–84 — no |
| GPU throttling | `nvidia-smi` under load | 44–47 °C, no throttle — no |
| PLE table not resident | pre-warmed all 26.82 GiB | no change — no |
| NUMA placement | `numactl --cpunodebind=0` | **worse** (49–69) — no |

The last two are worth stating plainly because both are the intuitive fix and both failed. The
engine's `--pool-affinity` offers only `all` / `auto` / `p-cores` — there is **no "stay on one NUMA
node" mode** — and pinning the whole service to node 0 made it slower, not faster.

**Unresolved.** Why a forward takes 75 ms or 177 ms is not explained. The engine reports
`hit_rate 0.999` and `pcie_share 0.0` throughout. Remaining candidates are QPI contention between
the sockets (GPU0↔GPU1 is `SYS`, not P2P) and PCIe Gen 3 ×16 saturation — both consistent with
these measurements, neither isolated. **"Not yet explained" is more accurate than picking one.**

**Chinese prompts: the gap was real, the conclusion was wrong.** A first pass reported Chinese code
at 72.5 against English 100.0, i.e. −27.5%. That was confounded — the two runs sat at different
points in the service's lifetime against different cache states. Re-run interleaved in one process:

```
67.2  65.8  68.8  65.3  70.2  71.5  68.2  67.7  70.4  70.3
 EN1   ZH1   EN2   ZH2   EN3   ZH3   EN4   ZH4   EN5   ZH5
```

No systematic difference; both sit in the same band. **The −27.5% is withdrawn.** What is real is
that this repository had no Chinese prompts at all, and `tools/bench.py --lang zh` now supplies
them (`en` remains the default).

**Method change that follows from this.** Every A/B in this repository must **interleave its arms
within one service lifetime** rather than running arm A to completion and then arm B. The v1.0 and
v1.1 tables do not do this and should be read with that in mind. Recorded rather than quietly
repaired.

---

## v1.1 — the PLE I/O arm, and a concurrency claim withdrawn

> **Amended by v1.2.** The decode figures and the `--ple-io` attribution in this entry are
> withdrawn; see above. The concurrency correction and the negative sweep results stand, with the
> caveat that the sweep arms were not interleaved either.

Adds the one memory-side lever this configuration had left, and corrects an earlier concurrency
number that flattered the engine. The engine version and the hardware are unchanged; every figure
below is measured on the same pair of cards as v1.0.

**What changed**

- `--ple-io mmap` replaces the default `direct`. The 28.8 GiB n-gram/PLE table is now mapped into
  the page cache instead of being read unbuffered from SSD on every token, and — unlike
  `--ple-io ram` — it is not locked down, so the kernel can reclaim the parts that are not hot.
- Decode: prose **74.1 tok/s**, code **100.0 tok/s** (v1.0 published 64.4 for both). The code path
  is where the change lands; prose moves less.
- Concurrency: the v1.0 table reported aggregate throughput flat at ~62 tok/s from 1 to 4
  concurrent requests, and that stands — but a later short-reply run was briefly written up as a
  **2.48× speed-up**, which was an artefact of the replies being too short to fill the queue. With
  1024-token replies the ratio is **0.90–0.98×**, i.e. straight serialisation. The v1.0 wording was
  right; the interim claim was wrong and is recorded here so it is not rediscovered.

**All three `--ple-io` arms, measured** (12 runs, first 2 discarded, median with IQR; prose and
code prompts are the same pair used throughout this repository):

| `--ple-io` | Prose | Code | `VmLck` | `VmSwap` | `MemAvailable` |
| --- | --: | --: | --: | --: | --: |
| `direct` (default) | 73.0 | 72.6 | 0 | 0 | high |
| `ram` | 75.8 | 98.8 | **26.8 GiB** | 0.95 GiB | 34.5 GiB |
| **`mmap`** | **74.1** | **100.0** | **0** | **0** | **59.5–64 GiB** |

`ram` is the arm that locks the whole table: it buys the same class of decode improvement but
pins 26.8 GiB, drives `MemFree` to 603 MB, and the kernel then swaps out the process's own
anonymous pages (`VmSwap` 1.45 GiB, `allocstall` 172k, `compact_stall` 325k) — with occasional
tens-of-seconds stalls whose engine-side decode rate stays normal, because the stall is page
reclaim and not compute. `mmap` reaches the same place with `VmLck = 0`, zero swap, and 25 GiB
more headroom. **`mmap` is the arm to run.**

**Kernel tuning: tried, and it is a loss here.** Six `vm.*` and THP settings were applied to
relieve the memory pressure above (see [docs/TUNING.md](docs/TUNING.md)); with the most
conservative combination prose moved -2.2% (75.8 → 74.1) and code did not move. The one
individually clean win is `THP defrag=never`, which took `compact_stall` from a 325k running
total to an increment of **+1**. That is the only kernel setting this repository recommends, and
it is kept separate from the `--ple-io` decision on purpose: fixing the memory pressure at the
source (`mmap`) beats relieving it downstream.

**Swept and negative, this round** (each restarted, 12 runs, same harness — none exceeded the
default configuration):

| Setting | Prose | Code | vs. default |
| --- | --: | --: | --- |
| *default* | **77.0** | **103.1** | — |
| `--pcie-frac 0` / `0.25` / `0.5` | 74.7 / 76.4 / 75.5 | 99.6 / 99.8 / 101.4 | all below |
| `--pool-workers 12` / `16` / `23` | 76.0 / 77.0 / 76.0 | 100.0 / 100.4 / 98.2 | all below |
| `--host-core last` | 76.3 | 101.2 | below |
| `--lookup-chain 2` / `4` | 76.1 / 73.7 | 99.8 / 100.8 | below |

Ten non-default settings, none of them a win. The two the engine's own calibrator names as
"machine-dependent" (`--pcie-frac`, `--pool-workers`) are tuned on a 6-core consumer desktop; on
a homogeneous dual-socket Xeon with no E-cores the engine's defaults already win, and the
widely-quoted 3× gain from `--pool-workers` on hybrid CPUs does not transfer. `--host-core` is
documented as a Windows interrupt-distribution fix; `--lookup-chain` is opt-in and pays only when
the prompt contains long repeated spans.

**Unchanged from v1.0**: `--spec 4 --spec-min-p 0.70`, `--kv int8`, `--kv-resident 20480`,
`--layer-split 20`, the CJK draft vocabulary, 512K with YaRN 2×, expert cache at 10,240 slots
and a 99+% hit rate.

**Still not measured**: any quality benchmark, any vision accuracy figure, any 512K-token recall
test, anything about a third card, and any multi-day soak.

---

## v1.0 — first publication

The complete recipe, and every figure in the README measured on it at 512K.

**Configuration**
- 512K context (YaRN 2× over the model's native 256K), with a 256K configuration selectable by
  one command.
- `--spec 4 --spec-min-p 0.70`. The deeper `--spec 8` used by the reference benchmarks is **8%
  slower** here, because a shallower draft wins on acceptance rate.
- `--kv int8`, fastest of int8 / fp16 / q4_0. `q4_0` is 6% slower — the dequantisation costs more
  than the saved bandwidth buys.
- `--kv-resident 20480`, **required at 512K** rather than optional: without it the KV state takes
  the slots the expert cache needs.
- Layer split at 20 rather than a peer cache.
- The CJK draft vocabulary installed into the runtime directory — the largest silent win in the
  recipe, one `cp` that nothing warns you about, worth ~66% draft acceptance on Chinese.

**Published figures** (all from the engine's own `/metrics` counters)
- Decode **64.4 tok/s** median, prose and code, 512-token outputs; IQR 4.5, range 57.9–73.7.
- Concurrency 1/2/4 → aggregate 60.3 / 63.1 / 62.3 tok/s — flat; the engine serialises.
- Prefill **1,684.1 tok/s** at ~16K cold, 1,082.3 at ~4K.
- Prompt reuse **82×** on a repeat (4,258 ms → 52 ms); 3.1× across a restart via the disk cache.
- Retrieval correct at 8K, 30K, 121K and **241K tokens**.
- Streaming p95 interval 37 ms.
- Vision wiring verified with a synthetic card.

**Corrected before publication.** Two measurement bugs are documented in the README under
"Two ways this document was wrong before it was right": prefill derived from client wall-clock
with cache hits mixed in (published 9,270 tok/s; the engine's counters say 1,684), and decode
records matched by output-token count, which returned one old record ten times and produced an
IQR of 0.0. Both fixes are encoded in `tools/bench.py`, which now matches records on a request id
and a timestamp watermark.

**Deliberately not published**
- Any quality benchmark. No GSM8K, no HumanEval, no MMLU. A Q2/Q3-class quantization is exactly
  where the cost of this recipe would show. Stated in place, in a section, not a footnote.
- Any vision accuracy figure. The vision test is a wiring check.
- Any 512K-token recall result. Serving at 512K; retrieval tested to 241K.
- Any claim that 512K costs decode speed. Measured differences between 256K and 512K sit inside
  the IQR, so no claim is made.

**Negative results recorded in full**, so nobody spends a session on them: `--batch` concurrency,
`--expert-cache-per-layer`, `--kv q4_0`, `--spec` 6/8/12, `--spec-min-p` 0.50/0.85/0.95, the
experimental native CUDA kernels, and a merged single-file model that passed every integrity
check and then emitted one token forever.
