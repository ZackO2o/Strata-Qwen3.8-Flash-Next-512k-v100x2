# Changelog

## v1.1 — the PLE I/O arm, and a concurrency claim withdrawn

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
