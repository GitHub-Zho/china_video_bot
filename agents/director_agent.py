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
QWEN_TEXT_MODEL = "qwen-max"   # DashScope — China-direct, no VPN
CRITIC_PASS_SCORE = 7
MAX_RETRIES = 2


def _llm_chat(system: str, user: str, temperature: float = 0.75,
              max_tokens: int = 1800) -> str:
    """
    Text generation for the Director/Critic. Prefers Qwen (DashScope) when its key
    is set — China-direct, reliable — else falls back to Groq. Raises on failure.
    """
    dash = os.getenv("DASHSCOPE_API_KEY")
    if dash:
        import time, requests, urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        payload = {"model": QWEN_TEXT_MODEL,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   "temperature": temperature, "max_tokens": max_tokens}
        headers = {"Authorization": f"Bearer {dash}", "Content-Type": "application/json"}
        last = None
        # Retry hard — DashScope over a flaky/proxied connection drops big requests
        # with SSL EOF; a transient fail must NOT silently fall back to a generic
        # template (which would lose the user's topic).
        sess = requests.Session()
        for attempt in range(10):
            try:
                r = sess.post(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    headers=headers, json=payload,
                    verify=(attempt % 2 == 0), timeout=90)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except Exception as e:
                last = e
                time.sleep(min(8, 1.0 * (attempt + 1)))
        raise last
    # Fallback: Groq
    resp = Groq(api_key=os.getenv("GROQ_API_KEY")).chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=temperature, max_tokens=max_tokens)
    return resp.choices[0].message.content

# Narration length targets (words).
# Growth format: short punchy hooks (6-10 words). Info format: full teaching sentences (8-18 words).
# We only BLOCK 1-3 word filler (too short) and lines over the format's max.
MIN_NARRATION_WORDS = 4
MAX_NARRATION_WORDS_GROWTH = 13
MAX_NARRATION_WORDS_INFO   = 18


class BriefValidationError(Exception):
    """Soft failure — brief content didn't meet rules. Triggers a retry with
    feedback, NOT a template fallback (which would lose the user's prompt).

    Carries the offending brief so the caller can keep it as a last resort
    (a prompt-faithful brief beats a generic template)."""
    def __init__(self, message: str, brief=None):
        super().__init__(message)
        self.brief = brief


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ScenePlan:
    index:        int
    narration:    str     # spoken words for this scene
    visual_query: str     # rich cinematic description (used to JUDGE footage match)
    duration:     float   # target seconds
    emotion:      str     # cinematic | energetic | serene | dramatic | warm
    search_query: str = ""  # 2-4 plain keywords for stock SEARCH (the subject/dish/action)
    gen_prompt:   str = ""  # rich context-aware prompt for AI image/video generation

    def stock_query(self) -> str:
        """Keyword query for stock sites — falls back to visual_query."""
        return self.search_query.strip() or self.visual_query

    def generation_prompt(self) -> str:
        """Rich prompt for AI generation — falls back to narration + subject."""
        return self.gen_prompt.strip() or f"{self.narration} ({self.stock_query()})"


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
    topic_label:   str = ""   # short 2-3 word badge shown top-left, e.g. "BEIJING DUCK"

    def label(self) -> str:
        return (self.topic_label or self.topic or "").strip()

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
            "topic_label":   self.topic_label,
            "scenes": [
                {"index": s.index, "narration": s.narration,
                 "visual_query": s.visual_query, "search_query": s.search_query,
                 "gen_prompt": s.gen_prompt,
                 "duration": s.duration, "emotion": s.emotion}
                for s in self.scenes
            ],
        }

    @classmethod
    def from_metadata_dict(cls, d: dict) -> "CreativeBrief":
        """Rebuild a brief from a (possibly human-edited) metadata/brief dict."""
        scenes = [
            ScenePlan(
                index=i,
                narration=s.get("narration", ""),
                visual_query=s.get("visual_query", "China travel"),
                duration=float(s.get("duration", 4.0)),
                emotion=s.get("emotion", "cinematic"),
                search_query=s.get("search_query", ""),
                gen_prompt=s.get("gen_prompt", ""),
            )
            for i, s in enumerate(d.get("scenes", []))
        ]
        return cls(
            title=d.get("title", "Discover China"),
            description=d.get("description", ""),
            tags=d.get("tags", ["China", "Travel"]),
            topic=d.get("topic", "China travel"),
            audience_type=d.get("audience_type", "explorer"),
            mood=d.get("mood", "cinematic"),
            hook=d.get("hook", scenes[0].narration if scenes else ""),
            cta=d.get("cta", "Follow for more."),
            scenes=scenes,
            target_seconds=float(d.get("target_seconds", 32)),
            topic_label=d.get("topic_label", ""),
        )


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


def _load_guidelines(video_type: str = "growth") -> str:
    """
    Load human-curated creative guidelines, including the block for the chosen
    video_type ("growth" or "info"). Shared do/avoid apply to both; the formats
    section adds type-specific voice/structure/interaction rules.
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
        if g.get("persona"):
            lines.append("WHO YOU ARE: " + g["persona"])

        # Format-specific block (growth vs info)
        fmt = (g.get("formats") or {}).get(video_type)
        if fmt:
            lines.append(f"\n=== THIS VIDEO'S FORMAT: {video_type.upper()} ===")
            lines.append(f"Goal: {fmt.get('goal','')}")
            lines.append(f"Voice: {fmt.get('voice','')}")
            if fmt.get("use_interaction_hook"):
                lines.append("Interaction hook: YES — end with comment/like bait "
                             "(open question, 'name in comments', mild debate).")
                if fmt.get("cta_patterns"):
                    lines.append("CTA options: " + " | ".join(fmt["cta_patterns"]))
            else:
                lines.append("Interaction hook: NO — do NOT bait comments/likes. "
                             "End calm and reflective.")
                if fmt.get("cta_patterns"):
                    lines.append("CTA options: " + " | ".join(fmt["cta_patterns"]))
            if fmt.get("hook_patterns"):
                lines.append("Hook patterns: " + " | ".join(fmt["hook_patterns"]))
            if fmt.get("example_good_narrations"):
                lines.append(f"GOOD {video_type} narration examples:")
                lines.extend(f'  "{ex}"' for ex in fmt["example_good_narrations"])

        if g.get("do"):
            lines.append("\nRULES — always do these (both formats):")
            lines.extend(f"  ✓ {rule}" for rule in g["do"])
        if g.get("avoid"):
            lines.append("RULES — never do these (both formats):")
            lines.extend(f"  ✗ {rule}" for rule in g["avoid"])
        if g.get("example_bad_narrations"):
            lines.append("BAD narration examples (never write like this):")
            lines.extend(f'  "{ex}"' for ex in g["example_bad_narrations"])

        result = "\n".join(lines)
        print(f"  [Director] 📋 Guidelines v{g.get('version',1)} loaded "
              f"(format={video_type}, {len(g.get('do',[]))} shared rules)")
        return result
    except Exception as e:
        print(f"  [Director] ⚠️  Could not load guidelines: {e}")
        return ""


# ── Groq director prompt ──────────────────────────────────────────────────────

_SYSTEM = """\
You are DIRECTOR + ART DIRECTOR for Instagram Reels / YouTube Shorts China travel videos.
Target: {target_seconds}-second videos with {n_scenes} scenes × {secs_per_scene}s each.

NARRATION LENGTH — follow the guidelines' format rules exactly:
  • growth format: 6-10 words — punchy hooks, incomplete-feeling, leaves gaps
  • info format:   8-18 words — complete teaching sentences; explain WHY not just WHAT
  BAD (any format): "Explore the unknown" (generic, no China detail)
  GOOD growth: "Three dollars. That's what breakfast costs here." (7 words, price shock)
  GOOD info:   "They inflate the skin with air first — so the fat melts away from the surface." (complete explanation with reason)

STRICT scene rules (ALL must pass):
1. narration length — see format rules above; info lines are COMPLETE SENTENCES with WHY
2. Scene 0 hook: question or tension-starter that the viewer NEEDS answered
3. Each scene flows into the next — leave a gap or build curiosity, never conclude too early
4. visual_query: 10-15 words, China location + time of day + light mood + one human/motion detail
5. Last scene: a closing line that creates LONGING or quiet reflection (info) or comment-bait (growth)

ART DIRECTION — gen_prompt rules (20-35 words, one per scene):
You know the WHOLE topic. Frame each prompt around ONE focal point:
  • ACTION line (slicing, pouring, wrapping): EXTREME CLOSE-UP on hands + action. NO face.
  • TEXTURE/SENSORY line (crispy, glossy, steam): MACRO close-up on that texture, shallow depth, no clutter.
  • PLACE/DISH line: clean hero shot of THAT subject only, no distracting background.
  Examples (topic = Beijing roast duck):
    narration "108 slices by hand" → "Extreme close-up of a knife slicing lacquered Peking roast duck into thin glistening slices on a wood board, crispy skin, hands only, no face, warm light, shallow depth of field"
    narration "skin shatters like glass" → "Macro close-up of glossy mahogany Peking duck skin, oil sheen glistening, crispy crackled texture, steam rising, shallow depth, no people, no background clutter"
  NEVER: vague "beautiful food", faces when the point is hands, unrelated background.

Return ONLY valid JSON — no markdown, no explanation:
{{
  "title": "...",
  "description": "...(2-3 sentences + 5 hashtags)",
  "tags": ["...", "...", "...", "...", "..."],
  "topic": "...",
  "audience_type": "explorer|newcomer",
  "mood": "cinematic|energetic|serene|dramatic",
  "hook": "...(6-10 word question or tension-starter)",
  "cta": "...",
  "topic_label": "...(2-3 word UPPERCASE badge naming the subject, e.g. 'BEIJING ROAST DUCK', 'GUILIN')",
  "scenes": [
    {{
      "narration": "...(6-10 words, punchy, leaves viewer wanting more)",
      "visual_query": "...(rich cinematic description, used to judge footage match)",
      "search_query": "...(2-4 PLAIN keywords a stock-video site will match — the SUBJECT/dish/action only, ALWAYS naming the food. For food, describe the COOKED DISH: e.g. 'roasted duck dish food', 'sliced roast duck plate', 'roast duck restaurant'. NEVER bare 'duck' (returns live ducks). NEVER ambiguous words like 'carving'/'carving tradition' (returns STONE carving) — say 'sliced roast duck' instead. NEVER place names like 'Nanluogu Xiang' (stock has none). Always include the food word in EVERY scene's query.)",
      "gen_prompt": "...(20-35 word tight-shot AI image prompt — follow ART DIRECTION rules above)",
      "duration": {secs_per_scene},
      "emotion": "cinematic|energetic|serene|dramatic|warm"
    }},
    ...
  ]
}}"""

_CRITIC_SYSTEM = """\
Score this SHORT-FORM Reels/Shorts script on 5 criteria (1-10 each).
SHORT, punchy lines (4-10 words) are GOOD here — do NOT penalise brevity.
  hook        — does scene 0 stop the scroll? (a question, sensory tease, or insider contrast)
  specificity — concrete subject/detail (a real dish, place, or action), not vague filler
  punch       — are lines tight and craveable (4-10 words)? Penalise ONLY 1-3 word filler
                like "Explore the unknown", and overly long documentary sentences
  reels_fit   — sounds like a real enthusiast, not a brochure or an ad ("come to X / I'll take you" = bad)
  cohesion    — do all scenes stay on the ONE topic (no drift to unrelated places)?

overall = average of the 5 scores (round to nearest integer). A clean punchy
on-topic script should score 7-9.

Return ONLY JSON:
{{"hook":<n>,"specificity":<n>,"punch":<n>,"reels_fit":<n>,"cohesion":<n>,
  "overall":<n>,"feedback":"<2 sentences naming exactly what to fix>"}}"""


# ── Fallback templates ────────────────────────────────────────────────────────

def _fallback_brief(audience_type: str | None, target_seconds: float,
                    prompt: str = "") -> CreativeBrief:
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
            "hook": "Most tourists never see this side of China.",
            "cta": "Follow for more hidden China.",
            "raw_scenes": [
                ("Most tourists never see this side of China.",
                 "aerial China Zhangjiajie sandstone pillars dawn golden mist swirling", "dramatic"),
                ("Fenghuang Town is two thousand years old.",
                 "China Fenghuang ancient town wooden stilt houses river reflection sunrise warm", "warm"),
                ("Yunnan's rice terraces glow gold every October.",
                 "China Yuanyang Hani rice terraces golden sunset aerial panoramic harvest", "cinematic"),
                ("Guilin's Li River looks like an ink painting.",
                 "China Li River Guilin cormorant fisherman karst peaks morning mist boat", "serene"),
                ("Watch pandas eat bamboo for five dollars.",
                 "China Chengdu giant panda research base bamboo close up morning light", "warm"),
                ("Rainbow mountains, twenty million years in the making.",
                 "China Zhangye Danxia colourful rainbow mountains aerial wide shot golden hour", "dramatic"),
                ("Shanghai's Bund at midnight looks like sci-fi.",
                 "China Shanghai Bund skyline night reflections Pudong neon lights river", "energetic"),
                ("A whole night-market meal costs three dollars.",
                 "China Chengdu night market street food stalls bustling vendors lanterns evening", "energetic"),
                ("Wuzhen water town has no cars, only canals.",
                 "China Wuzhen water town canals stone bridges reflection lanterns dusk serene", "serene"),
                ("Huangshan's peaks float above a sea of clouds.",
                 "aerial China Huangshan yellow mountain pine trees sea of clouds sunrise fog", "cinematic"),
                ("Cycle Xi'an's ancient wall, all fourteen kilometres.",
                 "China Xian ancient city wall cycling sunset golden light panoramic dusk", "energetic"),
                ("Follow for more hidden corners of China.",
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
            "hook": "China is nothing like what you were told.",
            "cta": "Save this before your China trip.",
            "raw_scenes": [
                ("China is nothing like what you were told.",
                 "China modern city Shanghai high rise buildings blue sky futuristic skyline", "energetic"),
                ("Bullet trains hit 350 kilometres per hour.",
                 "China Fuxing high speed train station interior sleek modern platform departure", "energetic"),
                ("Street breakfast costs less than one dollar.",
                 "China street food vendor steaming dumplings baozi morning local market alley", "warm"),
                ("Cities are cleaner than you ever imagined.",
                 "China clean modern street pedestrian area Beijing hutong renovated morning light", "serene"),
                ("Google fails here, but WeChat handles everything.",
                 "China smartphone navigation WeChat app street map local landmark city walking", "cinematic"),
                ("Every province's food tastes completely different.",
                 "China Sichuan hotpot restaurant interior steaming broth spicy colourful evening", "warm"),
                ("Strangers will walk you to your destination.",
                 "China local family smiling welcoming tea ceremony traditional courtyard afternoon", "warm"),
                ("The landscapes look like film sets, but real.",
                 "aerial China Huangshan mountains pine trees sea of clouds dramatic sunrise", "cinematic"),
                ("Beijing to Shanghai by train: fifty dollars.",
                 "China Beijing South station high speed rail platform boarding morning rush", "energetic"),
                ("Pay everywhere by phone, go cashless instantly.",
                 "China WeChat Pay Alipay QR code street vendor payment seamless modern", "energetic"),
                ("Tourist areas have English signs everywhere now.",
                 "China tourist area English signage international visitors city landmark daytime", "serene"),
                ("Save this before your China trip.",
                 "China travel montage diverse landscapes city food culture fast cut vibrant", "energetic"),
            ]
        },
    ]

    candidates = [t for t in templates if t["audience_type"] == (audience_type or "explorer")]
    tmpl = random.choice(candidates) if candidates else random.choice(templates)
    if prompt:
        print(f"  [Director] ⚠️  Template fallback can't honor prompt '{prompt[:40]}' "
              f"— using generic '{tmpl['topic']}'")

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
    prompt: str = "",
    video_type: str = "growth",
    video_understanding=None,   # VideoUnderstanding | None
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
    # Mode 2: inject video understanding BEFORE the topic prompt so it acts as
    # ground truth for the Director.  The to_director_prompt() block contains
    # the ordered steps and an explicit instruction to write from real content.
    if video_understanding is not None:
        user_parts.insert(0, video_understanding.to_director_prompt())

    if prompt:
        user_parts.insert(0,
            f"CREATIVE DIRECTION FROM USER (highest priority — the video MUST be "
            f"about this): {prompt}")
    else:
        # Only avoid recent topics when the user didn't pin a specific prompt.
        try:
            from agents.topic_guard import avoid_clause
            clause = avoid_clause()
            if clause:
                user_parts.append(clause)
        except Exception:
            pass
    if insights:
        user_parts.append(insights)
    if critique_feedback:
        user_parts.append(
            f"PREVIOUS ATTEMPT FAILED QUALITY CHECK ({CRITIC_PASS_SCORE}/10 required). "
            f"Improve based on: {critique_feedback}"
        )

    raw = _llm_chat(system, "\n\n".join(user_parts),
                    temperature=0.75, max_tokens=2400).strip()
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

    scenes = [
        ScenePlan(
            index=i,
            narration=s.get("narration", ""),
            visual_query=s.get("visual_query", "China landscape travel"),
            duration=float(s.get("duration", secs_per)),
            emotion=s.get("emotion", "cinematic"),
            search_query=s.get("search_query", ""),
            gen_prompt=s.get("gen_prompt", ""),   # Art Direction merged into Director
        )
        for i, s in enumerate(raw_scenes)
    ]

    brief = CreativeBrief(
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
        topic_label=data.get("topic_label", ""),
    )

    # Validate word counts — format-aware limits.
    # Info format allows fuller teaching sentences; growth needs short punchy hooks.
    max_words = MAX_NARRATION_WORDS_INFO if video_type == "info" else MAX_NARRATION_WORDS_GROWTH
    bad = []
    for i, s in enumerate(raw_scenes):
        wc = len(s.get("narration", "").split())
        if wc < MIN_NARRATION_WORDS:
            bad.append((i, s.get("narration", ""), f"{wc}w too short"))
        elif wc > max_words:
            bad.append((i, s.get("narration", ""), f"{wc}w too long"))
    if bad:
        examples = "; ".join(f'Scene {i} ({why}): "{n}"' for i, n, why in bad[:3])
        raise BriefValidationError(
            f"Narrations must be {MIN_NARRATION_WORDS}-{max_words} words ({video_type} format). "
            f"Problems: {examples}. Rewrite ALL narrations to this length.",
            brief=brief,   # keep it — a prompt-faithful brief beats a generic template
        )

    return brief


def _critique_brief(brief: CreativeBrief) -> tuple[int, str]:
    """Returns (score, feedback). Score < CRITIC_PASS_SCORE means retry."""
    try:
        narration_preview = "\n".join(
            f"Scene {s.index}: {s.narration}" for s in brief.scenes[:6]
        )
        raw = _llm_chat(_CRITIC_SYSTEM, narration_preview,
                        temperature=0.2, max_tokens=300).strip()
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
              f"punch={data.get('punch')} reels={data.get('reels_fit')}")
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
    prompt: str = "",
    video_type: str = "growth",
    video_understanding=None,   # VideoUnderstanding | None  (Mode 2)
) -> CreativeBrief:
    """
    Create a scene-by-scene creative brief. Uses Qwen/Groq with critic loop.
    Falls back to curated templates if LLM is unavailable.

    prompt:              optional free-text creative direction.
    video_type:          "growth" (hook/engagement) or "info" (educational story).
    video_understanding: (Mode 2) VideoUnderstanding from video_analyst_agent.
                         When provided, the Director writes from actual video steps
                         instead of freely inventing. Auto-switches to "info" format.
    target_seconds defaults to TARGET_YOUTUBE_SECONDS from settings.
    """
    if target_seconds is None:
        target_seconds = float(TARGET_YOUTUBE_SECONDS)

    # Mode 2: video-grounded scripts read better as "info" (teaching voice)
    if video_understanding is not None and video_type == "growth":
        video_type = "info"

    insights   = _load_insights()
    guidelines = _load_guidelines(video_type)   # format-aware rules
    feedback   = ""

    brief = None
    last_attempt = None   # last brief produced, even if it failed validation
    for attempt in range(MAX_RETRIES + 1):
        prov = "Qwen" if os.getenv("DASHSCOPE_API_KEY") else "Groq"
        label = f"Calling {prov}" if attempt == 0 else f"Retry {attempt}/{MAX_RETRIES}"
        print(f"  [Director] {label} (target={target_seconds:.0f}s, "
              f"scenes={math.ceil(target_seconds / SLIDE_DURATION)})…")
        try:
            brief = _generate_brief_via_groq(
                audience_type, target_seconds, feedback, insights, guidelines, prompt,
                video_type=video_type,
                video_understanding=video_understanding,
            )
            last_attempt = brief
        except BriefValidationError as e:
            # Soft failure — retry with the validation message as feedback.
            print(f"  [Director] ✗ Validation: {e}")
            feedback = str(e)
            if e.brief is not None:
                last_attempt = e.brief   # keep it as a prompt-faithful fallback
            continue
        except Exception as e:
            print(f"  [Director] Groq unavailable ({e}), using template")
            return _fallback_brief(audience_type, target_seconds, prompt)

        score, feedback = _critique_brief(brief)
        if score >= CRITIC_PASS_SCORE:
            print(f"  [Director] ✅ Brief approved: \"{brief.topic}\" "
                  f"({len(brief.scenes)} scenes)")
            return brief

    # Exhausted retries. Prefer the last on-topic attempt over a generic template —
    # especially when the user pinned a prompt (a roast-duck script with one slightly
    # short line beats a generic "hidden China" template).
    if last_attempt is not None:
        print(f"  [Director] ⚠️  Max retries — using last on-topic attempt "
              f"(\"{last_attempt.topic}\")")
        return last_attempt
    if brief is not None:
        return brief
    print(f"  [Director] ⚠️  No valid brief — using template")
    return _fallback_brief(audience_type, target_seconds, prompt)
