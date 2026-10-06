# Tuning log

What we tried, what it did, and what we concluded. The negative results are the useful part —
they are what stops the next person repeating them.

All figures are 12 runs with the first 2 discarded, median reported, 256K context unless noted.
See [FAILURE_MODES.md](FAILURE_MODES.md#6-benchmark-noise-bigger-than-the-effect-you-are-chasing)
for why the method matters more than the numbers.

## Accepted

| Change | Effect | Why |
| --- | --- | --- |
| `--spec 8` → `--spec 4` | **+8%** (72.9 → 79.4) | Shallower draft, acceptance 61–65% → 76–78%. More real tokens per verify. |
| `--spec-min-p 0.50` → `0.70` | +2.6% (76.7 → 78.7) | 0.50 lets the draft get sloppy; 0.85 and 0.95 over-restrict and shrink the draft count. |
| CJK draft vocabulary into `rt/` | Chinese acceptance → 66.5% | The shipped default covers 27 of 55,328 Han tokens. |
| `--kv-resident 20480` | **+2 GiB VRAM per card** | KV moves to pinned host RAM. Mandatory at 512K. |
| `--conversation-cache-disk` | 3.1× cross-process, 10.8× in-process | Survives restarts, which in-process caching does not. |
| `--expert-profile-save` | stability, not speed | Pre-fills 10,240 of 10,240 slots from what the previous run learned. Measured +3.8%, inside the noise band — claimed as consistency, not throughput. |
| `--kv int8` | baseline | Best of the three (below). |

## Rejected

| Change | Effect | Why not |
| --- | --- | --- |
| `--kv q4_0` | **−6%** (79.4 → 74.6) | The Hadamard rotation and dequantization cost more than the 600 MiB they save. |
| `--kv fp16` | −3% (79.4 → 76.6) | Slower *and* 1.2 GiB more VRAM. |
| `--spec 12` | −15% (79.4 → 67.8) | Acceptance falls to 60%. Long drafts are mostly wasted. |
| `--spec 6` | −6% (79.4 → 74.2) | Worse than 4. |
| `--batch 2` | 4 requests: 53.5 → 42.2 tok/s aggregate | No MTP in slots; per-request 77 → 27 tok/s. |
| `--expert-cache-per-layer` | none | Fixes a 2.97%-hit-rate pathology. We are at 99.4–99.8%, so there is nothing to fix. |
| `--native-moe-combine` | within IQR | 76.9 |
| `--native-gdn` | within IQR | 76.5 |
| `--gr-native-mmvf` | within IQR | 78.8, IQR 7.8 — not a result |
| `--layer-split` 16 / 20 / 24 | within IQR | 67.7 / 71.4 / 71.9. 20 kept (it also fits the cache best). |
| `--kv-resident` at 256K | no gain | At 256K the KV fits without it; it only buys slot room when the context is huge. |

## Method notes

- **Discard the first two runs.** The first reply after a restart is consistently the slowest
  (63.9–67.7 tok/s against 72–74.5 for the ones after it) while the expert cache and the draft
  head warm up. Including it drags every median down by a few percent and makes configurations
  with slower warm-up look worse than they are.
- **Restart between configurations.** The adaptive expert tier learns from your requests. Run A
  changes what run B starts with, which is exactly how you get a difference you cannot reproduce.
  For byte-comparable runs the engine's own docs suggest `--adapt-every 1000000 --pcie-frac 0`.
- **Prefill and decode are separate measurements.** A change that speeds one often does nothing
  for the other; the Volta prompt kernels in the fork move prefill only, which the fork's own
  `docs/NVIDIA_V100.md` states outright.
- **Watch the expert-cache hit rate in the log, and stop tuning the cache if it is above 99%.**
  Every reply prints it. Below 99%, slot count and placement matter; above, they do not.
