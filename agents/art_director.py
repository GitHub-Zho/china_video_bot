"""
Art Director — Qwen reads the WHOLE script and writes a rich, context-aware
image/video generation prompt for each scene.

Why: a bare scene keyword ("108 slices") gives the image model no context. The
art director understands the full video is about, say, Beijing roast duck, so it
writes "a chef slicing glossy Peking roast duck into thin pieces on a wooden
board, crispy skin, restaurant" — which generates footage that actually fits.

Fills ScenePlan.gen_prompt for every scene. Falls back to the scene's own text
if Qwen is unavailable.
"""
import json
import os

import requests
import urllib3

from config.settings import DASHSCOPE_API_KEY

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def enrich_gen_prompts(brief) -> None:
    """
    Mutates brief.scenes: sets each scene's gen_prompt to a rich, full-context
    description suitable for text-to-image / text-to-video. No-op if no key.
    """
    if not DASHSCOPE_API_KEY:
        return

    topic = brief.label() or brief.topic
    scene_lines = "\n".join(
        f'{i}. narration: "{s.narration}"  | subject hint: {s.stock_query()}'
        for i, s in enumerate(brief.scenes)
    )
    sys = (
        "You are the ART DIRECTOR for a short video. You get the WHOLE script for "
        "context, then write ONE image/video generation prompt per scene.\n\n"
        "CRITICAL — frame the SHOT around the single most important visual of the line:\n"
        "1. Identify the ONE focal point the line is really about — an ACTION, a "
        "   TEXTURE, or a SUBJECT — and frame a TIGHT shot on it.\n"
        "   • If the line is about an action (slicing, pouring, wrapping): EXTREME "
        "     CLOSE-UP on the hands + the action. Show hands only, NO face.\n"
        "   • If it's about a sensory quality (crispy/glossy skin, steam, sheen): "
        "     MACRO close-up emphasising that texture, glistening detail, shallow depth.\n"
        "   • If it's about the dish/place: a clean hero shot of THAT, nothing else.\n"
        "2. EXCLUDE distractions: no unrelated food in the background, no clutter, no "
        "   faces when the point is hands/texture. State what to exclude.\n"
        "3. Use the video topic for context (a bare '108 slices' becomes 'a knife "
        "   slicing crispy Peking roast duck into thin glistening pieces, close-up on "
        "   the blade and meat, hands only, no face').\n"
        "4. Realistic food/travel photography, China-set, 20-35 words, concrete.\n\n"
        "Examples:\n"
        "  line '108 slices by hand' → 'Extreme close-up of a knife slicing lacquered "
        "Peking roast duck into thin glistening slices on a wood board, crispy skin "
        "visible, hands only, no face, warm light, shallow depth of field'\n"
        "  line 'skin shatters like glass' → 'Macro close-up of glossy mahogany Peking "
        "duck skin, oil sheen glistening, crispy crackled texture, steam, shallow depth, "
        "no people, no background clutter'"
    )
    user = (
        f"VIDEO TOPIC: {topic}\n\nFULL SCRIPT (all scenes, for context):\n{scene_lines}\n\n"
        f"Return ONLY JSON: {{\"prompts\": [\"<scene 0 prompt>\", \"<scene 1 prompt>\", ...]}} "
        f"with exactly {len(brief.scenes)} prompts, in order."
    )
    import time
    try:
        raw = None
        for attempt in range(5):
            try:
                r = requests.post(
                    _ENDPOINT,
                    headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}",
                             "Content-Type": "application/json"},
                    json={"model": "qwen-max",
                          "messages": [{"role": "system", "content": sys},
                                       {"role": "user", "content": user}],
                          "temperature": 0.4},
                    verify=(attempt == 0), timeout=60,
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"].strip()
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(1.5 * (attempt + 1))
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        prompts = json.loads(raw.strip()).get("prompts", [])
        for i, s in enumerate(brief.scenes):
            if i < len(prompts) and prompts[i]:
                s.gen_prompt = prompts[i].strip()
        print(f"  [ArtDirector] ✅ Wrote {min(len(prompts), len(brief.scenes))} "
              f"context-aware generation prompts")
    except Exception as e:
        print(f"  [ArtDirector] unavailable ({e}) — using scene text as prompt")
