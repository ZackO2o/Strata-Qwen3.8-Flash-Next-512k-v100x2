# Failure modes

Every entry here is something that happened to us, that looked like something else, and that
cost real time. Ordered by how expensive.

---

## 1. Merged single-file model: loads clean, passes every check, emits garbage

**Symptom.** Loads without error. `canonical_xcheck.py` reports BIT-EXACT on the quantized
kernel. 100% draft acceptance. 115 tok/s. Every reply is the same token: `!` (token id 0).
Occasionally a normal English sentence appears, which is what makes it confusing.

**What we ruled out first, wrongly, in this order.** Quantization kernel. Pack index offsets.
`native_experts` offsets. PLE table location. Expert cache on/off. Single card vs two.
`--kv int8` vs `fp16`. `STRATA_BF16_TC=0`. `STRATA_ATTN_PRE75=0` with `STRATA_PROMPT_ATTN_OLD=1`.
None of it mattered.

**Cause.** Layout. The engine reads the PLE table from **the beginning of the file passed to
`--ple-gguf`**. In the standard release that file is shard 2 of 2 and contains exactly one
tensor, `per_layer_token_embd.weight`, at offset 192. The third-party merged file had that same
tensor at offset 2,548,131,776 with `output.weight` at the front. The engine read the output
head as a PLE table, the per-layer embeddings were wrong, and the residual stream collapsed
onto one token.

**Why the checks pass.** The file is not corrupt. The bytes are right, the header is right, the
quantized tensors are right, the pack index matches the GGUF offsets. Only the *convention* is
violated — and no check in the toolchain tests the convention.

**How to catch it in 30 seconds**, before downloading or after:

```python
import sys; sys.path.insert(0, "<llama.cpp>/gguf-py")
from gguf import GGUFReader
r = GGUFReader("<ple-shard>.gguf")
print("tensors:", len(r.tensors))
for t in r.tensors:
    print(" ", t.name, "offset=%d" % t.data_offset, "bytes=%d" % t.n_bytes, t.tensor_type)
```

Pass condition: **exactly 1 tensor, offset near 0, byte size equal to the model's PLE table
size** (28.8 GB for this family).

**The rule we now follow:** when output is nonsense but every metric looks healthy, swap in a
known-good release-shaped file *first* and re-run. It costs one download and it separates
"the file is wrong" from "the engine or flags are wrong" in one step. Debugging the suspect
file is what you do after that, not before.

---

## 2. Parameters that do not exist fail silently

`--conversation-cache-disk-mib` is **not a parameter**. The real ones are:

```
--conversation-cache-disk <PATH>
--conversation-cache-disk-gib <N>
```

Written with the invented name, the cache does not engage, the engine does not complain, and
start-up is clean. We only found it because the cache directory stayed empty.

Before trusting a remembered flag: `./build/strata serve --help | grep -A6 -- '--the-flag'`.
Some settings are **environment variables, not flags** (`STRATA_PREFILL_HELP`, `STRATA_SPLIT_OWN`,
`STRATA_STAGE_TRIM`, `STRATA_EXPERT_PAIR`), and passing one as a flag fails the start.

---

## 3. Engine exits before ready, with no error at all

**Symptom.** `engine exited before it was ready`, and the engine's log has nothing in it.

**Cause.** The launcher builds the child environment from the config's `lib_dirs` (which becomes
`LD_LIBRARY_PATH`) and `env` (custom variables). With `lib_dirs` missing, the engine cannot find
the CUDA libraries, dies instantly, and has nothing to say.

**Diagnosis.** Run the engine's arguments by hand, with `</dev/null`:

```bash
cd <strata> && <exe> serve <args...> </dev/null
```

Runs fine by hand, fails under the launcher → the environment is being lost in between, and the
fix is in `lib_dirs` / `env`, not in the arguments.

---

## 4. `--batch` does not make it faster

Covered in the README with numbers. The one-line version: batch slots carry no MTP drafts, so
per-request decode falls from ~77 to ~27 tok/s while aggregate throughput stays flat. If your
aggregate is already limited by single-request decode rate, slots have nothing to recover.

---

## 5. `--no-fused-gr` will not start on this build

It is an A/B arm that assumes a code path this fork does not have. The engine refuses. Not a
problem with your machine.

---

## 6. Benchmark noise bigger than the effect you are chasing

Same configuration, measured 20 minutes apart: **71.4 tok/s and 66.2 tok/s**. A 7% spread from
nothing. Most single-parameter differences are smaller than that, so a 5-run A/B will confidently
report effects that do not exist — we published one such wrong result before catching it.

What the numbers in this repository were measured with:

- **12 runs, first 2 discarded** (cold start and expert-cache warm-up)
- **median with interquartile range**, not mean
- one variable changed at a time, server restarted between
- **only claim a difference when it exceeds the IQR**

Under that rule, `--spec 4` (+8–13%) is real and the experimental native kernels (all inside the
IQR, 76.5–78.8) are not. `tools/bench.py` implements the discipline; use it rather than
re-inventing it.

---

## 7. Short-prompt throughput is mostly startup cost

A ~256-token prompt reports 181.5 tok/s and a ~16K prompt reports 1,684.1 tok/s. Neither number
is wrong; the first is nearly all fixed cost, about 1.7 s of it, and dividing a small token count
by a large constant inflates the ratio. **Never put the two in the same table** without saying
so, and never quote the short one as a throughput figure.

The same trap caught us harder in the other direction: a cached 29K prompt once reported
9,270 tok/s, because almost none of it was actually read. Prefill throughput is only meaningful
against `prompt_read` on a **cold** prompt, which is why the README's table uses a fresh prompt
per row and why the 32K row is omitted rather than published.

---

## 8. `torch_dtype`-style traps in the tooling

`tools/strata_tokenizer.py` uses `Path.write_text(newline=...)`, a Python 3.10+ parameter. On
Python 3.9 the pack step dies at the last stage after writing most of its output, so the pack
looks present and is not. Either use Python 3.10+, or drop the keyword argument. Do not switch
the system Python for this.
