#!/usr/bin/env python3
"""Long-context retrieval check, on its own.

    python3 tools/needle.py --depths 8192,32768,131072,262144
    python3 tools/needle.py --depths 524288 --answer-tokens 400

Checks both `content` and `reasoning_content` before calling a miss: a thinking model may put
the answer in either, and an early version of this script reported three false misses by
reading only the first field.

The deepest run here is the configured 512K only if your configuration says so — this script
does not assume a ceiling, but it does take prompt assembly time proportional to the depth.
"""
import argparse, json, os, sys, time
import urllib.request

API_KEY_FILE = os.environ.get("API_KEY_FILE", os.path.expanduser("~/.strata_api_key"))
PORT = os.environ.get("PORT", "8080")
BASE = os.environ.get("BASE_URL", f"http://127.0.0.1:{PORT}")
KEY = open(API_KEY_FILE).read().strip() if os.path.exists(API_KEY_FILE) else os.environ.get("STRATA_API_KEY", "")

NEEDLE = "The secret access code is MANTIS-7742-QQ."


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--depths", default="8192,32768,131072",
                    help="approximate prompt sizes in tokens, comma separated")
    ap.add_argument("--answer-tokens", type=int, default=200,
                    help="cap on the reply; a thinking model needs room to reason first")
    ap.add_argument("--timeout", type=float, default=1800)
    a = ap.parse_args()

    ok = True
    for size in (int(x) for x in a.depths.split(",")):
        filler = ("The following is archival meeting transcript material for reference purposes only. "
                  "It contains routine operational notes and carries no action items. ") * (size // 25)
        half = len(filler) // 2
        doc = filler[:half] + "\n\n" + NEEDLE + "\n\n" + filler[half:]
        prompt = ("[DOC]\n" + doc + "\n[/DOC]\n\nThe document above contains one access code. "
                  "Quote it exactly, character for character, and nothing else.")

        body = json.dumps({"messages": [{"role": "user", "content": prompt}],
                           "max_tokens": a.answer_tokens, "temperature": 0.0}).encode()
        req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {KEY}"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=a.timeout) as r:
                d = json.load(r)
        except Exception as e:
            print(f"  ~{size:>7}: request failed: {e}")
            ok = False
            continue
        dt = time.perf_counter() - t0

        m = d["choices"][0]["message"]
        answer = ((m.get("content") or "") + " " + (m.get("reasoning_content") or "")).strip()
        found = "MANTIS-7742" in answer.upper()
        ptok = d.get("usage", {}).get("prompt_tokens", 0)
        print(f"  ~{size:>7} (actual {ptok:>7}): {'FOUND' if found else 'MISSED'}  {dt:6.1f}s")
        if not found:
            ok = False
            print(f"      reply: {answer[:200]!r}")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
