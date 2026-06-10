# 🇨🇳 China Video Bot

Give it a topic → it writes a script, sources matching footage (stock + AI-generated + real reference clips),
adds an AI voiceover and captions, assembles **YouTube (16:9) + Instagram Reels (9:16)** videos, quality-
checks them with a vision model, and publishes to YouTube and Instagram. Short-form China travel/culture
content, ~25–35 s, fully automated.

> **New here?** This README is the operating manual. For deeper detail:
> `docs/PIPELINE_FLOW.md` (full flow + known issues + optimization notes),
> `data/AUTONOMOUS_GUIDE.md` (server ops), `data/ROADMAP.md` (architecture + build history).

---

## Quick start

```bash
# 0. one-time setup (see "Setup" below), then:

# Recommended: generate the script first, review it, then build the video
python scripts/run.py --prompt "Beijing roast duck" --review
#   → prints the 5-scene script + saves output/<id>/brief.json, then STOPS
#   → read/edit brief.json if you want, then:
python scripts/run.py --from-brief output/<id>/brief.json --dry-run
#   → builds locally (no upload). Files: output/<id>/youtube.mp4 + reels.mp4

# Or one-shot (no review):
python scripts/run.py --prompt "Guilin Li River" --dry-run

# Fully automatic (Director picks a fresh topic, avoids recent ones, publishes):
python scripts/run.py
```

`--dry-run` builds locally and skips all uploads. Drop it to publish to YouTube + Instagram.

---

## How it works (the pipeline)

```
You give a topic (--prompt)
   │
[1] DIRECTOR  (Qwen-max)
      · writes 5 scenes: narration line + stock search query + AI image prompt
      · reads director_guidelines.json (v8) — creative rules, audience, format
      · Art Director merged in: gen_prompt included in same JSON output (saves 1 API call)
      · CRITIC (Qwen-max) scores the script ≥7/10; auto-retries up to 3× if weak
      │
      └─► --review STOPS HERE so you can read/edit brief.json   ◄── YOU (touchpoint 1)
   │
[2] VOICE  (Kokoro, local TTS)
      · per-scene audio, 0.4 s lead-in silence (subtitle appears before voice)
      · 0.6 s tail gap between scenes; MIN_SCENE_SECONDS = 3.2 s
      · outputs: audio.mp3 + subtitles.srt + scene_durations[]
   │
[3] MEDIA — three sources compete per scene
      │
      ├─ (optional) REFERENCE AGENT  (yt-dlp + FFmpeg)
      │     download a 12 s segment from a B站 / YouTube URL
      │     extract 12 frames → strip watermarks (drawbox) → cache to output/ref_cache/
      │     same URL re-used across scenes hits cache immediately (MD5 key)
      │
      └─ compete_and_apply  (per scene)
            ├─ Stock:      Pexels + Pixabay keyword search → up to 5 preview thumbnails
            ├─ AI image:   Wanxiang (text-to-image) — async submit → poll → thumbnail
            ├─ Reference:  real extracted frames (already deduplicated across scenes)
            └─ JUDGE:      Qwen-VL scores all candidates 0–10
                  ├─ winner ≥5 → write media/{i:02d}.jpg or .mp4
                  └─ no winner → fallback: first Pexels video (unscored)
   │
[3b] PRE-CHECK  (Qwen-VL)
      · batch-score every scene's chosen media before assembly
      · score < 6 → immediately re-run compete_and_apply for that scene
      · catches bad picks before spending 15–40 s on FFmpeg
   │
[4] VIDEO ASSEMBLY  (FFmpeg)
      · per-scene: photo → Ken Burns zoompan  |  clip → scale-crop
      · smart crop for Reels: landscape source → center-crop portrait strip → zoompan
      · concat all scenes → overlay audio → burn subtitles (Anton font, drawtext)
      · topic badge top-left, 2 s hook card at front, 0.3 s scene crossfades
      · outputs: youtube.mp4 (1920×1080)  +  reels.mp4 (1080×1920)
   │
[5] QA  (Qwen-VL)
      · sample 1 frame / 1.5 s (up to 20 frames), label each with its narration line
      · detect: content mismatch / subtitle problems / visual artifacts
      · content mismatch found →
            find_replacement_clip (stock + AI image + AI video all compete)
            if replaced → _reassemble_from_media (re-renders video, no new TTS)
            if not replaced → download_scene_alternatives for manual --fix   ◄── YOU (touchpoint 2)
   │
[6] PUBLISH
      · YouTube  (YouTube Data API v3)
      · Instagram Reels  (Meta Graph API v20.0 + resumable upload)
      · both skipped with --dry-run
      · 3 days later: analytics → insights.json → feeds back into the Director
```

---

## The two places you step in

**1. Script review (before any media is fetched)**
```bash
python scripts/run.py --prompt "..." --review     # see script, stop
# edit output/<id>/brief.json if needed, then:
python scripts/run.py --from-brief output/<id>/brief.json --dry-run
```
Catches bad/hallucinated lines before spending time on voice + media + video.

**2. Fix a content mismatch (after build)**
If QA finds footage that doesn't match the narration it writes `output/<id>/review.json`
and downloads 3 alternatives to `output/<id>/alternatives/scene_NN/`.
Look at the `alt_*.jpg` previews, pick one:
```bash
python scripts/run.py --fix <id> --scene 6 --pick 2   # use alt_2 for scene 6, rebuild
```

---

## All commands

```bash
python scripts/run.py                                   # auto daily mode (publishes)
python scripts/run.py --dry-run                         # auto, build only
python scripts/run.py --prompt "TOPIC" --review         # script-first (recommended)
python scripts/run.py --from-brief PATH --dry-run       # build an approved/edited script
python scripts/run.py --prompt "TOPIC" --dry-run        # one-shot, no review
python scripts/run.py --audience newcomer               # explorer | newcomer
python scripts/run.py --seconds 24                      # target length override
python scripts/run.py --from-folder ~/photos            # use YOUR images/clips, not stock
python scripts/run.py --learn-style ref.mp4 NAME        # learn a reference video's style
python scripts/run.py --prompt "..." --style NAME       # imitate a learned style
python scripts/run.py --fix <id> --scene N --pick K     # swap a flagged scene's clip
python scripts/run.py --reference-url URL --timestamps "0:10-0:22,1:05-1:17"
#  ↑ attach a B站/YouTube clip as real-footage reference for this run
```

---

## Agents (`agents/`)

| File | Role | Model |
|------|------|-------|
| `director_agent.py` | Writes 5-scene script (narration + queries + AI prompts); self-critique loop | Qwen-max |
| `critic_agent.py` | Scores script ≥7/10; triggers Director retry | Qwen-max |
| `art_director.py` | (merged into Director as of v8) rich AI image prompts | — |
| `topic_guard.py` | Recent-topics list so Director avoids repeats | — |
| `voice_agent.py` | TTS → MP3 + per-scene SRT timing | Kokoro (local) |
| `reference_agent.py` | Download B站/YouTube clips, extract frames, strip watermarks, cache | yt-dlp + FFmpeg |
| `media_agent.py` | Stock + AI-image + reference compete per scene; Qwen-VL judge; pre-check | Qwen-VL |
| `image_agent.py` | Wanxiang text-to-image async submit + poll | Wanxiang API |
| `video_agent.py` | FFmpeg assembly; hook card; Ken Burns; portrait smart-crop; subtitle re-burn | — |
| `qa_agent.py` | Frame sampling → mismatch + subtitle detection → auto-fix or alternatives | Qwen-VL |
| `vision.py` | Shared verifier (Qwen-VL primary, Gemini fallback) | Qwen-VL / Gemini |
| `style_analyst_agent.py` | Learn + imitate a reference video's style | Gemini + ffprobe |
| `media_analyst_agent.py` | Turn YOUR photos into a matched script (`--from-folder`) | Qwen-VL |
| `analytics_agent.py` | YouTube metrics → insights.json for the Director | — |
| `publisher_agent.py` | Upload to YouTube | YouTube Data API |
| `instagram_agent.py` | Upload to Instagram Reels; auto-refresh 60-day token | Meta Graph API |
| `account_manager.py` | Multi-account credential store; lazy token refresh | — |
| `orchestrator.py` | Wires every step together | — |

**Design principle:** the *generator* (Qwen-max text) and the *verifier* (Qwen-VL vision) are separate
model calls with independent prompts — the critic can catch what the generator missed.

---

## Models & APIs

| Job | Provider | Notes |
|-----|----------|-------|
| Script generation + Critic | **Qwen-max** (DashScope) | China-native, no VPN needed |
| Vision: judge footage, QA, style | **Qwen-VL** (qwen-vl-max) | China-native; Gemini 2.5 Flash-Lite as fallback |
| AI image generation | **Wanxiang** (通义万象 t2i) | China-native; async batch submit |
| Voice | **Kokoro** (local ONNX) | free, offline; edge-tts as fallback |
| Stock footage | **Pexels + Pixabay** | free, commercial use |
| Photo fallback | **Unsplash** | free |
| Reference clips | **yt-dlp** (B站 / YouTube) | local download, cached |
| Publish: YouTube | **YouTube Data API v3** | free quota |
| Publish: Instagram | **Meta Graph API v20.0** | free; 60-day token, auto-renews |

> **Geo-restriction note:** Groq and Gemini are **blocked from mainland China**.
> This stack uses Qwen (DashScope) throughout — runs directly on Chinese networks
> with no VPN. Gemini is only used as a fallback when `GEMINI_API_KEY` is set and
> DashScope is unreachable. For overseas servers (Oracle/AWS) both work fine.

---

## Setup

### 1. System

```bash
# FFmpeg must include the drawtext filter (libfreetype)
# conda-forge ffmpeg works; Homebrew 8.x may not
ffmpeg -filters | grep drawtext        # must print a line
```

### 2. Python

```bash
pip install -r requirements.txt
```

### 3. Kokoro TTS model files (~350 MB, one-time)

```bash
# Download script in data/AUTONOMOUS_GUIDE.md §3
# Cached to ~/.cache/kokoro/
```

### 4. API keys → `.env`  _(never commit this file)_

```env
# Required
DASHSCOPE_API_KEY=...     # platform.aliyun.com/aigc  (Qwen text + VL + Wanxiang)

# Stock footage (free)
PEXELS_API_KEY=...        # pexels.com/api
PIXABAY_API_KEY=...       # pixabay.com/api/docs
UNSPLASH_ACCESS_KEY=...   # unsplash.com/developers

# Optional — fallback vision when DashScope unreachable
GEMINI_API_KEY=...        # aistudio.google.com/apikey

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
# Download client_secrets.json from Google Cloud Console first
# (APIs & Services → Credentials → OAuth 2.0 → Desktop app → Download JSON)
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

Multiple YouTube channels and Instagram accounts are supported out of the box.

```bash
# Add accounts (run once per account, on your local machine)
python scripts/setup_youtube_oauth.py --account main
python scripts/setup_youtube_oauth.py --account travel
python scripts/setup_instagram.py --account food
python scripts/setup_instagram.py --account travel

# Check all account health (tokens valid? Instagram expiring soon?)
python scripts/maintain_accounts.py

# View stored accounts
python -c "from agents.account_manager import list_accounts; list_accounts()"
```

Credentials are stored in `credentials/accounts/yt_{name}.json` and `ig_{name}.json`.
Copy the whole `credentials/accounts/` folder to a server — no re-auth needed.

**Token lifetime:**

| Platform | Token type | Expires? | Auto-renewed? |
|----------|-----------|----------|---------------|
| YouTube | Refresh token | No (permanent unless revoked) | N/A |
| Instagram | Long-lived token (60 days) | Yes | ✅ by `maintain_accounts.py` weekly cron |

---

## How it learns / how to give feedback

Three files persist knowledge across runs:

- **`data/director_guidelines.json`** — creative rules the Director obeys every run.
  Don't like how scripts read? Add a rule + bump `version`. No code change needed.
- **`data/insights.json`** — auto-distilled from YouTube analytics after each video.
- **`data/learning_log.md`** — human-readable audit trail of every rule change and QA finding.

Quality is layered: (1) you optionally review the script, (2) Critic rejects weak scripts before
media is fetched, (3) Qwen-VL picks the best-matching footage from all candidates,
(4) pre-check catches bad picks before FFmpeg runs, (5) QA flags mismatches + offers alternatives.

---

## Output layout

```
output/<id>/
├── brief.json          # the script (editable; --from-brief uses this)
├── metadata.json       # title / description / tags / scenes
├── audio.mp3           # voiceover
├── subtitles.srt       # captions
├── media/00.mp4 …      # one clip or photo per scene (stock / AI / reference)
├── youtube.mp4         # 1920×1080 final
├── reels.mp4           # 1080×1920 final
├── review.json         # (if QA found mismatches) what to fix + how
└── alternatives/
    └── scene_NN/
        └── alt_K.{mp4,jpg}    # candidate swaps for --fix

output/ref_cache/              # cached reference frame extracts (keyed by URL + timestamp)
```

---

## For a fresh Claude reading this cold

Read in order: this README → `docs/PIPELINE_FLOW.md` (full step-by-step flow, known issues, optimization
notes) → `data/ROADMAP.md` (architecture decisions, honest risks) → `data/AUTONOMOUS_GUIDE.md` (server
ops, Qwen/Kokoro setup). Entry point: `orchestrator.py::run_pipeline`. Every `agents/*.py` has a module
docstring explaining its role. Two human touchpoints: script review (`--review`) and mismatch fix (`--fix`).
