# China Video Bot

Give it a topic **or** a reference video → it writes a grounded script, sources matching footage,
adds an AI voiceover + subtitles, assembles **YouTube (16:9) + Instagram Reels (9:16)** videos,
quality-checks them with a vision model, and publishes to YouTube and Instagram.
Short-form China travel/culture content, ~25–60 s, fully automated.

---

## Local browser launcher

Use the local UI when you want to generate videos without typing CLI commands or opening Codex.
It supports both topic-driven and video-grounded generation. The UI always uses `--dry-run` and
cannot publish to YouTube or Instagram.

First-time setup:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Then double-click `scripts/start_ui.command` in Finder. It starts a local server and opens:

```text
http://127.0.0.1:7860/
```

Terminal fallback:

```bash
.venv/bin/python -m launcher.app
```

Mode 1 defaults to disabling automatic Scout until its outstanding relevance filters are fixed and
verified. You can re-enable Scout from the page when you explicitly want to test it.

---

## Two modes at a glance

| | Mode 1 — Topic-driven | Mode 2 — Video-grounded |
|---|---|---|
| **Input** | Free-text topic | Reference video URL (Bilibili / YouTube) |
| **Script** | Director freely invents from topic + guidelines | Director writes from real content it saw |
| **Footage** | Stock (Pexels/Pixabay) + AI images | Real clips cut from the source video; stock fallback for unmatched scenes |
| **Best for** | General travel/culture topics | Process/how-to content ("how X is made") |
| **Flag** | `--prompt "..."` | `--from-video URL --topic "..."` |

```bash
# Mode 1 — topic driven
python scripts/run.py --prompt "Beijing roast duck" --dry-run

# Mode 2 — video grounded
python scripts/run.py --from-video https://www.bilibili.com/video/BV1EY4y1B7Mc/ \
                      --topic "Beijing Roast Duck preparation" --dry-run
```

---

## Quick start

```bash
# Recommended: review the script before building
python scripts/run.py --prompt "Guilin Li River" --review
#   → prints 5-scene script + saves output/<id>/brief.json, then STOPS
#   → edit brief.json if you want, then:
python scripts/run.py --from-brief output/<id>/brief.json --dry-run
#   → builds locally (no upload)

# One-shot, no review:
python scripts/run.py --prompt "Guilin Li River" --dry-run

# Fully automatic daily mode (Director picks topic, publishes):
python scripts/run.py
```

`--dry-run` builds locally and skips all uploads. Drop it to publish.

---

## Mode 1 — topic-driven pipeline

```
--prompt "Beijing roast duck"
   │
[1] DIRECTOR  (Qwen-max)
      · writes 5 scenes: narration + stock query + AI image prompt
      · reads director_guidelines.json — creative rules, audience, format
      · CRITIC scores ≥7/10; auto-retries up to 3× if weak
      └─► --review STOPS HERE  ◄── YOU (touchpoint 1)
   │
[2] VOICE  (Kokoro local TTS)
      · per-scene audio, 0.4s lead-in silence, 0.6s tail gap
      · outputs: audio.mp3 + subtitles.srt + scene_durations[]
   │
[3] MEDIA — three sources compete per scene
      ├─ Stock:      Pexels + Pixabay keyword search
      ├─ AI image:   Wanxiang text-to-image (async)
      ├─ Reference:  real extracted frames from --reference-url (optional)
      └─ JUDGE:      Qwen-VL scores all candidates 0–10 → picks winner
   │
[3b] PRE-CHECK  (Qwen-VL)
      · score < 6 → re-run compete for that scene before FFmpeg
   │
[4] VIDEO ASSEMBLY  (FFmpeg)  — see details below
   │
[5] QA  (Qwen-VL) + optional --fix  ◄── YOU (touchpoint 2)
   │
[6] PUBLISH  (YouTube + Instagram Reels)
```

---

## Mode 2 — video-grounded (documentary) pipeline

```
--from-video URL --topic "Beijing Roast Duck preparation"
   │
[1] UNDERSTAND PASS  (sparse: 1 frame / 4s)
      · downloads video (~480p, cached by URL)
      · Qwen-VL reads every frame in batches of 5
      · builds VideoUnderstanding: summary + ordered steps with timestamps
      · e.g. "[0:03] Inflating duck — pump separates skin from fat"
   │
[2] DIRECTOR  (Qwen-max)
      · same as Mode 1, but prompt includes the real step list
      · narration lines refer to actual moments ("first the skin is inflated")
      └─► --review STOPS HERE  ◄── YOU (touchpoint 1)
   │
[3] VOICE  (Kokoro)  — same as Mode 1; produces exact scene_durations
   │
[4] CLIP PASS  (dense: 1 frame / 2s)
      · builds full text timeline of source video (cached to JSON)
      · one text-only Qwen call matches each narration to best timestamp
      · ffmpeg extracts real mp4 clips from source at matched times
      · scenes not matched → stock fallback (same as Mode 1)
   │
[5] VIDEO ASSEMBLY  (FFmpeg)  — same as Mode 1
   │
[6] QA + PUBLISH  — same as Mode 1
```

**Why Mode 2 beats Mode 1 for how-to content:** the narration is grounded in real footage,
the clips are the real moment (not generic stock), and the style feels like documentary editing.

---

## Video assembly details (both modes)

```
Per scene:
  photo  → Ken Burns zoompan (slow pan/zoom creates motion)
  clip   → scale-crop to target resolution
              Smart clip selection: Qwen-VL analyzes up to 4 candidate windows
              and picks the segment most relevant to the narration (not just t=0)

Portrait / Reels (9:16):
  Smart crop: Qwen-VL asks "where is the main subject? (0–10)"
  → horizontal crop offset = (iw - w) × fraction
  → subject stays in frame even when they're not centered

Concat → overlay audio → burn subtitles (Anton font, drawtext)
Topic badge top-left, 2s hook card at front, 0.3s crossfades
Outputs: youtube.mp4 (1920×1080) + reels.mp4 (1080×1920)
```

---

## The two places you step in

**1. Script review**
```bash
python scripts/run.py --prompt "..." --review
# → edit output/<id>/brief.json if needed, then:
python scripts/run.py --from-brief output/<id>/brief.json --dry-run
```

**2. Fix a content mismatch**
If QA flags footage that doesn't match narration, it writes `review.json`
and downloads 3 alternatives to `output/<id>/alternatives/scene_NN/`.
```bash
python scripts/run.py --fix <id> --scene 6 --pick 2   # use alt_2 for scene 6
```

---

## All commands

```bash
# ── Mode 1: topic-driven ──────────────────────────────────────────────────────
python scripts/run.py                                    # auto daily (Director picks topic, publishes)
python scripts/run.py --dry-run                          # auto, build only
python scripts/run.py --prompt "TOPIC" --review          # script-first (recommended)
python scripts/run.py --from-brief PATH --dry-run        # build an approved/edited script
python scripts/run.py --prompt "TOPIC" --dry-run         # one-shot, no review

# ── Mode 2: video-grounded ────────────────────────────────────────────────────
python scripts/run.py --from-video URL --topic "TOPIC" --dry-run
python scripts/run.py --from-video URL --topic "TOPIC" --review     # script-first
python scripts/run.py --from-video URL --topic "TOPIC" --sample-interval 6.0  # faster analysis

# ── Mode 1 options ────────────────────────────────────────────────────────────
python scripts/run.py --audience newcomer               # explorer | newcomer
python scripts/run.py --seconds 24                      # target length override
python scripts/run.py --type info                       # info | growth | both (default)
python scripts/run.py --from-folder ~/photos            # use YOUR images/clips
python scripts/run.py --learn-style ref.mp4 NAME        # learn a reference video's style
python scripts/run.py --prompt "..." --style NAME       # imitate a learned style
python scripts/run.py --fix <id> --scene N --pick K     # swap a flagged scene's clip

# ── Mode 1: attach real reference footage from a URL ─────────────────────────
python scripts/run.py --prompt "TOPIC" \
    --reference-url https://www.bilibili.com/video/BV... \
    --time-range "7:40-8:10"               # preferred: scan a range, auto-sample
python scripts/run.py --prompt "TOPIC" \
    --reference-url URL \
    --timestamps "7:48,8:01,8:06"          # alt: specific timestamps
```

---

## Agents

| File | Role | Model |
|------|------|-------|
| `director_agent.py` | 5-scene script (narration + queries + AI prompts); self-critique loop | Qwen-max |
| `critic_agent.py` | Scores script ≥7/10; triggers Director retry | Qwen-max |
| `topic_guard.py` | Recent-topics list so Director avoids repeats | — |
| `voice_agent.py` | TTS → MP3 + per-scene SRT timing | Kokoro (local) |
| `reference_agent.py` | Download Bilibili/YouTube clips, extract frames, strip watermarks, cache | yt-dlp + FFmpeg |
| `media_agent.py` | Stock + AI-image + reference compete per scene; Qwen-VL judge; smart segment pick | Qwen-VL |
| `image_agent.py` | Wanxiang text-to-image async submit + poll | Wanxiang API |
| `video_agent.py` | FFmpeg assembly; hook card; Ken Burns; smart portrait crop; subtitle burn | FFmpeg |
| `video_analyst_agent.py` | **Mode 2 only** — two-pass video understanding + documentary clip extraction | Qwen-VL |
| `qa_agent.py` | Frame sampling → mismatch + subtitle detection → auto-fix or alternatives | Qwen-VL |
| `vision.py` | Shared vision verifier (Qwen-VL primary, Gemini fallback) | Qwen-VL / Gemini |
| `style_analyst_agent.py` | Learn + imitate a reference video's style | Gemini + ffprobe |
| `media_analyst_agent.py` | Turn YOUR photos into a matched script (`--from-folder`) | Qwen-VL |
| `analytics_agent.py` | YouTube metrics → insights.json for the Director | — |
| `publisher_agent.py` | Upload to YouTube | YouTube Data API |
| `instagram_agent.py` | Upload to Instagram Reels; auto-refresh 60-day token | Meta Graph API |
| `account_manager.py` | Multi-account credential store; lazy token refresh | — |
| `orchestrator.py` | Wires every step together | — |

**Design principle:** generator (Qwen-max text) and verifier (Qwen-VL vision) are separate model
calls with independent prompts — the critic can catch what the generator missed.

---

## Models & APIs

| Job | Provider | Notes |
|-----|----------|-------|
| Script generation + Critic | **Qwen-max** (DashScope) | China-native, no VPN needed |
| Vision: judge footage, QA, video analysis | **Qwen-VL** (qwen-vl-max) | China-native; Gemini 2.5 Flash-Lite as fallback |
| AI image generation | **Wanxiang** (通义万象 t2i) | China-native; async batch submit |
| Voice | **Kokoro** (local ONNX) | free, offline; edge-tts as fallback |
| Stock footage | **Pexels + Pixabay** | free, commercial use |
| Photo fallback | **Unsplash** | free |
| Reference clips | **yt-dlp** (Bilibili / YouTube) | local download, cached |
| Publish: YouTube | **YouTube Data API v3** | free quota |
| Publish: Instagram | **Meta Graph API v20.0** | free; 60-day token, auto-renews |

> **Geo note:** Groq and Gemini are blocked from mainland China. This stack uses Qwen (DashScope)
> throughout — runs directly on Chinese networks with no VPN. Gemini is only a fallback when
> `GEMINI_API_KEY` is set and DashScope is unreachable.

---

## Setup

### 1. System

```bash
ffmpeg -filters | grep drawtext   # must print a line (libfreetype required)
```
conda-forge FFmpeg works. Homebrew 8.x may not include drawtext.

### 2. Python

```bash
pip install -r requirements.txt
```

### 3. Kokoro TTS model files (~350 MB, one-time)

See `data/AUTONOMOUS_GUIDE.md §3`. Cached to `~/.cache/kokoro/`.

### 4. API keys → `.env`  _(never commit this file)_

```env
# Required
DASHSCOPE_API_KEY=...       # platform.aliyun.com/aigc  (Qwen text + VL + Wanxiang)

# Stock footage (free)
PEXELS_API_KEY=...          # pexels.com/api
PIXABAY_API_KEY=...         # pixabay.com/api/docs
UNSPLASH_ACCESS_KEY=...     # unsplash.com/developers

# Optional — fallback vision when DashScope is unreachable
GEMINI_API_KEY=...          # aistudio.google.com/apikey

# YouTube publishing (skip for --dry-run)
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
YOUTUBE_REFRESH_TOKEN=...

# Instagram publishing (skip for --dry-run)
IG_USER_ID=...
IG_ACCESS_TOKEN=...
```

### 5. YouTube OAuth (one-time per channel)

```bash
# Download client_secrets.json from Google Cloud Console first:
# APIs & Services → Credentials → OAuth 2.0 → Desktop app → Download JSON
# Move it to: credentials/client_secrets.json

python scripts/setup_youtube_oauth.py                    # default account
python scripts/setup_youtube_oauth.py --account travel   # named account
```

### 6. Instagram OAuth (one-time per account)

```bash
# Create a Meta Developer App with Instagram Graph API + instagram_content_publish
# Set redirect URI: http://localhost:8765/callback

python scripts/setup_instagram.py                        # default account
python scripts/setup_instagram.py --account food         # named account
```

### 7. Daily cron (server)

```bash
# Generate + publish every day at 9 AM
0 9 * * *  cd /path/to/china_video_bot && .venv/bin/python scripts/run.py >> logs/run.log 2>&1

# Account health check every Sunday at 3 AM (keeps Instagram tokens alive)
0 3 * * 0  cd /path/to/china_video_bot && .venv/bin/python scripts/maintain_accounts.py >> logs/account_health.log 2>&1
```

---

## Multi-account support

```bash
# Add accounts (run once per account, on your local machine)
python scripts/setup_youtube_oauth.py --account main
python scripts/setup_youtube_oauth.py --account travel
python scripts/setup_instagram.py --account food

# Check all account health (tokens valid? Instagram expiring soon?)
python scripts/maintain_accounts.py

# View stored accounts
python -c "from agents.account_manager import list_accounts; list_accounts()"
```

Credentials live in `credentials/accounts/yt_{name}.json` and `ig_{name}.json`.
Copy the folder to a server — no re-auth needed.

| Platform | Token type | Expires? | Auto-renewed? |
|----------|-----------|----------|---------------|
| YouTube | Refresh token | No | N/A |
| Instagram | Long-lived token (60 days) | Yes | ✅ by weekly cron |

---

## How it learns

- **`data/director_guidelines.json`** — creative rules the Director obeys every run. Edit and bump `version` to change script style without touching code.
- **`data/insights.json`** — auto-distilled from YouTube analytics after each video.
- **`data/learning_log.md`** — human-readable audit trail of every rule change and QA finding.

---

## Output layout

```
output/<id>/
├── brief.json               # the script (editable; --from-brief uses this)
├── video_understanding.json # Mode 2 only: structured knowledge from source video
├── metadata.json            # title / description / tags / scenes
├── audio.mp3                # voiceover
├── subtitles.srt            # captions
├── media/00.mp4 …           # one clip or photo per scene
├── youtube.mp4              # 1920×1080 final
├── reels.mp4                # 1080×1920 final
├── review.json              # (if QA found mismatches)
└── alternatives/
    └── scene_NN/
        └── alt_K.{mp4,jpg}  # candidate swaps for --fix

output/ref_cache/            # cached reference frame extracts (keyed by URL)
data/video_cache/            # Mode 2: cached timeline JSON (skips re-analysis on re-run)
```

---

## For a fresh Claude reading this cold

Entry point: `orchestrator.py::run_pipeline` (Mode 1) and `orchestrator.py::run_pipeline_from_video` (Mode 2).
Mode 2 core: `agents/video_analyst_agent.py` — `analyze_video()` (Pass 1) and `extract_clips_for_brief()` (Pass 2).
Every `agents/*.py` has a module docstring. Two human touchpoints: `--review` (script) and `--fix` (footage swap).
