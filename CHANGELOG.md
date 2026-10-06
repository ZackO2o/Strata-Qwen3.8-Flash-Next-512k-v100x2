# Changelog

## v1.0 -- first publication

The complete recipe, and every number in the README measured on it.

**Configuration**
- 512K context (YaRN 2x over the model's native 256K), with a 256K configuration selectable by
  one command.
- `--spec 4 --spec-min-p 0.70`, chosen by measurement: the deeper `--spec 8` used by the
  reference benchmarks is **8% slower** here, because a shallower draft wins on acceptance rate.
- `--kv int8`, the fastest of int8 / fp16 / q4_0 on this hardware; `q4_0` is 6% slower.
- `--kv-resident 20480`, required at 512K rather than optional: without it the KV state takes
  the slots the expert cache needs.
- Layer split at 20 rather than a peer cache, measured against the published 16 GB build.
- The CJK draft vocabulary installed into the runtime directory -- the largest silent win in the
  recipe, and one `cp` that nothing warns you about.

**Published figures**
- Decode 64.1 tok/s aggregate at 1-4 concurrent; 71.3 tok/s prose and 69.4 tok/s code at 512
  tokens out.
- Prefill 1,611.8 tok/s at 14.5K, 1,862.6 tok/s at 29K.
- TTFT 88 ms warm; streaming interval p95 36 ms.
- Needle retrieval at 7.6K, 30K and 120K tokens.

**Deliberately not published**
- Any quality benchmark. There is no GSM8K or HumanEval here, and a Q2/Q3-class quantization is
  exactly where the cost of this recipe would show. The README says so in place, in a section
  rather than a footnote.
- Any 512K-token recall result. Serving at 512K; tested to 121K.

**Negative results recorded in full**, so nobody spends a session on them: `--batch` concurrency,
`--expert-cache-per-layer`, `--kv q4_0`, `--spec` 6/8/12, `--spec-min-p` 0.50/0.85/0.95, the
experimental native CUDA kernels, and a merged single-file model that passed every integrity
check and then emitted one token forever.

**Methodology**
- 12 runs, first 2 discarded, median with IQR. The same configuration measured twice, 20 minutes
  apart, differed by 7% -- larger than most of the effects being chased. `tools/bench.py` encodes
  the discipline so numbers are comparable across machines.
