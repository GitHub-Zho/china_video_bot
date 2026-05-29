"""Script Agent — generates video scripts via Groq API (free LLM, no credit card).

Groq provides free access to Llama 3.3 70B (14,400 req/day free tier).
Falls back to a curated template bank if API is unavailable.
"""
import json
import os
from pathlib import Path
from config.settings import HISTORY_FILE
from config.prompts import SYSTEM_PROMPT, FEEDBACK_ADDENDUM


def _load_feedback() -> str:
    path = Path(HISTORY_FILE)
    if not path.exists():
        return ""
    history = json.loads(path.read_text())
    if len(history) < 3:
        return ""
    ranked     = sorted(history, key=lambda x: x.get("avg_view_duration", 0), reverse=True)
    top        = ranked[:3]
    bottom     = ranked[-2:]
    top_str    = "\n".join(f"- {v['topic']} ({v['audience_type']}): {v['avg_view_duration']:.0f}s avg" for v in top)
    bottom_str = "\n".join(f"- {v['topic']} ({v['audience_type']}): {v['avg_view_duration']:.0f}s avg" for v in bottom)
    return FEEDBACK_ADDENDUM.format(top_performers=top_str, underperformers=bottom_str)


def _generate_via_groq(audience_type: str | None) -> dict:
    """Call Groq API (free tier) to generate a video script."""
    from groq import Groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    client   = Groq(api_key=api_key)
    feedback = _load_feedback()
    system   = SYSTEM_PROMPT + (feedback if feedback else "")
    user_msg = (
        f"Generate a script. Audience type: {audience_type}."
        if audience_type
        else "Choose the best audience type and topic for today."
    )

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",   # best free Groq model
        messages=[
            {"role": "system",  "content": system},
            {"role": "user",    "content": user_msg},
        ],
        temperature=0.8,
        max_tokens=900,
    )

    raw = response.choices[0].message.content.strip()
    # Strip accidental markdown fences
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    return json.loads(raw)


def _fallback_template(audience_type: str | None) -> dict:
    """Curated templates — used when Groq API is unavailable."""
    import random
    templates = [
        {
            "title": "China's Hidden Wonders: What No One Tells You",
            "description": "Discover the side of China most travelers never see — ancient villages, neon markets, impossible landscapes. #ChinaTravel #VisitChina #HiddenGems #TravelAsia #China2025",
            "tags": ["China travel", "hidden gems", "Asia travel", "Chinese culture", "travel tips"],
            "audience_type": "explorer",
            "topic": "Hidden wonders of China",
            "hook": "What if everything you thought you knew about China was only half the story?",
            "script": "What if everything you thought you knew about China was only half the story? Beyond the Great Wall and the Forbidden City lies a country of impossible variety — neon-drenched cities where ancient temples sit between skyscrapers, mountain villages where time has stood still for centuries, and a cuisine so diverse it could take a lifetime to explore. In Yunnan, terraced rice fields glow gold at sunset. In Chengdu, giant pandas lounge in bamboo forests while locals play mahjong nearby. In Shanghai, a futuristic skyline rises across the river from a century-old waterfront. China isn't one place — it's a hundred countries in one. And the best part? It's more welcoming than you'd expect. If you've been curious about China but didn't know where to start, the answer is simple: just start anywhere. Every corner has a story.",
            "image_queries": ["Yunnan rice terraces sunset", "Chengdu panda sanctuary bamboo", "Shanghai skyline Bund night", "Zhangjiajie mountains mist", "Guilin Li River karst", "Beijing Forbidden City", "Xi'an Terracotta Warriors", "Suzhou classical garden"],
            "mood": "cinematic"
        },
        {
            "title": "First Time in China? Here's What to Expect",
            "description": "Planning your first trip to China but feeling nervous? This is what actually happens when you arrive. #FirstTimeChina #ChinaTravel #TravelTips #VisitChina",
            "tags": ["China first time", "China travel guide", "visit China", "travel tips"],
            "audience_type": "newcomer",
            "topic": "First-time visitor guide",
            "hook": "Nervous about visiting China? Here's what actually happens when you arrive.",
            "script": "Nervous about visiting China? Here's what actually happens when you arrive. The cities are cleaner than you imagined, the high-speed trains faster than you expected, and strangers friendlier than you were warned. Yes, Google doesn't work — but WeChat and Baidu Maps do, and most hotels are happy to help navigate. The food is nothing like takeout back home — it's layered, regional, and endlessly surprising. Breakfast at a local shop costs less than a dollar and tastes like nothing else on earth. The language barrier is real, but translation apps have solved trickier problems. China rewards curiosity. The more you wander off the tourist path, the more it opens up — local markets, neighbourhood teahouses, park dancers at sunrise. First-time visitors often say the same thing: it was nothing like what I feared, and everything I didn't expect.",
            "image_queries": ["China high speed train", "Beijing street food market", "Shanghai modern city", "Chinese tea ceremony", "Great Wall sunrise", "China local market", "Chengdu street life", "China temple morning"],
            "mood": "energetic"
        },
        {
            "title": "Why China Should Be Your Next Travel Destination",
            "description": "From dumplings at dawn to dragon boat festivals, China offers experiences unlike anywhere else on Earth. Here's why it should top your travel list. #ChinaTravel #TravelGoals #Asia",
            "tags": ["China travel", "travel destinations", "Asia bucket list", "Chinese food", "culture travel"],
            "audience_type": "newcomer",
            "topic": "Why visit China",
            "hook": "There's a place where ancient and futuristic exist side by side — and most people are still sleeping on it.",
            "script": "There's a place where ancient and futuristic exist side by side — and most people are still sleeping on it. China. Not the China from old movies or cold war headlines. The real China of today: high-speed trains connecting cities faster than planes, street food scenes that would make any chef weep, and landscapes so dramatic they look AI-generated. You can stand inside a 600-year-old emperor's palace in the morning, then ride a magnetic levitation train to a skyscraper rooftop bar in the afternoon. You can hike mountains that float in the clouds, or float down rivers flanked by limestone peaks. And the cost? A fraction of Europe or Japan. The question isn't whether China is worth visiting. The question is: what took you so long?",
            "image_queries": ["China maglev train futuristic", "Beijing Palace Museum courtyard", "Zhangjiajie floating mountains", "Guilin river boat limestone", "Shanghai rooftop bar night", "China street dumpling food", "Chengdu hotpot restaurant", "China landscape aerial view"],
            "mood": "mysterious"
        },
    ]
    if audience_type:
        candidates = [t for t in templates if t["audience_type"] == audience_type]
        return random.choice(candidates) if candidates else random.choice(templates)
    return random.choice(templates)


def generate_script(audience_type: str = None) -> dict:
    """
    Generate a video script using Groq API (free).
    Falls back to built-in template bank if API fails.
    """
    try:
        print("  [Script] Calling Groq API (llama-3.3-70b)...")
        data = _generate_via_groq(audience_type)
        print(f"  [Script] ✅ Generated: {data.get('topic', '?')}")
    except Exception as e:
        print(f"  [Script] Groq unavailable ({e}), using template fallback")
        data = _fallback_template(audience_type)
        print(f"  [Script] ✅ Template: {data['topic']}")

    # Normalise: ensure 8 image queries
    queries = data.get("image_queries", [])
    if len(queries) < 8:
        queries += ["China landscape travel"] * (8 - len(queries))
    data["image_queries"] = queries[:8]

    return data
