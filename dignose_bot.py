#!/usr/bin/env python3
"""
Quick diagnostic for the invoice bot stack.
Checks, in order, the most common failure points:
  1. Python version (python-telegram-bot v20+ needs Python 3.8+)
  2. Installed packages (requests, telegram, openai)
  3. Config values present (from config.py or env vars)
  4. Telegram token validity (getMe) -- free
  5. Moonshot auth + model visibility (/models) -- free
  6. Tiny Moonshot text completion (~few tokens, cheapest possible call)
  7. Optional: tiny vision call if you pass --image path/to.jpg

Usage:
  python diagnose_bot.py                 # checks 1-6
  python diagnose_bot.py --image x.jpg # also tests vision route
  python diagnose_bot.py --skip-chat     # stop after /models (zero Moonshot spend)
"""

from __future__ import annotations
import argparse, base64, json, os, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))


def section(title: str):
    print(f"\n=== {title} ===")


def ok(msg: str):
    print(f"  PASS  {msg}")


def bad(msg: str):
    print(f"  FAIL  {msg}")


def warn(msg: str):
    print(f"  WARN  {msg}")


def redact(v: str) -> str:
    if not v:
        return "<missing>"
    return v[:4] + "..." + v[-4:] if len(v) > 8 else "****"


def step_python():
    section("1. Python version")
    v = sys.version_info
    ok(f"Python {v.major}.{v.minor}.{v.micro} at {sys.executable}")
    if (v.major, v.minor) < (3, 8):
        bad("python-telegram-bot v20+ (Application class) requires Python 3.8+.")
        print("        Either use Python 3.8+ in your venv, or rewrite the bot with")
        print("        python-telegram-bot 13.x (Updater/ dispatcher style).")


def step_imports():
    section("2. Installed packages")
    for mod, pkg in [("requests", "requests"),
                     ("telegram", "python-telegram-bot"),
                     ("openai", "openai")]:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            ok(f"{pkg} installed ({ver})")
        except ImportError:
            bad(f"{mod} not installed -> run: pip install {pkg}")
            return False
    return True


def step_config():
    section("3. Config values")
    cfg = {}
    try:
        import config
        for k in ["BOT_TOKEN", "CHAT_ID", "MOONSHOT_KEY", "MOONSHOT_BASE", "MOONSHOT_MODEL"]:
            cfg[k] = str(getattr(config, k, os.environ.get(k, ""))).strip()
        ok("loaded from config.py")
    except ImportError:
        warn("config.py not found; falling back to environment variables")
        for k in ["BOT_TOKEN", "CHAT_ID", "MOONSHOT_KEY", "MOONSHOT_BASE", "MOONSHOT_MODEL"]:
            cfg[k] = os.environ.get(k, "").strip()
    allok = True
    for k, v in cfg.items():
        if not v or "xxxx" in v.lower():
            bad(f"{k} is missing/placeholder"); allok = False
        else:
            ok(f"{k} = {redact(v) if 'KEY' in k or 'TOKEN' in k else v}")
    if not allok:
        print("        Create config.py next to bot.py with the 5 values.")
        sys.exit(1)
    cfg["CHAT_ID"] = int(cfg["CHAT_ID"])
    return cfg


def step_telegram(cfg):
    section("4. Telegram getMe (free)")
    try:
        import requests
        t0 = time.time()
        r = requests.get(
            f"https://api.telegram.org/bot{cfg['BOT_TOKEN']}/getMe",
            timeout=15)
        dt = time.time() - t0
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            b = data["result"]
            ok(f"HTTP {r.status_code} in {dt:.1f}s -> bot @{b.get('username')} (id {b.get('id')})")
        else:
            bad(f"HTTP {r.status_code}: {data.get('description')} -> token invalid or revoked")
    except Exception as e:
        bad(f"request error: {e} -> network/proxy/DNS issue, not a credential issue")


def step_models(cfg):
    section("5. Moonshot /models (free)")
    import requests
    try:
        t0 = time.time()
        r = requests.get(f"{cfg['MOONSHOT_BASE']}/models",
                         headers={"Authorization": f"Bearer {cfg['MOONSHOT_KEY']}"},
                         timeout=20)
        dt = time.time() - t0
        if r.status_code == 401:
            bad("HTTP 401 -> MOONSHOT_KEY invalid/expired"); return False
        if r.status_code == 402:
            bad("HTTP 402 -> account needs top-up (check platform.moonshot.cn)"); return False
        r.raise_for_status()
        ids = [m.get("id") for m in r.json().get("data", [])]
        ok(f"HTTP 200 in {dt:.1f}s, {len(ids)} models visible")
        if cfg["MOONSHOT_MODEL"] in ids:
            ok(f"model '{cfg['MOONSHOT_MODEL']}' is available")
        else:
            bad(f"model '{cfg['MOONSHOT_MODEL']}' NOT in /models list")
            print(f"        available: {', '.join(ids[:12])}")
            return False
        return True
    except Exception as e:
        bad(f"{e} -> network issue or wrong MOONSHOT_BASE"); return False


def step_chat(cfg, image=None):
    section("6. Moonshot chat completion (tiny spend)" if image is None
            else "7. Moonshot vision completion (tiny spend)")
    import requests
    content = [{"type": "text",
                "text": "Reply with exactly: pong"}]
    if image:
        with open(image, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content[0]["text"] = "What is in this image? Answer in <=5 words."
        content.append({"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    body = {"model": cfg["MOONSHOT_MODEL"],
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 10, "temperature": 1}
    t0 = time.time()
    r = requests.post(f"{cfg['MOONSHOT_BASE']}/chat/completions",
                      json=body,
                      headers={"Authorization": f"Bearer {cfg['MOONSHOT_KEY']}"},
                      timeout=90)
    dt = time.time() - t0
    if r.status_code == 429:
        bad(f"HTTP 429 rate limit after {dt:.1f}s -> wait and retry, or upgrade tier")
        return
    if r.status_code in (401, 402):
        bad(f"HTTP {r.status_code} -> auth/billing issue"); return
    if r.status_code >= 400:
        bad(f"HTTP {r.status_code}: {r.text[:300]}"); return
    out = r.json()["choices"][0]["message"]["content"].strip()
    usage = r.json().get("usage", {})
    ok(f"HTTP 200 in {dt:.1f}s -> reply: {out!r}")
    ok(f"usage: {json.dumps(usage)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="path to a small jpg to also test the vision route")
    ap.add_argument("--skip-chat", action="store_true",
                    help="stop after /models; zero Moonshot spend")
    args = ap.parse_args()

    step_python()
    if not step_imports():
        print("\nFix installs first:  pip install requests python-telegram-bot openai")
        sys.exit(1)
    cfg = step_config()
    step_telegram(cfg)
    if step_models(cfg) and not args.skip_chat:
        step_chat(cfg)
        if args.image:
            step_chat(cfg, image=args.image)

    print("\nDone. Any FAIL above is the thing to fix; the rest of the stack is fine.")


if __name__ == "__main__":
    main()