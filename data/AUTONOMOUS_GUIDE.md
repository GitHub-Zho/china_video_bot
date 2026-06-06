# China Video Bot — Autonomous Operation Guide

> The operations manual. Read this to run, understand, and improve the system
> **without Claude in the loop**. For the build history and design rationale, see
> `data/ROADMAP.md`. For the decision log, see `data/learning_log.md`.

---

## ⚠️ 0. CRITICAL: API geo-restrictions (read first)

Both default model providers are **geo-blocked in mainland China**:
- **Gemini API** returns `400 "User location is not supported"` from China.
- **Groq API** returns `403 Forbidden` from China.

The pipeline still BUILDS videos when these fail (graceful degradation: dedup
instead of vision-selection, no QA), but generation (Director) needs Groq and
verification needs Gemini — so for full quality you must use ONE of:

1. **Run on the Oracle/AWS server** (US/international region) — Groq + Gemini work
   normally there. The geo-block only affects testing from a China IP. ← simplest
2. **Use a VPN** when testing locally from China.
3. **China-native stack (no VPN):** switch generation + verification to a
   provider that works in China:
   - LLM (Director): Alibaba Qwen (DashScope) or DeepSeek — both China-accessible.
   - Vision (verify/QA): **Qwen2.5-VL** via DashScope — excellent on Chinese
     scenes. `agents/vision.py` is the single swap point for the verifier.
   Needs an Alibaba DashScope API key (account may require a Chinese phone).

---

## 1. What this is

A self-driving pipeline that turns a topic into a finished short-form China travel
video (YouTube 16:9 + Instagram Reels 9:16), with English voiceover, burned
subtitles, a hook card, and auto quality control. Every model used is **free**.

```
prompt → Director(Groq) → Voice(Kokoro) → Media(Pexels+Pixabay, Gemini-picked)
       → Assemble(FFmpeg) → QA(Gemini) → auto-fix subtitles → Publish(YouTube)
```

---

## 2. How to run

```bash
# Daily auto mode — Director picks topic, avoids recent ones, publishes
python scripts/run.py

# Build locally without uploading
python scripts/run.py --dry-run

# Topic-driven
python scripts/run.py --prompt "Chengdu hot pot for first-timers" --dry-run
python scripts/run.py --prompt "Guilin Li River" --audience explorer

# From your own photos/clips (Gemini writes narration to match them)
python scripts/run.py --from-folder ~/my_china_photos --dry-run

# Override length
python scripts/run.py --prompt "Xi'an" --seconds 24 --dry-run

# Style: learn a reference video's style, then imitate it
python scripts/run.py --learn-style /path/to/reference.mp4 my_style
python scripts/run.py --learn-style "https://youtube.com/shorts/XXXX" my_style
python scripts/run.py --prompt "Lijiang" --style my_style --dry-run
```

Exit code is 0 on success, 1 on failure (so cron/CI can detect problems).

---

## 3. Setup (server: AWS / Oracle Ubuntu)

```bash
# System deps — ffmpeg MUST have libfreetype (drawtext filter)
sudo apt update && sudo apt install -y ffmpeg fonts-dejavu
ffmpeg -filters | grep drawtext     # must print a line; if empty, see §7

# Python deps
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Kokoro TTS model files (~350MB, one-time) → ~/.cache/kokoro/
python3 - <<'PY'
import requests, os
os.makedirs(os.path.expanduser("~/.cache/kokoro"), exist_ok=True)
base="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
for f in ("kokoro-v1.0.onnx","voices-v1.0.bin"):
    r=requests.get(base+f); open(os.path.expanduser(f"~/.cache/kokoro/{f}"),"wb").write(r.content)
PY
```

### API keys (.env file, never commit it)
```
GROQ_API_KEY=...         # console.groq.com — LLM generation (free)
GEMINI_API_KEY=...       # aistudio.google.com/apikey — vision verifier (free)
PEXELS_API_KEY=...       # pexels.com/api — video + photos (free)
PIXABAY_API_KEY=...      # pixabay.com/api/docs — second video source (free)
UNSPLASH_ACCESS_KEY=...  # unsplash.com/developers — photo fallback (free)
# YouTube (only needed to publish, not for --dry-run):
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
YOUTUBE_REFRESH_TOKEN=...
```
All vision/LLM features degrade gracefully if a key is missing — the video still
builds, just without that enhancement.

### Daily schedule (cron)
```bash
# 9:00 AM UTC daily
0 9 * * * cd /path/to/china_video_bot && .venv/bin/python scripts/run.py >> logs/run.log 2>&1
```

---

## 4. What each agent does

| File | Role | Model |
|------|------|-------|
| `agents/director_agent.py` | Plans scenes: narration + visual query per scene | Groq (text) |
| `agents/topic_guard.py` | Lists recent topics so Director avoids repeats | — |
| `agents/voice_agent.py` | TTS → MP3 + per-scene SRT timing | Kokoro (local) |
| `agents/media_agent.py` | Downloads clips, dedups, Gemini picks best match | Gemini (vision) |
| `agents/video_agent.py` | FFmpeg assembly, hook card, subtitles, re-burn | — |
| `agents/qa_agent.py` | Samples frames, finds issues, suggests param fixes | Gemini (vision) |
| `agents/vision.py` | Shared verifier wrapper (swap model in one place) | Gemini |
| `agents/style_analyst_agent.py` | Learn/imitate a reference video's style | Gemini + ffprobe |
| `agents/analytics_agent.py` | YouTube metrics → insights for the Director | — |
| `agents/publisher_agent.py` | Upload to YouTube | — |
| `orchestrator.py` | Wires it all together (`run_pipeline`) | — |

**Design principle:** the *generator* (Groq) and the *verifier* (Gemini) are
different models on purpose — independent checks, no shared blind spots.

---

## 5. The learning loop (how it improves)

```
Generate video
   ↓ QA (Gemini) finds issues → auto-fixes THIS video's subtitles (per-video)
   ↓ also logs issues to data/learning_log.md
Publish → (3 days later) analytics_agent collects YouTube metrics
   ↓ extract_insights() → data/insights.json (high-CTR topics, what to avoid)
Next run: Director reads insights.json + director_guidelines.json
```

### Three knowledge files
- **`data/director_guidelines.json`** — creative rules. Director reads it every run.
  Edit this to change how scripts are written. Bump `version` when you do.
- **`data/insights.json`** — auto-generated from YouTube performance.
- **`data/learning_log.md`** — human-readable audit trail of every change + QA finding.

---

## 6. How to give the bot feedback (without Claude)

1. Watch a generated video in `output/<id>/`.
2. Decide what's wrong (e.g. "narrations too generic", "hook too weak").
3. Edit `data/director_guidelines.json`:
   - add a rule to `do` or `avoid`
   - add a good/bad example
   - bump `version` + update `updated_reason`
4. Next run picks it up automatically — no code change needed.

To change a **technical** default (subtitle size, video length, hook duration),
edit `config/settings.py`. Per-video QA auto-tuning handles one-offs; edit the
default only if the SAME issue appears across many videos.

---

## 7. Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| Subtitles missing / `Subtitle mode: copy` | ffmpeg lacks drawtext. Install conda-forge ffmpeg or `apt install ffmpeg` (with libfreetype). The code auto-picks the ffmpeg next to your Python. |
| `[Vision] Gemini 429` everywhere | Free-tier rate/daily limit hit. It degrades gracefully (dedup + default subtitles). Switch `GEMINI_VISION_MODEL` in settings to another flash variant, or wait for the daily reset. |
| Director falls back to template | Groq down or narrations failed validation twice. Template still produces a valid video; check GROQ_API_KEY. |
| Repeated clips | Should not happen (dedup). If it does, the query pool was too small — broaden visual queries in guidelines. |
| Voice sounds flat | Kokoro model files missing → it fell back to edge-tts. Re-download model files (§3). |
| Upload fails `youtubeSignupRequired` | The Google account has no YouTube channel yet — create one at youtube.com. |

---

## 8. Cost = $0

| Service | Free tier used |
|---------|----------------|
| Groq | LLM generation (14,400 req/day free) |
| Gemini 2.5 Flash-Lite | Vision verify/QA (~15 RPM, 1000+/day) |
| Pexels / Pixabay / Unsplash | Media (free, commercial use) |
| Kokoro | TTS (local, no API) |
| YouTube Data API | Publishing (free quota) |

**Optional paid upgrade** (only if you want higher quality later): swap the
verifier to Anthropic Claude vision — change `GEMINI_VISION_MODEL` usage in
`agents/vision.py`. The generator stays on Groq. See ROADMAP §1.5.
