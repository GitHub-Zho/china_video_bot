"""
Critic Agent — scores a script before committing resources to full generation.

Evaluates:
  - Hook strength     (does the first sentence grab attention?)
  - Specificity       (real place names, not vague "hidden gems")
  - Pacing            (word count vs target duration)
  - Reels fit         (conversational? punchy? not documentary-ish?)
  - Audience match    (explorer vs newcomer tone)

Returns CritiqueResult with a 1-10 score and actionable feedback.
Script Agent retries with the feedback if score < PASS_SCORE (default 7).
Max retries: 2 (to avoid infinite loops).
"""
import json
import os
from dataclasses import dataclass
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

GROQ_MODEL  = "llama-3.3-70b-versatile"
PASS_SCORE  = 7     # minimum score to proceed
MAX_RETRIES = 2     # max regeneration attempts


@dataclass
class CritiqueResult:
    score:     int          # 1-10
    passed:    bool         # score >= PASS_SCORE
    feedback:  str          # specific improvement instructions for next attempt
    breakdown: dict         # {criterion: score} for logging


_SYSTEM = """You are a ruthless short-form travel content critic.
Score a YouTube Shorts / Instagram Reels script on these 5 criteria (each 1-10):
  hook        — does the first sentence make someone stop scrolling?
  specificity — are real, specific places/facts mentioned (not vague generalities)?
  pacing      — is word count appropriate for the target duration (short = punchy)?
  reels_fit   — conversational tone, not a documentary? Would sound natural fast?
  audience    — matches the intended audience (explorer or newcomer)?

Return ONLY valid JSON with this exact structure:
{
  "hook": <1-10>,
  "specificity": <1-10>,
  "pacing": <1-10>,
  "reels_fit": <1-10>,
  "audience": <1-10>,
  "overall": <1-10>,
  "feedback": "<2-3 sentences of specific improvement instructions>"
}
Be harsh. A score of 8+ means it is genuinely compelling content."""


def critique_script(
    script:        str,
    topic:         str,
    audience_type: str,
    target_seconds: int = 60,
) -> CritiqueResult:
    """
    Score the script. Returns CritiqueResult.
    Falls back to a passing result if Groq is unavailable (don't block pipeline).
    """
    word_count = len(script.split())

    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        user_msg = (
            f"Topic: {topic}\n"
            f"Audience: {audience_type}\n"
            f"Target duration: {target_seconds}s  (ideal word count: {target_seconds * 2 // 3}–{target_seconds})\n"
            f"Actual word count: {word_count}\n\n"
            f"Script:\n{script}"
        )

        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=400,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        data = json.loads(raw.strip())
        score = int(data.get("overall", 5))
        breakdown = {k: v for k, v in data.items()
                     if k not in ("overall", "feedback")}

        result = CritiqueResult(
            score=score,
            passed=(score >= PASS_SCORE),
            feedback=data.get("feedback", ""),
            breakdown=breakdown,
        )

        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"  [Critic] Score: {score}/10 {status}  |  "
              f"hook={breakdown.get('hook')} spec={breakdown.get('specificity')} "
              f"pace={breakdown.get('pacing')} reels={breakdown.get('reels_fit')}")
        if not result.passed:
            print(f"  [Critic] Feedback: {result.feedback}")

        return result

    except Exception as e:
        print(f"  [Critic] Unavailable ({e}), skipping critique")
        # Fail-open: let the pipeline continue
        return CritiqueResult(
            score=8,
            passed=True,
            feedback="",
            breakdown={},
        )
