# 🇨🇳 China Video Bot

An automated multi-agent pipeline that generates and publishes short-form China travel & culture videos to YouTube and Instagram Reels — daily, with zero manual intervention.

## What it does

Every day at 9:00 AM UTC the bot:

1. **Generates a script** via Groq (Llama 3.3 70B, free tier) — 60-90 second voiceover copy targeting English-speaking audiences curious about China
2. **Downloads beautiful images** from Pexels + Unsplash (free APIs) — China landscapes, cities, food, culture
3. **Creates a voiceover + subtitles** via Microsoft Edge TTS (free) — natural English narration with precisely-timed SRT subtitles
4. **Assembles the video** via MoviePy + FFmpeg — 1920×1080 YouTube version and 1080×1920 Instagram Reels version, with burned-in subtitles
5. **Uploads to YouTube** via YouTube Data API v3 with automatic title, description, and tags
6. **Collects analytics** 3 days after publish and feeds performance data back to improve future scripts

## Two audience types

| Audience | Description |
|---|---|
| **Explorer** | People who've seen China content and want hidden gems, lesser-known destinations, surprising facts |
| **Newcomer** | People curious about China but don't know where to start — first-timer perspective |

## Architecture

```
Orchestrator
├── Script Agent    — Groq API (Llama 3.3 70B) + template fallback
├── Image Agent     — Pexels API + Unsplash API (alternating)
├── Voice Agent     — edge-tts (Microsoft, free) → MP3 + SRT
├── Video Agent     — MoviePy slideshow + FFmpeg subtitle burn
├── Publisher Agent — YouTube Data API v3 (OAuth2, refresh token)
└── Analytics Agent — YouTube Analytics API v2 → feedback loop
```

## Tech stack — 100% free APIs

| Component | Tool | Cost |
|---|---|---|
| LLM / Script | [Groq](https://console.groq.com) (Llama 3.3 70B) | Free |
| Images | Pexels API + Unsplash API | Free |
| Text-to-speech | Microsoft Edge TTS (`edge-tts`) | Free |
| Video assembly | MoviePy v2 + FFmpeg | Free / open source |
| Publishing | YouTube Data API v3 | Free quota |
| Hosting | Oracle Cloud Free Tier (ARM VM) | Always free |

## Quick start

### 1. Clone & install
```bash
git clone https://github.com/GitHub-Zho/china_video_bot.git
cd china_video_bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure `.env`
```bash
cp .env.example .env   # then fill in your keys
```

Required keys:
- `GROQ_API_KEY` — free at [console.groq.com](https://console.groq.com)
- `PEXELS_API_KEY` — free at [pexels.com/api](https://www.pexels.com/api/)
- `UNSPLASH_ACCESS_KEY` — free at [unsplash.com/developers](https://unsplash.com/developers)
- `YOUTUBE_CLIENT_ID / SECRET / REFRESH_TOKEN` — see YouTube setup below

### 3. YouTube OAuth (one-time)
```bash
# Download OAuth client secrets from Google Cloud Console first:
# → APIs & Services → Credentials → OAuth 2.0 Client ID (Desktop) → Download JSON
# → Save as credentials/client_secrets.json

python scripts/setup_youtube_oauth.py
```

### 4. Run
```bash
# One video right now (no upload)
python main.py --dry-run

# One video right now + upload
python main.py --now

# Start the daily scheduler (9:00 AM ET)
python main.py
```

## Deploy to Oracle Cloud (recommended)

```bash
# On your Oracle Cloud Ubuntu ARM instance:
bash deploy/setup_server.sh   # installs all dependencies
nano .env                      # fill in your keys
bash deploy/setup_cron.sh      # sets up daily cron at 09:00 UTC
```

## Project structure

```
china_video_bot/
├── agents/
│   ├── script_agent.py      # LLM script generation + template fallback
│   ├── image_agent.py       # Pexels + Unsplash image downloader
│   ├── voice_agent.py       # edge-tts TTS + accurate SRT timing
│   ├── video_agent.py       # MoviePy slideshow + FFmpeg subtitle burn
│   ├── publisher_agent.py   # YouTube upload (cloud + local OAuth modes)
│   ├── analytics_agent.py   # YouTube Analytics collection + feedback
│   └── subtitle_agent.py    # Naive SRT fallback utility
├── config/
│   ├── settings.py          # All configuration constants
│   └── prompts.py           # LLM system prompts + feedback template
├── deploy/
│   ├── setup_server.sh      # Oracle Cloud first-time setup
│   └── setup_cron.sh        # Daily cron installation
├── scripts/
│   └── setup_youtube_oauth.py  # One-time YouTube OAuth flow
├── data/                    # Analytics + publish history (gitignored)
├── output/                  # Generated videos (gitignored)
├── orchestrator.py          # Full pipeline runner
└── main.py                  # CLI entry point + APScheduler
```

## CLI options

```
python main.py --now          # Run pipeline immediately
python main.py --dry-run      # Run pipeline, skip YouTube upload
python main.py --newcomer     # Force newcomer audience type
python main.py --explorer     # Force explorer audience type
python main.py --analytics    # Run analytics collection only
```
