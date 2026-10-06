# Patches

**This directory is empty, and that is the point at the current revisions.**

The V100 fork at `310e5cb` (upstream `6f32ec0` plus the fork's Volta work) builds sm_70 with the
two CMake switches in [../docs/BUILD_NOTES.md](../docs/BUILD_NOTES.md) and needs no source
changes:

```
-DSTRATA_ENABLE_CUDA=ON          # defaults to OFF, and with it off the `strata` target is never declared
-DSTRATA_EXPERIMENTAL_SM60=ON    # the community gate for sm_60 / sm_70
```

Earlier revisions did need patches. If you pin an older tag, these are the two that were
required — recorded so an old checkout has something to apply, and worth avoiding entirely by
building a recent commit instead.

## 1. `tools/strata_tokenizer.py` on Python 3.9

It calls `Path.write_text(newline=...)`, which is Python 3.10+. On 3.9 the packing step raises
`TypeError` *after* writing most of the pack, leaving a populated-looking directory the engine
cannot use. Drop the keyword argument. Do not upgrade the system interpreter for this.

## 2. `kvmem-gdn-replay-test` on CUDA 12.x

That target references CUDA-13-only symbols and will not compile on 12.x. Comment the target
out. It is a test binary and nothing depends on it.

**If a build fails on a target whose name ends in `-test`, apply this rather than chasing CUDA
headers** — no test target is needed to serve the model.

---

Apply from the source root with `git apply patches/<name>.patch`, or `patch -p1 < <name>.patch`.
