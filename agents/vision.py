"""
Vision layer — the VERIFIER. Independent from the Groq generator.

One place wraps all image analysis (content verification, QA, style analysis),
so the verifier model can be swapped without touching callers. Currently Gemini
2.5 Flash (free, 1500/day). To upgrade to Claude vision later, change only the
implementation here.

Design principle (see ROADMAP §1.5): generator (Groq) ≠ verifier (Gemini), so
the two models don't share blind spots.
"""
import base64
import json
import re
from pathlib import Path

import requests
import urllib3

from config.settings import (GEMINI_API_KEY, GEMINI_VISION_MODEL,
                             DASHSCOPE_API_KEY, QWEN_VISION_MODEL)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_VISION_MODEL}:generateContent"
)
_QWEN_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# Free Gemini flash tier ≈ 15 requests/min. Throttle to ~1 call / 4.2s so a
# full video (≈9 vision calls) stays under the limit instead of tripping 429.
_MIN_CALL_INTERVAL = 4.2
_last_call_ts = 0.0


def _throttle():
    import time
    global _last_call_ts
    wait = _MIN_CALL_INTERVAL - (time.time() - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.time()


def provider_name() -> str:
    """Name of the active verifier (for logging)."""
    import os
    if DASHSCOPE_API_KEY:
        return "Qwen-VL"
    if GEMINI_API_KEY:
        return "Gemini"
    if os.getenv("GROQ_API_KEY"):
        return "Groq"
    return "none"


def vision_available() -> bool:
    """True if any verifier (Qwen / Gemini / Groq) is configured."""
    import os
    return bool(DASHSCOPE_API_KEY or GEMINI_API_KEY or os.getenv("GROQ_API_KEY"))


def _qwen_vision(image_paths, prompt, labels, temperature, max_tokens) -> str | None:
    """Qwen-VL via DashScope (OpenAI-compatible). Primary verifier in China."""
    if not DASHSCOPE_API_KEY:
        return None
    content = [{"type": "text", "text": prompt}]
    for i, p in enumerate(image_paths):
        path = Path(p)
        if not path.exists():
            continue
        if labels and i < len(labels):
            content.append({"type": "text", "text": labels[i]})
        b64 = base64.b64encode(path.read_bytes()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{_mime_for(path)};base64,{b64}"}})
    payload = {
        "model": QWEN_VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature, "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}",
               "Content-Type": "application/json"}
    # This payload carries 5-6 base64 images — the LARGEST DashScope request in the
    # pipeline — so it drops with SSL EOF most often. A single verify=True/False pass
    # is not enough: when the Qwen judge fails, compete_and_apply can't score the
    # AI-generated close-up and falls back to a generic stock clip. Retry hard.
    import time
    last = None
    for attempt in range(8):
        try:
            r = requests.post(_QWEN_ENDPOINT, headers=headers, json=payload,
                              timeout=120, verify=(attempt % 2 == 0))
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except requests.exceptions.SSLError as e:
            last = e
            time.sleep(min(6, 1.0 * (attempt + 1)))
        except Exception as e:
            last = e
            time.sleep(min(6, 1.0 * (attempt + 1)))
    print(f"  [Vision] Qwen error after retries: {last}")
    return None


def _mime_for(path: Path) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")


def analyse_images(image_paths: list[str], prompt: str,
                   labels: list[str] | None = None,
                   temperature: float = 0.2,
                   max_tokens: int = 1024) -> str | None:
    """
    Send one or more images + a text prompt to the verifier. Returns the model's
    text response, or None if unavailable / on error (callers degrade gracefully).

    labels: optional per-image text labels (e.g. "Image 1: scene 3").
    """
    if not vision_available():
        return None

    # Provider preference: Qwen (DashScope) first — China-accessible, no VPN.
    if DASHSCOPE_API_KEY:
        out = _qwen_vision(image_paths, prompt, labels, temperature, max_tokens)
        if out is not None:
            return out
        print("  [Vision] Qwen unavailable — trying Gemini…")

    if not GEMINI_API_KEY:
        # No Gemini → go straight to Groq fallback
        print("  [Vision] Falling back to Groq vision (Llama 4 Scout)…")
        return _groq_vision(image_paths, prompt, labels, temperature, max_tokens)

    parts: list[dict] = [{"text": prompt}]
    for i, p in enumerate(image_paths):
        path = Path(p)
        if not path.exists():
            continue
        if labels and i < len(labels):
            parts.append({"text": labels[i]})
        b64 = base64.b64encode(path.read_bytes()).decode()
        parts.append({"inline_data": {"mime_type": _mime_for(path), "data": b64}})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": temperature,
                             "maxOutputTokens": max_tokens},
    }

    import time
    verify = True
    # Retry transient overload (503) / rate-limit (429) with backoff.
    for attempt in range(3):
        try:
            _throttle()                      # stay under free-tier RPM
            r = requests.post(_ENDPOINT, params={"key": GEMINI_API_KEY},
                              json=payload, timeout=120, verify=verify)
            if r.status_code in (429, 500, 503) and attempt < 2:
                wait = 2 ** attempt          # 1, 2 s
                print(f"  [Vision] Gemini {r.status_code} — retry in {wait}s "
                      f"({attempt+1}/2)")
                time.sleep(wait)
                continue
            if r.status_code in (429, 500, 503):
                break                        # exhausted — fall back to Groq
            r.raise_for_status()
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except requests.exceptions.SSLError:
            verify = False                   # Mac SSL fallback, retry immediately
            continue
        except Exception as e:
            print(f"  [Vision] Gemini error: {e}")
            break

    # ── Free fallback: Groq Llama 4 Scout vision (different provider) ──────────
    # When Gemini is rate-limited, Groq's free vision keeps the loop alive.
    print("  [Vision] Falling back to Groq vision (Llama 4 Scout)…")
    return _groq_vision(image_paths, prompt, labels, temperature, max_tokens)


def _groq_vision(image_paths: list[str], prompt: str,
                 labels: list[str] | None, temperature: float,
                 max_tokens: int) -> str | None:
    """Groq Llama 4 Scout vision — free fallback when Gemini is unavailable."""
    import os
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        from groq import Groq
        content: list[dict] = [{"type": "text", "text": prompt}]
        for i, p in enumerate(image_paths):
            path = Path(p)
            if not path.exists():
                continue
            if labels and i < len(labels):
                content.append({"type": "text", "text": labels[i]})
            b64 = base64.b64encode(path.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{_mime_for(path)};base64,{b64}"}})
        resp = Groq(api_key=api_key).chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": content}],
            temperature=temperature, max_tokens=max_tokens,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"  [Vision] Groq fallback error: {e}")
        return None


def analyse_images_json(image_paths: list[str], prompt: str,
                        labels: list[str] | None = None) -> dict | list | None:
    """
    Like analyse_images but parses the response as JSON. Strips markdown fences.
    Returns parsed object, or None on any failure.
    """
    raw = analyse_images(image_paths, prompt, labels)
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    # Be forgiving: extract the first {...} or [...] block if there's extra prose
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None
