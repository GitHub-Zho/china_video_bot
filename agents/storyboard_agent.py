"""
Storyboard Agent — maps a script to per-scene visual instructions.

Input:  script text + topic + mood
Output: list of Scene objects with precise visual search queries,
        duration hints, and emotional tone per scene.

This replaces generic image_queries with shot-level direction so
the media agent fetches footage that matches what's being said.
"""
import json
import os
from dataclasses import dataclass
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

GROQ_MODEL = "llama-3.3-70b-versatile"


@dataclass
class Scene:
    index:        int
    narration:    str     # the spoken words for this scene
    visual_query: str     # precise search query for footage
    duration:     float   # target seconds (3-7)
    emotion:      str     # cinematic / energetic / serene / dramatic


# ── Fallback storyboard (used if Groq is unavailable) ─────────────────────────

def _fallback_storyboard(queries: list[str]) -> list[Scene]:
    """Turn plain image_queries into basic Scene objects."""
    return [
        Scene(
            index=i,
            narration="",
            visual_query=q,
            duration=5.0,
            emotion="cinematic",
        )
        for i, q in enumerate(queries)
    ]


# ── Groq storyboard generation ─────────────────────────────────────────────────

_SYSTEM = """You are a video storyboard director specializing in short-form travel content.
Given a voiceover script, break it into visual scenes.
For each scene return ONLY valid JSON — an array of objects with these exact keys:
  narration    (string) the spoken words for this moment
  visual_query (string) precise footage search query (10-15 words, specific location/action/mood)
  duration     (number) seconds this scene lasts (3 to 7)
  emotion      (string) one of: cinematic | energetic | serene | dramatic | warm

Rules:
- Make visual_query ultra-specific: "aerial Zhangjiajie sandstone pillars morning mist" not "China mountains"
- Duration should be proportional to narration length (faster speech = shorter scene)
- Total duration of all scenes should roughly match the audio length hint provided
- Always prepend "China" if the location is in China
- Output ONLY the JSON array, no other text."""


def generate_storyboard(
    script: str,
    topic: str,
    mood: str,
    audio_duration: float,
    image_queries: list[str],
) -> list[Scene]:
    """
    Generate a scene-by-scene storyboard from the script.
    Falls back to basic scenes if Groq is unavailable.
    """
    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        user_msg = f"""Script topic: {topic}
Mood: {mood}
Estimated audio duration: {audio_duration:.0f} seconds
Original visual queries (use as inspiration, improve them): {json.dumps(image_queries)}

Script to storyboard:
{script}

Create one scene per major thought/sentence. Return JSON array only."""

        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.6,
            max_tokens=1200,
        )

        raw = resp.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)
        scenes = [
            Scene(
                index=i,
                narration=s.get("narration", ""),
                visual_query=s.get("visual_query", image_queries[i % len(image_queries)]),
                duration=float(s.get("duration", 5.0)),
                emotion=s.get("emotion", "cinematic"),
            )
            for i, s in enumerate(data)
        ]
        print(f"  [Storyboard] {len(scenes)} scenes planned for {audio_duration:.0f}s audio")
        return scenes

    except Exception as e:
        print(f"  [Storyboard] Groq unavailable ({e}), using basic fallback")
        return _fallback_storyboard(image_queries)
