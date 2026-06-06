# 🇨🇳 China Video Bot

Give it a topic → it writes a script, finds matching footage, adds an AI voiceover
and captions, assembles **YouTube (16:9) + Instagram Reels (9:16)** videos, quality-
checks them, and (optionally) publishes to YouTube. Short-form China travel/culture
content, ~25–35s, fully automated, **100% free APIs**.

> **New here?** This README is the operating manual. For deeper detail:
> `data/AUTONOMOUS_GUIDE.md` (server ops), `data/ROADMAP.md` (architecture + build
> history + design rationale), `data/learning_log.md` (every decision made & why).

---

## Quick start

```bash
# 0. one-time setup (see "Setup" below), then:

# Recommended: generate the SCRIPT first, review it, then build the video
python scripts/run.py --prompt "Beijing roast duck" --review
#   → prints the 8-scene script + saves output/<id>/brief.json, then STOPS
#   → read/edit brief.json if you want, then:
python scripts/run.py --from-brief output/<id>/brief.json --dry-run
#   → builds locally (no upload). Files: output/<id>/youtube.mp4 + reels.mp4

# Or one-shot (no review):
python scripts/run.py --prompt "Guilin Li River" --dry-run

# Fully automatic (Director picks a fresh topic, avoids recent ones, publishes):
python scripts/run.py
```

`--dry-run` builds locally and skips the YouTube upload. Drop it to publish.

---

## How it works (the pipeline)

```
You give a topic (--prompt)
   │
[1] DIRECTOR (Groq LLM) writes the script
      · 8 scenes, each = one narration line + one footage search query
      · reads 3 knowledge sources: guidelines.json + insights.json + style (optional)
      · self-critiques and rewrites if weak; only states facts it's confident are true
      │
      └─► --review STOPS HERE so you can read/edit the script   ◄── YOU (touchpoint 1)
   │
[2] VOICE (Kokoro, local TTS) → MP3 + per-scene subtitle timing
   │
[3] MEDIA → Pexels + Pixabay candidates per scene
      · de-duplicated across scenes (no repeated clips)
      · Gemini "pick best" chooses the candidate that matches the scene
   │
[4] VIDEO (FFmpeg) assembles both formats
      · each clip trimmed to its narration length (audio/subtitle in sync)
      · 2s hook card up front, Anton-font captions, auto-sized per aspect ratio
   │
[QC] Gemini reviews sampled frames
      · subtitle problems → auto-fixed by re-burning (no full re-render)
      · footage doesn't match narration → flagged + 3 alternatives downloaded  ◄── YOU (touchpoint 2)
   │
[5] PUBLISH to YouTube (skipped with --dry-run)
      · 3 days later, analytics → insights.json → feeds back into the Director
```

---

## The two places you step in

**1. Script review (before any media is fetched)**
```bash
python scripts/run.py --prompt "..." --review     # see script, stop
# edit output/<id>/brief.json if needed, then:
python scripts/run.py --from-brief output/<id>/brief.json --dry-run
```
Catches bad/hallucinated lines before spending time on voice/media/video.

**2. Fix a content mismatch (after build)**
If QC finds footage that doesn't match the narration, it writes
`output/<id>/review.json` and downloads 3 alternatives to
`output/<id>/alternatives/scene_NN/`. Look at the `alt_*.jpg` previews, pick one:
```bash
python scripts/run.py --fix <id> --scene 6 --pick 2   # use alt_2 for scene 6, rebuild
```

---

## All commands

```bash
python scripts/run.py                                # auto daily mode (publishes)
python scripts/run.py --dry-run                      # auto, build only
python scripts/run.py --prompt "TOPIC" --review      # script-first (recommended)
python scripts/run.py --from-brief PATH --dry-run    # build an approved/edited script
python scripts/run.py --prompt "TOPIC" --dry-run     # one-shot, no review
python scripts/run.py --audience newcomer            # explorer | newcomer
python scripts/run.py --seconds 24                   # target length
python scripts/run.py --from-folder ~/photos         # use YOUR images/clips, not stock
python scripts/run.py --learn-style ref.mp4 NAME     # learn a reference video's style
python scripts/run.py --prompt "..." --style NAME    # imitate a learned style
python scripts/run.py --fix <id> --scene N --pick K  # swap a flagged scene's clip
```

---

## Agents (what's in `agents/`)

| File | Role | Model |
|------|------|-------|
| `director_agent.py` | Plans scenes (narration + visual query); self-critique loop | Groq (text) |
| `topic_guard.py` | Lists recent topics so the Director avoids repeats | — |
| `voice_agent.py` | TTS → MP3 + per-scene SRT timing | Kokoro (local) |
| `media_agent.py` | Download clips, dedup, pick best match, fetch alternatives | Gemini (vision) |
| `video_agent.py` | FFmpeg assembly, hook card, captions, subtitle re-burn | — |
| `qa_agent.py` | Frame sampling → subtitle + content-mismatch detection → auto-fix | Gemini (vision) |
| `vision.py` | Shared verifier wrapper (Gemini, Groq vision fallback) | Gemini/Groq |
| `style_analyst_agent.py` | Learn & imitate a reference video's style | Gemini + ffprobe |
| `media_analyst_agent.py` | Turn YOUR photos into a matched script (`--from-folder`) | Gemini (vision) |
| `analytics_agent.py` | YouTube metrics → insights for the Director | — |
| `publisher_agent.py` | Upload to YouTube | — |
| `orchestrator.py` | Wires it together (`run_pipeline`, `run_pipeline_from_brief`, …) | — |

**Design principle:** the *generator* (Groq) and the *verifier* (Gemini) are
different models on purpose — independent checks, no shared blind spots.

---

## Models & cost — $0

| Job | Provider | Notes |
|-----|----------|-------|
| Script generation | **Groq** Llama 3.3 70B | free, 14.4k req/day |
| Vision (pick footage, QA, style) | **Gemini 2.5 Flash-Lite** | free ~1000/day; Groq Llama-4 vision as fallback |
| Voice | **Kokoro** (local ONNX) | free, no API; edge-tts fallback |
| Footage | **Pexels + Pixabay** | free, commercial use |
| Photo fallback | **Unsplash** | free |
| Publish | **YouTube Data API** | free quota |

> ⚠️ **Geo-restriction:** Groq and Gemini are **blocked from mainland China**
> (Gemini: "location not supported"; Groq: 403). Use a **VPN** for local testing,
> or run on an **overseas server** (Oracle/AWS) where they work directly. The
> pipeline still builds videos if vision fails (graceful degradation). For a fully
> China-native stack, swap the verifier to Qwen — see `data/AUTONOMOUS_GUIDE.md §0`.
>
> **Optional paid upgrade:** swap the verifier to Claude vision for higher quality
> (~$0.1–0.4/month at 1 video/day). One change in `agents/vision.py`.

---

## Setup

```bash
# 1. System: ffmpeg MUST include the drawtext filter (libfreetype)
#    conda-forge ffmpeg works; Homebrew ffmpeg 8.x may not.
ffmpeg -filters | grep drawtext        # must print a line

# 2. Python deps
pip install -r requirements.txt

# 3. Kokoro TTS model files (~350MB, one-time) → ~/.cache/kokoro/
#    (download script in data/AUTONOMOUS_GUIDE.md §3)

# 4. API keys → .env  (never commit this file)
GROQ_API_KEY=...        # console.groq.com
GEMINI_API_KEY=...      # aistudio.google.com/apikey  (new keys start with AQ.)
PEXELS_API_KEY=...      # pexels.com/api
PIXABAY_API_KEY=...     # pixabay.com/api/docs
UNSPLASH_ACCESS_KEY=... # unsplash.com/developers
# YouTube (only to publish, not for --dry-run):
YOUTUBE_CLIENT_ID=... ; YOUTUBE_CLIENT_SECRET=... ; YOUTUBE_REFRESH_TOKEN=...
#   one-time OAuth: python scripts/setup_youtube_oauth.py
```

Daily cron (server): `0 9 * * * cd /path && .venv/bin/python scripts/run.py >> logs/run.log 2>&1`

---

## How it learns / how to give feedback

Three files persist knowledge across runs (and across Claude sessions):

- **`data/director_guidelines.json`** — creative rules the Director obeys every run.
  Don't like how scripts read? Add a rule + bump `version`. No code change needed.
- **`data/insights.json`** — auto-distilled from YouTube performance.
- **`data/learning_log.md`** — human-readable audit trail of every rule change + QA finding.

Quality defense is layered: (1) you review the script, (2) Director won't invent
unfindable claims, (3) Gemini picks best-matching footage, (4) QA flags + offers
alternatives for any mismatch that slips through.

---

## Output layout

```
output/<id>/
├── brief.json          # the script (editable; --from-brief builds it)
├── metadata.json       # title/description/tags/scenes
├── audio.mp3           # voiceover
├── subtitles.srt       # captions
├── media/00.mp4 …      # one clip/photo per scene
├── youtube.mp4         # 1920×1080 final
├── reels.mp4           # 1080×1920 final
├── review.json         # (if QC found mismatches) what to fix + how
└── alternatives/scene_NN/alt_K.{mp4,jpg}   # candidate swaps for --fix
```

---

## For a fresh Claude reading this cold

Read in order: this README → `data/ROADMAP.md` (architecture, the "honest risks"
section, why each decision was made) → `data/AUTONOMOUS_GUIDE.md` (ops) →
`data/learning_log.md` (what's been tried). Entry point: `orchestrator.py::run_pipeline`.
Every `agents/*.py` has a module docstring explaining its role. Built in phases 1–6,
all complete (ROADMAP §4). Two human touchpoints: script review (`--review`) and
mismatch fix (`--fix`).
