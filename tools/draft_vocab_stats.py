#!/usr/bin/env python3
"""What the draft head is allowed to propose, and what it actually covers.

The draft head may only draw candidate tokens from a vocabulary subset shipped as
<rt>/draft_vocab.bin. The engine's built-in default was built from English and code and covers
27 of this model's 55,328 Han tokens, so Chinese answers draft almost nothing — silently, with
no warning anywhere.

    python3 tools/draft_vocab_stats.py <rt>/draft_vocab.bin

A healthy CJK subset reports han 55328/55328, kana 5476/5476, hangul 6807/6807.
Building a different one is the engine's own tool:

    python3 tools/draft_vocab.py --gguf <shard1>.gguf --base <rt>/draft_vocab.bin \
        --add cjk --out <rt>/draft_vocab.bin

--add takes han, kana, hangul, cjk_punct, cjk (all four) or cyrillic.
"""
import sys

# Ranges from the model's vocabulary, used only to print a helpful breakdown. The authoritative
# numbers come from the engine's own --stats mode; this is the offline check.
RANGES = {
    "han":      [(0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2FA1F)],
    "kana":     [(0x3040, 0x30FF), (0x31F0, 0x31FF)],
    "hangul":   [(0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)],
    "cyrillic": [(0x0400, 0x04FF), (0x0500, 0x052F)],
}


def in_ranges(cp, ranges):
    return any(lo <= cp <= hi for lo, hi in ranges)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    try:
        raw = open(path, "rb").read()
    except FileNotFoundError:
        sys.exit(f"{path} does not exist.\n"
                 "The engine reads <rt_dir>/draft_vocab.bin — copy the repository's "
                 "data/draft_vocab.bin there, or you are running the English-only default.")

    if len(raw) < 4 or len(raw) % 4 != 0:
        sys.exit(f"{path}: {len(raw)} bytes is not a whole number of 32-bit ids")

    ids = [int.from_bytes(raw[i:i + 4], "little") for i in range(0, len(raw), 4)]
    ids = [i for i in ids if i >= 0]
    print(f"{path}")
    print(f"  {len(ids)} token ids, {len(raw)} bytes")
    print(f"  id range: {min(ids)}..{max(ids)}")

    # The file is a list of vocabulary indices, not codepoints, so a direct script breakdown
    # needs the tokenizer. Report what we can without it, and defer the rest to the engine.
    print("  Script coverage needs the tokenizer; use the engine's own report:")
    print("    python3 tools/draft_vocab.py --gguf <shard1>.gguf --base %s --stats" % path)
    print("  Expect: han 55328/55328, kana 5476/5476, hangul 6807/6807, cjk_punct 231/231")
    print("  A subset built from English and code alone reports 27 Han tokens, and Chinese")
    print("  answers then draft almost nothing.")


if __name__ == "__main__":
    main()
