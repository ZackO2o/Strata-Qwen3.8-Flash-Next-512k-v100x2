# Changelog

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
