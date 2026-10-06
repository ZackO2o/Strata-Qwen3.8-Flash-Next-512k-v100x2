#!/usr/bin/env python3
"""Every performance number in the README, from one run against a live server.

    python3 tools/bench.py                       # everything
    python3 tools/bench.py --only decode         # one section
    PORT=8080 python3 tools/bench.py --json out.json

Method, which matters more than any single figure here:
  * 12 runs per decode configuration, the first 2 discarded (cold start and cache warm-up)
  * median reported, with the interquartile range beside it
  * one variable at a time; restart between configurations if you are A/B-ing
  * claim a difference only when it exceeds the IQR

Measured differences smaller than the spread are noise. A documented example: the same
configuration measured 20 minutes apart gave 71.4 and 66.2 tok/s — 7% from nothing.
"""
import argparse, base64, io, json, os, statistics, sys, threading, time
import urllib.request, urllib.error

API_KEY_FILE = os.environ.get("API_KEY_FILE", os.path.expanduser("~/.strata_api_key"))
PORT = os.environ.get("PORT", "8080")
BASE = os.environ.get("BASE_URL", f"http://127.0.0.1:{PORT}")
MODEL = os.environ.get("MODEL_NAME", "swift-1.5-flash-next-abliterated")


def load_key():
    if os.path.exists(API_KEY_FILE):
        return open(API_KEY_FILE).read().strip()
    return os.environ.get("STRATA_API_KEY", "")


KEY = load_key()

PROSE = ("Write a detailed technical essay of about 600 words explaining how mixture-of-experts "
         "language models route tokens, why expert caches matter, and how speculative decoding "
         "interacts with sparse activation. No bullet points, flowing prose only.")

CODE = ("Write a complete, production-quality Python module implementing an LRU cache with TTL "
        "expiry, thread safety, statistics, and a decorator interface. Include full type hints "
        "and a usage example at the end.")


def _post(payload, timeout=1800):
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    return urllib.request.urlopen(req, timeout=timeout)


def chat(prompt, max_tokens, temperature=0.7, timeout=1800):
    t0 = time.perf_counter()
    body = json.loads(_post({"messages": [{"role": "user", "content": prompt}],
                             "max_tokens": max_tokens, "temperature": temperature},
                            timeout=timeout).read())
    dt = (time.perf_counter() - t0) * 1000
    u = body.get("usage", {})
    return dt, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def filler_prompt(approx_tokens):
    unit = ("深度学习框架的性能优化涉及计算图调度、内存分配器、算子融合与量化策略。"
            "Strata engine uses a peer-tier expert cache over NVLink with MTP speculative decoding. "
            "Benchmark results depend on prompt length, KV residency and draft acceptance rate. ")
    return ("请仔细阅读以下技术材料，然后回答文末的问题。\n\n" + unit * max(1, approx_tokens // 60) +
            "\n\n问题：请总结以上材料的核心技术要点，并展开谈谈你的理解。")


def stream(prompt, max_tokens, temperature=0.7, timeout=1800):
    t0 = time.perf_counter(); ttft = None; inter = []; last = None; text = ""
    resp = _post({"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
                  "temperature": temperature, "stream": True}, timeout=timeout)
    for line in resp:
        if not line.startswith(b"data: "):
            continue
        pl = line[6:].strip()
        if pl == b"[DONE]":
            break
        try:
            d = json.loads(pl)["choices"][0].get("delta", {})
        except Exception:
            continue
        # Content only: reasoning deltas would inflate the count for thinking models.
        delta = d.get("content") or ""
        if not delta:
            continue
        now = time.perf_counter()
        if ttft is None:
            ttft = (now - t0) * 1000
        else:
            inter.append((now - last) * 1000)
        text += delta; last = now
    return dict(ttft_ms=round(ttft or 0, 1), total_ms=round((time.perf_counter() - t0) * 1000, 1),
                intervals=inter, text=text)


def stats(vals, drop=0):
    v = sorted(vals)[drop:]
    if len(v) < 2:
        return dict(n=len(v), median=round(v[0], 1) if v else 0)
    q = statistics.quantiles(v, n=4)
    return dict(n=len(v), median=round(statistics.median(v), 1),
                mean=round(statistics.mean(v), 1), iqr=round(q[2] - q[0], 1),
                min=round(v[0], 1), max=round(v[-1], 1))


# ── sections ─────────────────────────────────────────────────────────────────
def sec_prefill(R):
    print("\n== Prefill ==")
    R["prefill"] = []
    for size in (128, 1024, 4096, 16384, 32768):
        dt, ptok, _ = chat(filler_prompt(size), 8)
        e = dict(target=size, prompt_tok=ptok, ms=round(dt, 1), tok_s=round(ptok / dt * 1000, 1))
        R["prefill"].append(e)
        note = "  <- mostly fixed startup cost" if size <= 128 else ""
        print(f"  ~{size:>6} -> {ptok:>6} tok  {dt:8.0f} ms  {e['tok_s']:8.1f} tok/s{note}")


def sec_ttft(R):
    print("\n== TTFT (streaming) ==")
    R["ttft"] = []
    for size in (128, 4096, 16384):
        for i in range(2):
            s = stream(filler_prompt(size), 24)
            R["ttft"].append(dict(target=size, run=i, ttft_ms=s["ttft_ms"]))
            print(f"  ~{size:>6} #{i+1}: TTFT {s['ttft_ms']:8.0f} ms")


def sec_decode(R, n=12, mt=512):
    print(f"\n== Decode ({n} runs, first 2 discarded) ==")
    R["decode"] = {}
    for label, prompt in (("prose", PROSE), ("code", CODE)):
        vals = []; tl = []
        for i in range(n):
            dt, _, ctok = chat(prompt, mt)
            vals.append(ctok / dt * 1000); tl.append(ctok)
            if i < 2:
                print(f"  {label:>5} warm-up #{i+1}: {vals[-1]:6.1f} tok/s (discarded)")
        R["decode"][label] = stats(vals, drop=2)
        print(f"  {label:>5}: median {R['decode'][label]['median']:6.1f} tok/s  "
              f"IQR {R['decode'][label]['iqr']}  n={R['decode'][label]['n']}")


def sec_reuse(R):
    print("\n== Prompt reuse ==")
    R["reuse"] = []
    p = filler_prompt(4096)
    for i in range(3):
        dt, ptok, _ = chat(p, 32)
        R["reuse"].append(dict(run=i, ms=round(dt, 1), prompt_tok=ptok))
        print(f"  same {ptok}-tok prompt #{i+1}: {dt:8.0f} ms")


def sec_stream(R):
    print("\n== Streaming intervals (2048 out) ==")
    s = stream(PROSE, 2048)
    iv = s["intervals"]
    if not iv:
        R["stream"] = dict(error="no content deltas")
        print("  no content deltas — the reply may be all reasoning")
        return
    v = sorted(iv); n = len(v)
    R["stream"] = dict(chunks=n, ttft_ms=s["ttft_ms"], total_s=round(s["total_ms"] / 1000, 2),
                       p50=round(statistics.median(iv), 1), p95=round(v[int(n * .95)], 1),
                       mean=round(statistics.mean(iv), 1), chars=len(s["text"]))
    print(f"  chunks={n}  TTFT={s['ttft_ms']:.0f}ms  p50={R['stream']['p50']:.0f}ms  "
          f"p95={R['stream']['p95']:.0f}ms  total={R['stream']['total_s']}s")


def sec_concurrency(R, mt=512):
    print("\n== Concurrency sweep ==")
    R["concurrency"] = []
    for conc in (1, 2, 4):
        out = []
        def one():
            dt, _, ctok = chat(PROSE, mt)
            out.append((ctok, dt))
        t0 = time.perf_counter()
        ths = [threading.Thread(target=one) for _ in range(conc)]
        [t.start() for t in ths]; [t.join() for t in ths]
        wall = (time.perf_counter() - t0) * 1000
        per = [c / d * 1000 for c, d in out]
        e = dict(conc=conc, wall_ms=round(wall, 1), agg_tok_s=round(sum(c for c, _ in out) / wall * 1000, 1),
                 per_req=round(statistics.mean(per), 1), runs=[round(x, 1) for x in per])
        R["concurrency"].append(e)
        print(f"  {conc} in flight: wall {wall/1000:6.1f}s  aggregate {e['agg_tok_s']:6.1f} tok/s  "
              f"per request {e['per_req']:5.1f} tok/s")
        time.sleep(2)


def sec_needle(R, depths=(8192, 32768, 131072)):
    print("\n== Needle in a haystack ==")
    R["needle"] = []
    needle = "The secret access code is MANTIS-7742-QQ."
    for size in depths:
        filler = ("The following is archival meeting transcript material for reference purposes only. "
                  "It contains routine operational notes and carries no action items. ") * (size // 25)
        half = len(filler) // 2
        doc = filler[:half] + "\n\n" + needle + "\n\n" + filler[half:]
        prompt = ("[DOC]\n" + doc + "\n[/DOC]\n\nThe document above contains one access code. "
                  "Quote it exactly, character for character, and nothing else.")
        t0 = time.perf_counter()
        body = json.loads(_post({"messages": [{"role": "user", "content": prompt}],
                                 "max_tokens": 200, "temperature": 0.0}, timeout=1800).read())
        dt = time.perf_counter() - t0
        m = body["choices"][0]["message"]
        # A reasoning model may put the answer in either field; check both before declaring a miss.
        answer = ((m.get("content") or "") + " " + (m.get("reasoning_content") or "")).strip()
        found = "MANTIS-7742" in answer.upper()
        ptok = body.get("usage", {}).get("prompt_tokens", 0)
        R["needle"].append(dict(target=size, prompt_tok=ptok, ms=round(dt * 1000, 1), found=found))
        print(f"  ~{size:>7} (actual {ptok:>7}): {'found' if found else 'MISSED'}  {dt:6.1f}s")


def sec_vision(R):
    print("\n== Vision ==")
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (720, 260), "white"); d = ImageDraw.Draw(img)
        try:
            f = ImageFont.load_default(size=48)
        except Exception:
            f = None
        d.rectangle([20, 20, 700, 130], outline="red", width=6)
        d.text((45, 45), "STRATA 512K", fill="black", font=f)
        d.ellipse([520, 150, 690, 240], fill="blue")
        buf = io.BytesIO(); img.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        body = json.loads(_post({"messages": [{"role": "user", "content": [
            {"type": "text", "text": "What text and shapes do you see? Be literal and exact."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
            "max_tokens": 300, "temperature": 0.2}, timeout=600).read())
        ans = body["choices"][0]["message"].get("content") or ""
        ok = "STRATA" in ans.upper() and "512" in ans
        R["vision"] = dict(ok=ok, answer=ans[:400])
        print(f"  {'ok' if ok else 'FAIL'}  cell text read correctly: {ok}")
        print(f"  {ans[:160]!r}")
    except ImportError:
        print("  skipped: Pillow is not installed (pip install pillow)")
        R["vision"] = dict(skipped="no pillow")
    except Exception as e:
        print(f"  FAIL: {e}")
        R["vision"] = dict(ok=False, error=str(e))


SECTIONS = {"prefill": sec_prefill, "ttft": sec_ttft, "decode": sec_decode,
            "reuse": sec_reuse, "stream": sec_stream, "concurrency": sec_concurrency,
            "needle": sec_needle, "vision": sec_vision}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", choices=sorted(SECTIONS),
                    help="run only this section (repeatable)")
    ap.add_argument("--json", help="write the raw results here")
    ap.add_argument("-n", type=int, default=12, help="decode runs (default 12, first 2 discarded)")
    a = ap.parse_args()

    try:
        meta = json.loads(urllib.request.urlopen(
            urllib.request.Request(f"{BASE}/v1/models",
                                   headers={"Authorization": f"Bearer {KEY}"}), timeout=30).read())
        d = meta["data"][0]
        print(f"model {d['id']}  context {d['meta']['n_ctx']}  input {d['architecture']['input_modalities']}")
    except Exception as e:
        sys.exit(f"cannot reach {BASE} — is the server up and is the API key right?\n  {e}")

    R = {}
    for name in (a.only or list(SECTIONS)):
        try:
            if name == "decode":
                sec_decode(R, n=a.n)
            else:
                SECTIONS[name](R)
        except Exception as e:
            print(f"  section {name} failed: {e}")
            R[name] = dict(error=str(e))

    out = a.json or "/tmp/strata-bench.json"
    json.dump(R, open(out, "w"), indent=1, ensure_ascii=False)
    print(f"\nraw results -> {out}")


if __name__ == "__main__":
    main()
