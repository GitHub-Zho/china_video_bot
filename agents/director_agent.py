"""
Director Agent — Phase D.

Plans the ENTIRE video scene-by-scene BEFORE any generation starts.
Replaces the separate Script Agent + Storyboard Agent pattern.

Why this matters:
  Old flow: script → TTS (audio N secs) → storyboard → download M clips
            M×5s often ≠ N secs → video loops or gets cut
  New flow: Director plans K scenes × D secs = target_seconds
            Script is assembled from per-scene narration
            Media = exactly K items, durations match audio

Output: CreativeBrief — the single source of truth for the whole pipeline.
"""
import json
import math
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

from config.settings import (
    TARGET_YOUTUBE_SECONDS, SLIDE_DURATION, IMAGES_PER_VIDEO, HISTORY_FILE,
)

load_dotenv()

GROQ_MODEL = "llama-3.3-70b-versatile"
CRITIC_PASS_SCORE = 7
MAX_RETRIES = 2


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ScenePlan:
    index:        int
    narration:    str     # spoken words for this scene (8-18 words)
    visual_query: str     # precise footage search query
    duration:     float   # target seconds (3-7)
    emotion:      str     # cinematic | energetic | serene | dramatic | warm


@dataclass
class CreativeBrief:
    # Publishing metadata
    title:         str
    description:   str
    tags:          list[str]
    # Content identity
    topic:         str
    audience_type: str
    mood:          str
    hook:          str
    cta:           str
    # Scene plan
    scenes:        list[ScenePlan]
    target_seconds: float

    @property
    def script(self) -> str:
        """Assembled narration — all scene lines joined."""
        return " ".join(s.narration for s in self.scenes)

    @property
    def image_queries(self) -> list[str]:
        """Compat shim: visual query per scene (used by publisher metadata)."""
        return [s.visual_query for s in self.scenes]

    def to_metadata_dict(self) -> dict:
        """Serialise for metadata.json."""
        return {
            "title":         self.title,
            "description":   self.description,
            "tags":          self.tags,
            "topic":         self.topic,
            "audience_type": self.audience_type,
            "mood":          self.mood,
            "hook":          self.hook,
            "cta":           self.cta,
            "script":        self.script,
            "image_queries": self.image_queries,
            "target_seconds": self.target_seconds,
            "scenes": [
                {"index": s.index, "narration": s.narration,
                 "visual_query": s.visual_query,
                 "duration": s.duration, "emotion": s.emotion}
                for s in self.scenes
            ],
        }


# ── Loaders: analytics insights + human-curated guidelines ────────────────────

def _load_insights() -> str:
    """Load structured insights from analytics learning. Empty str if none."""
    insights_path = Path("data/insights.json")
    if not insights_path.exists():
        return ""
    try:
        data = json.loads(insights_path.read_text())
        if not data:
            return ""
        lines = ["Past performance insights (use to make better creative decisions):"]
        if data.get("high_ctr_patterns"):
            lines.append("High CTR patterns: " + "; ".join(data["high_ctr_patterns"][:3]))
        if data.get("high_retention_topics"):
            lines.append("Best retention topics: " + "; ".join(data["high_retention_topics"][:3]))
        if data.get("avoid"):
            lines.append("Avoid: " + "; ".join(data["avoid"][:3]))
        return "\n".join(lines)
    except Exception:
        return ""


def _load_guidelines() -> str:
    """
    Load human-curated creative guidelines from data/director_guidelines.json.

    This file is maintained by Claude based on video quality feedback.
    The Director injects these rules into every Groq generation prompt,
    so edits here directly shape what the next video looks like.

    Returns a formatted string ready to embed in the system prompt,
    or empty string if the file doesn't exist / has no content.
    """
    path = Path("data/director_guidelines.json")
    if not path.exists():
        return ""
    try:
        g = json.loads(path.read_text())
        if not g:
            return ""
        lines = [
            f"=== CREATIVE GUIDELINES (version {g.get('version', 1)}, "
            f"updated {g.get('updated', '?')}) ==="
        ]
        if g.get("do"):
            lines.append("RULES — always do these:")
            lines.extend(f"  ✓ {rule}" for rule in g["do"])
        if g.get("avoid"):
            lines.append("RULES — never do these:")
            lines.extend(f"  ✗ {rule}" for rule in g["avoid"])
        if g.get("style_notes"):
            lines.append("STYLE:")
            lines.extend(f"  → {note}" for note in g["style_notes"])
        if g.get("example_good_narrations"):
            lines.append("GOOD narration examples (use as reference):")
            lines.extend(f'  "{ex}"' for ex in g["example_good_narrations"])
        if g.get("example_bad_narrations"):
            lines.append("BAD narration examples (never write like this):")
            lines.extend(f'  "{ex}"' for ex in g["example_bad_narrations"])
        result = "\n".join(lines)
        print(f"  [Director] 📋 Guidelines loaded (v{g.get('version',1)}, "
              f"{len(g.get('do',[]))} rules, {len(g.get('avoid',[]))} avoids)")
        return result
    except Exception as e:
        print(f"  [Director] ⚠️  Could not load guidelines: {e}")
        return ""


# ── Groq director prompt ──────────────────────────────────────────────────────

_SYSTEM = """\
You are a creative director for short-form China travel videos targeting English-speaking audiences.
Plan a complete video scene by scene. Each scene has its own spoken narration line + visual.

STRICT Requirements — every single scene MUST meet ALL of these:
1. narration MUST be 10-15 spoken words (a full sentence, NOT a phrase)
   BAD:  "Explore the unknown"   (3 words — REJECTED)
   BAD:  "Visit ancient villages" (3 words — REJECTED)
   GOOD: "Most visitors never know this ancient village exists just two hours from Shanghai." (14 words ✓)
   GOOD: "The rice terraces of Yunnan glow gold every October — and almost no one goes." (15 words ✓)
2. Scene 0 MUST open with a surprising hook sentence (10-15 words) that makes someone stop scrolling
3. visual_query MUST be ultra-specific: 10-15 words, include China location + mood + time of day
4. Always prefix China locations: "China Guilin", "China Shanghai", etc.
5. Last scene MUST end with soft CTA sentence: "Follow for more hidden China adventures."
6. Target duration: {target_seconds} seconds total, {n_scenes} scenes × {secs_per_scene}s each

Return ONLY valid JSON — no markdown, no explanation:
{{
  "title": "...",
  "description": "...(2-3 sentences + 5 hashtags)",
  "tags": ["...", "...", "...", "...", "..."],
  "topic": "...",
  "audience_type": "explorer|newcomer",
  "mood": "cinematic|energetic|serene|dramatic",
  "hook": "...(first 10-15 word hook sentence)",
  "cta": "...",
  "scenes": [
    {{
      "narration": "...(MUST be 10-15 spoken words, a complete sentence)",
      "visual_query": "...(10-15 words, location + mood + time of day)",
      "duration": {secs_per_scene},
      "emotion": "cinematic|energetic|serene|dramatic|warm"
    }},
    ...
  ]
}}"""

_CRITIC_SYSTEM = """\
Score this travel video script on 5 criteria (1-10 each):
  hook        — does scene 0 make someone stop scrolling? (needs 10-15 words, surprising fact)
  specificity — are real, specific China locations/facts named? (generic = score 1-3)
  word_count  — do ALL scenes have 10-15 words? Scenes with <8 words score 1; 8-10 words score 5
  reels_fit   — conversational, not documentary? Does it sound like a real person talking?
  variety     — do scenes cover different aspects (city/nature/culture/food/history)?

CRITICAL: If ANY scene has fewer than 8 words, the word_count score must be 1-3.
CRITICAL: Narrations like "Explore the unknown" or "Visit ancient villages" are FAILURES (too short).

overall = average of all 5 scores (round to nearest integer).

Return ONLY JSON:
{{"hook":<n>,"specificity":<n>,"word_count":<n>,"reels_fit":<n>,"variety":<n>,
  "overall":<n>,"feedback":"<2 sentences naming exactly what to fix>"}}"""


# ── Fallback templates ────────────────────────────────────────────────────────

def _fallback_brief(audience_type: str | None, target_seconds: float) -> CreativeBrief:
    n_scenes    = math.ceil(target_seconds / SLIDE_DURATION)
    secs_per    = target_seconds / n_scenes

    templates = [
        {
            "title": "5 China Places That Will Blow Your Mind",
            "description": "From floating mountains to ancient water towns, China hides wonders most tourists never find. #ChinaTravel #HiddenGems #VisitChina #Asia #Travel",
            "tags": ["China travel", "hidden gems", "Asia travel", "Chinese culture", "travel 2025"],
            "topic": "Hidden wonders of China",
            "audience_type": "explorer",
            "mood": "cinematic",
            "hook": "Most tourists never see this part of China — and that is a shame.",
            "cta": "Follow for more hidden China adventures.",
            "raw_scenes": [
                ("Most tourists never see this part of China — and that is a shame.",
                 "aerial China Zhangjiajie sandstone pillars dawn golden mist swirling", "dramatic"),
                ("Fenghuang Ancient Town is two thousand years old and still fully lived-in.",
                 "China Fenghuang ancient town wooden stilt houses river reflection sunrise warm", "warm"),
                ("The rice terraces of Yunnan glow gold every October — almost no one goes.",
                 "China Yuanyang Hani rice terraces golden sunset aerial panoramic harvest", "cinematic"),
                ("Guilin's Li River looks exactly like a Song dynasty ink painting — but real.",
                 "China Li River Guilin cormorant fisherman karst peaks morning mist boat", "serene"),
                ("In Chengdu you can watch giant pandas eat bamboo for about five dollars.",
                 "China Chengdu giant panda research base bamboo close up morning light", "warm"),
                ("Zhangye Danxia — rainbow mountains that took twenty million years to form.",
                 "China Zhangye Danxia colourful rainbow mountains aerial wide shot golden hour", "dramatic"),
                ("Shanghai's Bund at midnight looks like a science fiction film set — for free.",
                 "China Shanghai Bund skyline night reflections Pudong neon lights river", "energetic"),
                ("A full meal at a local night market in Chengdu costs under three dollars.",
                 "China Chengdu night market street food stalls bustling vendors lanterns evening", "energetic"),
                ("Wuzhen water town has no cars — just canals, stone bridges, and silence.",
                 "China Wuzhen water town canals stone bridges reflection lanterns dusk serene", "serene"),
                ("The Huangshan mountains are so dramatic the Chinese say they are a painting.",
                 "aerial China Huangshan yellow mountain pine trees sea of clouds sunrise fog", "cinematic"),
                ("Xi'an has an ancient wall you can cycle around on top — all 14 kilometres.",
                 "China Xian ancient city wall cycling sunset golden light panoramic dusk", "energetic"),
                ("Follow for more hidden China adventures.",
                 "China misty mountain temple sunrise peaceful monks walking stone path", "cinematic"),
            ]
        },
        {
            "title": "First Time in China? Watch This First",
            "description": "Everything first-time visitors need to know about modern China — it's nothing like you expect. #FirstTimeChina #ChinaTravel #TravelTips #VisitChina #Asia",
            "tags": ["first time China", "China travel guide", "China tips", "visit China", "travel Asia"],
            "topic": "First-time visitor guide to China",
            "audience_type": "newcomer",
            "mood": "energetic",
            "hook": "China is nothing like what you were told — here is what to actually expect.",
            "cta": "Save this before your China trip.",
            "raw_scenes": [
                ("China is nothing like what you were told — here is what to actually expect.",
                 "China modern city Shanghai high rise buildings blue sky futuristic skyline", "energetic"),
                ("The high-speed trains hit 350 kilometres per hour — same trip time as flying.",
                 "China Fuxing high speed train station interior sleek modern platform departure", "energetic"),
                ("Breakfast at a local street stall costs less than one US dollar.",
                 "China street food vendor steaming dumplings baozi morning local market alley", "warm"),
                ("Most cities are cleaner than you imagined — streets swept before sunrise daily.",
                 "China clean modern street pedestrian area Beijing hutong renovated morning light", "serene"),
                ("Google does not work, but Baidu Maps and WeChat handle absolutely everything.",
                 "China smartphone navigation WeChat app street map local landmark city walking", "cinematic"),
                ("The food is nothing like takeout — every province tastes completely different.",
                 "China Sichuan hotpot restaurant interior steaming broth spicy colourful evening", "warm"),
                ("Strangers will offer to help you navigate — often walking you to your destination.",
                 "China local family smiling welcoming tea ceremony traditional courtyard afternoon", "warm"),
                ("The landscapes are so dramatic they look like film sets — but they are real.",
                 "aerial China Huangshan mountains pine trees sea of clouds dramatic sunrise", "cinematic"),
                ("A one-way bullet train ticket from Beijing to Shanghai costs around fifty dollars.",
                 "China Beijing South station high speed rail platform boarding morning rush", "energetic"),
                ("Mobile payment works everywhere — you can go cashless on day one.",
                 "China WeChat Pay Alipay QR code street vendor payment seamless modern", "energetic"),
                ("Most tourist areas have English signs and English-speaking staff since 2010.",
                 "China tourist area English signage international visitors city landmark daytime", "serene"),
                ("Save this before your China trip.",
                 "China travel montage diverse landscapes city food culture fast cut vibrant", "energetic"),
            ]
        },
    ]

    candidates = [t for t in templates if t["audience_type"] == (audience_type or "explorer")]
    tmpl = random.choice(candidates) if candidates else random.choice(templates)

    # Scale scenes to hit target_seconds
    raw = tmpl["raw_scenes"]
    # Repeat or trim to hit n_scenes
    while len(raw) < n_scenes:
        raw = raw + raw
    raw = raw[:n_scenes]

    scenes = [
        ScenePlan(index=i, narration=r[0], visual_query=r[1],
                  duration=secs_per, emotion=r[2])
        for i, r in enumerate(raw)
    ]
    return CreativeBrief(
        title=tmpl["title"], description=tmpl["description"], tags=tmpl["tags"],
        topic=tmpl["topic"], audience_type=tmpl["audience_type"],
        mood=tmpl["mood"], hook=tmpl["hook"], cta=tmpl["cta"],
        scenes=scenes, target_seconds=target_seconds,
    )


# ── Core generation ───────────────────────────────────────────────────────────

def _generate_brief_via_groq(
    audience_type: str | None,
    target_seconds: float,
    critique_feedback: str = "",
    insights: str = "",
    guidelines: str = "",
) -> CreativeBrief:
    n_scenes     = math.ceil(target_seconds / SLIDE_DURATION)
    secs_per     = round(target_seconds / n_scenes, 1)

    system = _SYSTEM.format(
        target_seconds=int(target_seconds),
        n_scenes=n_scenes,
        secs_per_scene=secs_per,
    )
    # Guidelines are injected directly into the system prompt so Groq treats
    # them as hard constraints, not suggestions in the user turn.
    if guidelines:
        system = system + "\n\n" + guidelines

    user_parts = []
    if audience_type:
        user_parts.append(
            f"Generate the full {n_scenes}-scene video plan. "
            f"Audience type: {audience_type}. "
            f"Return the complete JSON object as specified."
        )
    else:
        user_parts.append(
            f"Generate the full {n_scenes}-scene video plan. "
            f"Pick the best audience type (explorer or newcomer) and topic for today. "
            f"Return the complete JSON object as specified."
        )
    if insights:
        user_parts.append(insights)
    if critique_feedback:
        user_parts.append(
            f"PREVIOUS ATTEMPT FAILED QUALITY CHECK ({CRITIC_PASS_SCORE}/10 required). "
            f"Improve based on: {critique_feedback}"
        )

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": "\n\n".join(user_parts)},
        ],
        temperature=0.75,
        max_tokens=1800,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object from Groq, got {type(data).__name__}: {str(data)[:80]}")

    raw_scenes = data.get("scenes", [])
    if not raw_scenes:
        raise ValueError("Groq returned no scenes in the brief")

    # Validate word counts — last scene is CTA (≥5 words ok), others need ≥8
    last_idx = len(raw_scenes) - 1
    short_scenes = [
        (i, s.get("narration", ""))
        for i, s in enumerate(raw_scenes)
        if len(s.get("narration", "").split()) < (5 if i == last_idx else 8)
    ]
    if short_scenes:
        examples = "; ".join(f'Scene {i}: "{n}"' for i, n in short_scenes[:3])
        raise ValueError(
            f"Narrations too short in scenes: {examples}. "
            f"Non-CTA scenes need ≥8 words; last (CTA) scene needs ≥5 words."
        )

    scenes = [
        ScenePlan(
            index=i,
            narration=s.get("narration", ""),
            visual_query=s.get("visual_query", "China landscape travel"),
            duration=float(s.get("duration", secs_per)),
            emotion=s.get("emotion", "cinematic"),
        )
        for i, s in enumerate(raw_scenes)
    ]

    return CreativeBrief(
        title=data.get("title", "Discover China"),
        description=data.get("description", ""),
        tags=data.get("tags", ["China", "Travel"]),
        topic=data.get("topic", "China travel"),
        audience_type=data.get("audience_type", audience_type or "newcomer"),
        mood=data.get("mood", "cinematic"),
        hook=data.get("hook", scenes[0].narration if scenes else ""),
        cta=data.get("cta", "Follow for more."),
        scenes=scenes,
        target_seconds=target_seconds,
    )


def _critique_brief(brief: CreativeBrief) -> tuple[int, str]:
    """Returns (score, feedback). Score < CRITIC_PASS_SCORE means retry."""
    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        narration_preview = "\n".join(
            f"Scene {s.index}: {s.narration}" for s in brief.scenes[:6]
        )
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _CRITIC_SYSTEM},
                {"role": "user",   "content": narration_preview},
            ],
            temperature=0.2,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        score = int(data.get("overall", 5))
        feedback = data.get("feedback", "")
        status = "✅ PASS" if score >= CRITIC_PASS_SCORE else "❌ FAIL"
        print(f"  [Director/Critic] Score: {score}/10 {status}  "
              f"hook={data.get('hook')} spec={data.get('specificity')} "
              f"words={data.get('word_count')} reels={data.get('reels_fit')}")
        if score < CRITIC_PASS_SCORE:
            print(f"  [Director/Critic] → {feedback}")
        return score, feedback
    except Exception as e:
        print(f"  [Director/Critic] Unavailable ({e}), skipping")
        return CRITIC_PASS_SCORE, ""


# ── Public API ────────────────────────────────────────────────────────────────

def create_brief(
    audience_type: str | None = None,
    target_seconds: float | None = None,
) -> CreativeBrief:
    """
    Create a scene-by-scene creative brief. Uses Groq with critic loop.
    Falls back to curated templates if Groq is unavailable.

    target_seconds defaults to TARGET_YOUTUBE_SECONDS from settings.
    """
    if target_seconds is None:
        target_seconds = float(TARGET_YOUTUBE_SECONDS)

    insights   = _load_insights()
    guidelines = _load_guidelines()   # human-curated rules, updated by Claude
    feedback   = ""

    for attempt in range(MAX_RETRIES + 1):
        label = "Calling Groq" if attempt == 0 else f"Retry {attempt}/{MAX_RETRIES}"
        print(f"  [Director] {label} (target={target_seconds:.0f}s, "
              f"scenes={math.ceil(target_seconds / SLIDE_DURATION)})…")
        try:
            brief = _generate_brief_via_groq(
                audience_type, target_seconds, feedback, insights, guidelines
            )
        except Exception as e:
            print(f"  [Director] Groq unavailable ({e}), using template")
            return _fallback_brief(audience_type, target_seconds)

        score, feedback = _critique_brief(brief)
        if score >= CRITIC_PASS_SCORE:
            print(f"  [Director] ✅ Brief approved: \"{brief.topic}\" "
                  f"({len(brief.scenes)} scenes)")
            return brief

    # Exhausted retries — use last generated brief anyway
    print(f"  [Director] ⚠️  Max retries — using last attempt")
    return brief
