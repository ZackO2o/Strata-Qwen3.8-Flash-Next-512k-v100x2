#!/usr/bin/env python3
"""Every performance number in the README, taken from the server's own accounting.

    python3 tools/bench.py                    # everything
    python3 tools/bench.py --only decode      # one section
    python3 tools/bench.py --lang zh          # Chinese prose/code prompts (CJK draft path)
    PORT=8080 python3 tools/bench.py --json out.json

Why the engine's numbers and not the client's:

  Wall-clock around an HTTP call measures the client's serialization, the socket and the
  model's thinking, all blended together. The engine already accounts for each phase exactly --
  `prompt_ms`, `decode_ms`, `ttft_ms`, `prompt_read`, `prompt_total`, `reused_tokens` -- and
  reports them per request in `/metrics` under `requests[]`. Reading them means the published
  figures are the engine's own measurements rather than ours, and prompt processing stays
  separate from decoding instead of being averaged into one number.

  `prefill_tok_s` is `prompt_read / prompt_ms`: tokens the engine actually recomputed over its
  own prompt time. That is why a cache hit legitimately reports a very high figure -- almost
  nothing was read -- and why the prefill table below uses a fresh prompt for every row.

Two traps this avoids, both of which produced wrong numbers in earlier drafts:

  1. A thinking model returns its chain of thought in `reasoning_content` and leaves `content`
     empty. A benchmark reading only `content` sees a blank reply and reports a miss, or a
     zero-length stream. Every read here falls back to `reasoning_content`.
  2. Prefill derived from a client's wall clock absorbs decode time and cache hits. A repeated
     prompt reported 5,577 tok/s in one run of the earlier script -- an artefact of the
     conversation cache, not a measurement.

Method, which matters more than any figure below:
  * 12 runs per decode configuration, the first 2 discarded (cold start, cache warm-up)
  * median with the interquartile range beside it
  * one variable at a time, a restart between configurations when A/B-ing
  * claim a difference only when it exceeds the IQR

A documented example of why: the same configuration measured 20 minutes apart gave 71.4 and
66.2 tok/s. That is 7% from nothing -- larger than most of the effects being chased here.
"""
import argparse, base64, io, json, os, statistics, sys, threading, time
import urllib.request

API_KEY_FILE = os.environ.get("API_KEY_FILE", os.path.expanduser("~/.strata_api_key"))
PORT = os.environ.get("PORT", "8080")
BASE = os.environ.get("BASE_URL", f"http://127.0.0.1:{PORT}")
MODEL = os.environ.get("MODEL_NAME", "swift-1.5-flash-next-abliterated")

KEY = (open(API_KEY_FILE).read().strip() if os.path.exists(API_KEY_FILE)
       else os.environ.get("STRATA_API_KEY", ""))

PROSE = ("Write a detailed technical essay of about 600 words explaining how mixture-of-experts "
         "language models route tokens, why expert caches matter, and how speculative decoding "
         "interacts with sparse activation. No bullet points, flowing prose only.")

CODE = ("Write a complete, production-quality Python module implementing an LRU cache with TTL "
        "expiry, thread safety, statistics, and a decorator interface. Include full type hints "
        "and a usage example at the end.")

# The same two tasks in Chinese. Not translations to be compared against the English pair as if
# they were the same measurement -- they are not, and the decode rate differs for a real reason:
# this model's MTP draft vocabulary only covers a subset of Han tokens unless `--add cjk` was run,
# and Chinese drafts far less well when it was not. See tools/draft_vocab_stats.py. Running both
# pairs is how that effect is measured rather than assumed.
PROSE_ZH = ("写一篇约一千字的技术散文，详细解释混合专家（MoE）语言模型如何路由 token、"
            "专家缓存为什么重要、以及投机解码与稀疏激活的相互作用。不要用要点列表，"
            "只写流畅的散文。")

CODE_ZH = ("写一个完整的、可用于生产环境的 Python 模块，实现一个带 TTL 过期、线程安全、"
           "统计功能和装饰器接口的 LRU 缓存。包含完整的类型标注，并在结尾给一个使用示例。")

LANG_PAIRS = {
    "en": (PROSE, CODE),
    "zh": (PROSE_ZH, CODE_ZH),
}


# ── plumbing ─────────────────────────────────────────────────────────────────
def _req(path, payload=None, timeout=1800, request_id=None):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"}
    if request_id:
        h["x-request-id"] = request_id
    return urllib.request.urlopen(urllib.request.Request(f"{BASE}{path}", data=data, headers=h),
                                  timeout=timeout)


def metrics(timeout=60):
    with _req("/metrics", timeout=timeout) as r:
        return json.load(r)


def text_of(msg):
    """A thinking model fills reasoning_content and leaves content empty."""
    return ((msg.get("content") or "") + " " + (msg.get("reasoning_content") or "")).strip()


def chat(prompt, max_tokens, temperature=0.7, timeout=1800):
    """One completion, with the engine's own per-request accounting attached.

    Matching the /metrics record is the fragile part, and getting it wrong silently produces
    plausible-but-wrong numbers: an earlier version matched on output token count alone, so ten
    runs that each produced 512 tokens all matched the *same* older record and reported its
    decode rate ten times over -- identical "medians" and a prefill figure that was one cached
    request repeated.

    The engine stamps each record with `time` (a unix timestamp). Snapshot the newest timestamp
    before the call, send the request with `x-request-id`, and take the record that is both
    newer than the snapshot and carries our id.
    """
    before = 0.0
    try:
        rs = metrics().get("requests", [])
        before = max((e.get("time", 0) for e in rs), default=0.0)
    except Exception:
        pass

    rid = f"bench-{time.time_ns()}"
    t0 = time.perf_counter()
    with _req("/v1/chat/completions",
              {"messages": [{"role": "user", "content": prompt}],
               "max_tokens": max_tokens, "temperature": temperature},
              timeout=timeout, request_id=rid) as r:
        body = json.load(r)
    wall = (time.perf_counter() - t0) * 1000
    msg = body["choices"][0]["message"]
    usage = body.get("usage", {})

    rec = None
    try:
        cands = [e for e in metrics().get("requests", []) if e.get("time", 0) > before]
        # Prefer the record that echoes our request id; otherwise the newest one.
        for e in sorted(cands, key=lambda x: x.get("time", 0), reverse=True):
            if e.get("request_id") == rid:
                rec = e
                break
        if rec is None and cands:
            rec = sorted(cands, key=lambda x: x.get("time", 0), reverse=True)[0]
    except Exception:
        pass

    out = {"wall_ms": wall, "text": text_of(msg),
           "prompt_tokens": usage.get("prompt_tokens", 0),
           "output_tokens": usage.get("completion_tokens", 0),
           "finish": body["choices"][0].get("finish_reason")}
    if rec:
        for k in ("prompt_ms", "decode_ms", "ttft_ms", "prompt_read", "prompt_total",
                  "decode_tok_s", "reused_tokens"):
            if rec.get(k) is not None:
                out[k] = rec[k]
        pr, pm = rec.get("prompt_read"), rec.get("prompt_ms")
        if pr and pm:
            out["prefill_tok_s"] = round(pr / pm * 1000, 1)
    return out


def filler_prompt(approx_tokens):
    unit = ("深度学习框架的性能优化涉及计算图调度、内存分配器、算子融合与量化策略。"
            "Strata engine uses a peer-tier expert cache over NVLink with MTP speculative decoding. "
            "Benchmark results depend on prompt length, KV residency and draft acceptance rate. ")
    return ("请仔细阅读以下技术材料，然后回答文末的问题。\n\n" + unit * max(1, approx_tokens // 60) +
            "\n\n问题：请总结以上材料的核心技术要点，并展开谈谈你的理解。")


def stream(prompt, max_tokens, temperature=0.7, timeout=1800):
    t0 = time.perf_counter(); ttft = None; inter = []; last = None; text = ""
    with _req("/v1/chat/completions",
              {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
               "temperature": temperature, "stream": True}, timeout=timeout) as resp:
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
            delta = (d.get("content") or "") + (d.get("reasoning_content") or "")
            if not delta:
                continue
            now = time.perf_counter()
            if ttft is None:
                ttft = (now - t0) * 1000
            else:
                inter.append((now - last) * 1000)
            text += delta; last = now
    return dict(ttft_ms=round(ttft or 0, 1),
                total_ms=round((time.perf_counter() - t0) * 1000, 1),
                intervals=inter, text=text)


def stats(vals, drop=0):
    v = sorted(vals)[drop:]
    if not v:
        return dict(n=0, median=0)
    if len(v) < 4:
        return dict(n=len(v), median=round(statistics.median(v), 1))
    q = statistics.quantiles(v, n=4)
    return dict(n=len(v), median=round(statistics.median(v), 1),
                mean=round(statistics.mean(v), 1), iqr=round(q[2] - q[0], 1),
                min=round(v[0], 1), max=round(v[-1], 1))


# ── sections ─────────────────────────────────────────────────────────────────
def sec_prefill(R):
    """Engine-reported prompt_read / prompt_ms. A fresh prompt per row: a repeat is served from
    the conversation cache and reports a meaningless thousands-of-tok/s figure."""
    print("\n== Prefill (engine-reported prompt_read / prompt_ms) ==")
    R["prefill"] = []
    for size in (256, 1024, 4096, 16384, 32768):
        prompt = filler_prompt(size) + f"\n(seed {time.time_ns()})"
        r = chat(prompt, 8, temperature=0)
        e = dict(target=size, prompt_tok=r.get("prompt_total") or r["prompt_tokens"],
                 read=r.get("prompt_read"), ms=round(r.get("prompt_ms") or 0, 1),
                 tok_s=r.get("prefill_tok_s"), wall_ms=round(r["wall_ms"], 1))
        R["prefill"].append(e)
        print(f"  ~{size:>6} -> prompt {e['prompt_tok']:>6} tok, read {e['read']:>6}  "
              f"{e['ms']:8.0f} ms  {e['tok_s']:8.1f} tok/s   (client wall {e['wall_ms']:.0f} ms)")


def sec_ttft(R):
    print("\n== TTFT (engine-reported vs client wall) ==")
    R["ttft"] = []
    for size in (256, 4096, 16384):
        for i in range(2):
            r = chat(filler_prompt(size) + f"\n(seed {time.time_ns()})", 24, temperature=0)
            e = dict(target=size, run=i, ttft_ms=round(r.get("ttft_ms") or 0, 1),
                     wall_ms=round(r["wall_ms"], 1))
            R["ttft"].append(e)
            print(f"  ~{size:>6} #{i+1}: engine TTFT {e['ttft_ms']:8.0f} ms   "
                  f"client wall {e['wall_ms']:8.0f} ms")


def sec_decode(R, n=12, mt=512, lang="en"):
    prose, code = LANG_PAIRS[lang]
    print(f"\n== Decode ({n} runs each, first 2 discarded, lang={lang}) ==")
    R["decode"] = {}
    for label, prompt in (("prose", prose), ("code", code)):
        vals, texts = [], []
        for i in range(n):
            r = chat(prompt, mt)
            v = r.get("decode_tok_s")
            if v is None and r.get("decode_ms"):
                v = r["output_tokens"] / r["decode_ms"] * 1000
            vals.append(v or 0); texts.append(len(r["text"]))
            tag = "warm-up" if i < 2 else f"#{i-1}"
            print(f"  {label:>5} {tag:>7}: {vals[-1]:6.1f} tok/s  {r['output_tokens']:>5} tok  "
                  f"{r.get('decode_ms', 0):8.0f} ms decode")
        R["decode"][label] = stats(vals, drop=2)
        R["decode"][label]["chars_median"] = int(statistics.median(texts))
        s = R["decode"][label]
        print(f"  {label:>5}: MEDIAN {s['median']:6.1f} tok/s   IQR {s.get('iqr', 0)}   "
              f"n={s['n']}   ({s['chars_median']} chars)")


def sec_reuse(R):
    print("\n== Prompt reuse (identical prompt, three sends) ==")
    R["reuse"] = []
    p = filler_prompt(4096)          # deliberately identical every time
    for i in range(3):
        r = chat(p, 32, temperature=0)
        e = dict(run=i, ms=round(r.get("prompt_ms") or 0, 1),
                 prompt_tok=r.get("prompt_total"), read=r.get("prompt_read"),
                 reused=r.get("reused_tokens"), wall_ms=round(r["wall_ms"], 1))
        R["reuse"].append(e)
        print(f"  #{i+1}: prompt {e['prompt_tok']} tok, read {e['read']}, reused {e['reused']}  "
              f"prompt_ms {e['ms']:8.0f}   (wall {e['wall_ms']:.0f} ms)")


def sec_stream(R):
    print("\n== Streaming intervals ==")
    s = stream(PROSE, 2048)
    iv = s["intervals"]
    if not iv:
        R["stream"] = dict(error="no deltas")
        print("  no deltas")
        return
    v = sorted(iv); n = len(v)
    R["stream"] = dict(chunks=n, ttft_ms=s["ttft_ms"], total_s=round(s["total_ms"] / 1000, 2),
                       p50=round(statistics.median(iv), 1), p95=round(v[int(n * .95)], 1),
                       mean=round(statistics.mean(iv), 1), chars=len(s["text"]))
    print(f"  chunks={n}  TTFT={s['ttft_ms']:.0f}ms  p50={R['stream']['p50']:.0f}ms  "
          f"p95={R['stream']['p95']:.0f}ms  total={R['stream']['total_s']}s  "
          f"({R['stream']['chars']} chars)")


def sec_concurrency(R, mt=512, lang="en"):
    print(f"\n== Concurrency (lang={lang}) ==")
    R["concurrency"] = []
    for conc in (1, 2, 4):
        out = []
        def one():
            out.append(chat(LANG_PAIRS[lang][0], mt))
        t0 = time.perf_counter()
        ths = [threading.Thread(target=one) for _ in range(conc)]
        [t.start() for t in ths]; [t.join() for t in ths]
        wall = (time.perf_counter() - t0) * 1000
        toks = sum(r["output_tokens"] for r in out)
        per = [r.get("decode_tok_s") or 0 for r in out]
        e = dict(conc=conc, wall_ms=round(wall, 1), tokens=toks,
                 agg_tok_s=round(toks / wall * 1000, 1),
                 per_req=round(statistics.mean(per), 1), runs=[round(x, 1) for x in per])
        R["concurrency"].append(e)
        print(f"  {conc} in flight: wall {wall/1000:6.1f}s  {toks:>5} tok  "
              f"aggregate {e['agg_tok_s']:6.1f} tok/s  per request {e['per_req']:5.1f} tok/s")
        time.sleep(3)


def sec_needle(R, depths=(8192, 32768, 131072, 262144)):
    print("\n== Needle retrieval ==")
    R["needle"] = []
    for size in depths:
        filler = ("The following is archival meeting transcript material for reference purposes only. "
                  "It contains routine operational notes and carries no action items. ") * (size // 25)
        half = len(filler) // 2
        doc = filler[:half] + "\n\nThe secret access code is MANTIS-7742-QQ.\n\n" + filler[half:]
        prompt = ("[DOC]\n" + doc + "\n[/DOC]\n\nThe document above contains one access code. "
                  "Quote it exactly, character for character, and nothing else.")
        try:
            r = chat(prompt, 400, temperature=0)
        except Exception as e:
            print(f"  ~{size:>7}: request failed: {e}")
            R["needle"].append(dict(target=size, error=str(e)))
            continue
        found = "MANTIS-7742" in r["text"].upper()
        e = dict(target=size, prompt_tok=r.get("prompt_total"), found=found,
                 ms=round(r.get("prompt_ms") or r["wall_ms"], 1), reply=r["text"][:160])
        R["needle"].append(e)
        print(f"  ~{size:>7} (actual {e['prompt_tok']:>7}): "
              f"{'FOUND' if found else 'MISSED'}  prompt {e['ms']:8.0f} ms")
        if not found:
            print(f"      reply: {e['reply']!r}")


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
        with _req("/v1/chat/completions",
                  {"messages": [{"role": "user", "content": [
                      {"type": "text", "text": "What text and shapes do you see? Be literal and exact."},
                      {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
                   "max_tokens": 300, "temperature": 0.2}, timeout=600) as r:
            body = json.load(r)
        ans = text_of(body["choices"][0]["message"])
        ok = "STRATA" in ans.upper() and "512" in ans
        R["vision"] = dict(ok=ok, answer=ans[:300])
        print(f"  {'ok' if ok else 'FAIL'} -- read the card text: {ok}")
        print(f"  {ans[:120]!r}")
    except ImportError:
        print("  skipped: pip install pillow")
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
    ap.add_argument("--json", default="/tmp/strata-bench.json", help="where to write raw results")
    ap.add_argument("-n", type=int, default=12, help="decode runs (default 12, first 2 discarded)")
    ap.add_argument("--lang", default="en", choices=sorted(LANG_PAIRS),
                    help="prompt language for the prose/code and concurrency sections "
                         "(default en; zh measures the CJK draft-vocabulary path)")
    a = ap.parse_args()

    try:
        with _req("/v1/models", timeout=30) as r:
            d = json.load(r)["data"][0]
        print(f"model {d['id']}  context {d['meta']['n_ctx']}  "
              f"input {d['architecture']['input_modalities']}")
    except Exception as e:
        sys.exit(f"cannot reach {BASE} -- is the server up and the key right?\n  {e}")

    R = {}
    for name in (a.only or list(SECTIONS)):
        try:
            if name == "decode":
                sec_decode(R, n=a.n, lang=a.lang)
            elif name == "concurrency":
                sec_concurrency(R, lang=a.lang)
            else:
                SECTIONS[name](R)
        except Exception as e:
            print(f"  section {name} failed: {e}")
            R[name] = dict(error=str(e))

    json.dump(R, open(a.json, "w"), indent=1, ensure_ascii=False)
    print(f"\nraw results -> {a.json}")


if __name__ == "__main__":
    main()
