#!/usr/bin/env python3
"""Check the PLE shard before you spend a session debugging the wrong thing.

The engine reads the per-layer embedding table from the START of the file passed to --ple-gguf.
In the standard release that file is shard 2 of 2 and holds exactly one tensor at offset 192.
A merged single-file variant can hold the same tensor 2.5 GB in, with the output head at the
front — and it will load, pass a bit-exact kernel cross-check, show 100% draft acceptance, and
then emit the same token for every reply, because the engine reads the output head as a PLE
table and the residual stream collapses.

None of the toolchain's integrity checks test this. This one does.

    python3 tools/check_ple_shard.py <shard2>.gguf [--llama-dir /path/to/llama.cpp]
"""
import argparse, os, sys


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shard")
    ap.add_argument("--llama-dir", default=os.environ.get("LLAMA_DIR", "/data/build/llama.cpp"),
                    help="a llama.cpp checkout containing gguf-py")
    ap.add_argument("--expect-bytes", type=int, default=None,
                    help="the model's PLE table size in bytes (28.8 GB for this family)")
    a = ap.parse_args()

    sys.path.insert(0, os.path.join(a.llama_dir, "gguf-py"))
    try:
        from gguf import GGUFReader
    except ImportError:
        sys.exit(f"cannot import gguf from {a.llama_dir}/gguf-py — pass --llama-dir")

    r = GGUFReader(a.shard)
    print(f"{a.shard}")
    print(f"  {len(r.tensors)} tensor(s)")
    for t in r.tensors:
        print(f"    {t.name}  offset={t.data_offset}  bytes={t.n_bytes}  {t.tensor_type}")

    bad = []
    if len(r.tensors) != 1:
        bad.append(f"expected exactly 1 tensor, found {len(r.tensors)}")
    t = r.tensors[0] if r.tensors else None
    if t is not None:
        if t.data_offset > 4096:
            bad.append(f"the table starts at offset {t.data_offset}, not at the front of the file")
        if a.expect_bytes and t.n_bytes != a.expect_bytes:
            bad.append(f"tensor is {t.n_bytes} bytes, expected {a.expect_bytes}")

    if bad:
        print("\n  FAIL — the engine will misread this file:")
        for b in bad:
            print(f"    - {b}")
        print("\n  Use the two-shard release, with shard 2 passed to --ple-gguf.")
        sys.exit(1)

    print("\n  ok — single tensor at the start of the file")
    if a.expect_bytes:
        print(f"  ok — size matches ({a.expect_bytes} bytes)")


if __name__ == "__main__":
    main()
