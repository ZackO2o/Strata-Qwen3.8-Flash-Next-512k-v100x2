#!/usr/bin/env python3
"""Is the vision path wired up end to end?

    python3 tools/vision_smoke.py

Sends a generated card reading STRATA 512K, with a red-outlined rectangle and a blue ellipse,
and checks that the reply reads the text literally and names both shapes. This is a wiring
test, not an accuracy benchmark: it proves the encoder ran, the image reached the model, and
the text came back. It says nothing about quality on hard images.

Three things have to be true before this passes, and the failure mode of each is distinct:
  1. the `vision` block in the config points at a built strata-vision binary
  2. the ENGINE has --vision in its argument list — without it the request returns
     `400 this engine was started without --vision`
  3. --vram-reserve-mib leaves the encoder room
"""
import base64, io, json, os, sys
import urllib.request

API_KEY_FILE = os.environ.get("API_KEY_FILE", os.path.expanduser("~/.strata_api_key"))
PORT = os.environ.get("PORT", "8080")
BASE = os.environ.get("BASE_URL", f"http://127.0.0.1:{PORT}")
KEY = open(API_KEY_FILE).read().strip() if os.path.exists(API_KEY_FILE) else os.environ.get("STRATA_API_KEY", "")

TEXT = "STRATA 512K"


def main():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        sys.exit("Pillow is required: pip install pillow")

    img = Image.new("RGB", (720, 260), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=48)
    except Exception:
        font = None
    d.rectangle([20, 20, 700, 130], outline="red", width=6)
    d.text((45, 45), TEXT, fill="black", font=font)
    d.ellipse([520, 150, 690, 240], fill="blue")

    buf = io.BytesIO(); img.save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    payload = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "What text and shapes do you see? Be literal and exact."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
        "max_tokens": 300, "temperature": 0.2}

    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        print(f"HTTP {e.code}: {detail}")
        if "without --vision" in detail:
            print("\n-> add --vision to the engine's args in the config, then restart.")
            print("   The config's `vision` block only starts the encoder; the engine needs the flag too.")
        sys.exit(1)

    ans = body["choices"][0]["message"].get("content") or ""
    text_ok = "STRATA" in ans.upper() and "512" in ans
    print(f"text read correctly: {text_ok}")
    print(f"reply: {ans[:400]}")

    if text_ok:
        print("\nok — the encoder ran and the image reached the model")
        sys.exit(0)
    print("\nFAIL — the request succeeded but the text was not read; check the encoder binary and mmproj")
    sys.exit(1)


if __name__ == "__main__":
    main()
