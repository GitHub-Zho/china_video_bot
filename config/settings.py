import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── FFmpeg binary ──────────────────────────────────────────────────────────────
# Prefer the ffmpeg that lives alongside Python (conda env), which is compiled
# with libfreetype / drawtext support.  Falls back to system ffmpeg if not found.
_conda_bin    = Path(sys.executable).parent
_conda_ffmpeg = str(_conda_bin / "ffmpeg")
_conda_ffprobe = str(_conda_bin / "ffprobe")
FFMPEG_BIN  = os.getenv("FFMPEG_BIN",  _conda_ffmpeg  if Path(_conda_ffmpeg).exists()  else "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", _conda_ffprobe if Path(_conda_ffprobe).exists() else "ffprobe")

# === API Keys ===
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
PEXELS_API_KEY      = os.getenv("PEXELS_API_KEY")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")

# === Claude model (haiku = cheapest, fast enough for scripts) ===
CLAUDE_MODEL = "claude-haiku-3-5"
CLAUDE_MAX_TOKENS = 900  # keep scripts tight

# === TTS ===
TTS_MODEL  = "tts-1"          # upgrade to tts-1-hd for higher quality
TTS_VOICE  = "nova"           # warm female English voice
TTS_SPEED  = 0.95             # slightly slower = clearer

# === Video ===
YOUTUBE_W, YOUTUBE_H = 1920, 1080
REELS_W,   REELS_H   = 1080, 1920
FPS               = 25
SLIDE_DURATION    = 4.0       # fallback per-clip duration when no scene timing
FADE_DURATION     = 0.5       # seconds crossfade (snappier)
IMAGES_PER_VIDEO  = 10        # max media items per video

# Phase 1: per-scene clip duration clamps (driven by TTS narration length)
MIN_CLIP_SECONDS  = 2.0       # never show a clip shorter than this
MAX_CLIP_SECONDS  = 8.0       # never show a clip longer than this

# Target durations — shorter = more Reels-native, better completion rate
TARGET_YOUTUBE_SECONDS = 32   # ~8 scenes × 4s
TARGET_REELS_SECONDS   = 20   # ~5 scenes × 4s

HOOK_CARD_SECONDS = 2.0       # freeze-frame hook card at video start

# === Publishing ===
PUBLISH_HOUR     = 9          # 9:00 AM daily
PUBLISH_TZ       = "America/New_York"
VIDEO_PRIVACY    = "public"   # "public" | "unlisted" | "private"
YOUTUBE_CATEGORY = "19"       # 19 = Travel & Events

# === Analytics ===
ANALYTICS_DELAY_DAYS = 3      # wait 3 days after publish before querying

# === Rate limiting (be polite to free-tier APIs) ===
IMAGE_DOWNLOAD_DELAY = 0.4    # seconds between image downloads
API_CALL_DELAY       = 0.3    # seconds between search API calls

# === Paths ===
DATA_DIR        = "data"
OUTPUT_DIR      = "output"
CREDENTIALS_DIR = "credentials"
SECRETS_FILE    = f"{CREDENTIALS_DIR}/client_secrets.json"
TOKEN_FILE      = f"{CREDENTIALS_DIR}/token.json"
HISTORY_FILE    = f"{DATA_DIR}/performance_history.json"
PUBLISHED_FILE  = f"{DATA_DIR}/published_videos.json"
