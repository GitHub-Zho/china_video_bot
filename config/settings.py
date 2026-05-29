import os
from dotenv import load_dotenv

load_dotenv()

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
SLIDE_DURATION    = 5.0       # seconds each clip/photo is shown
FADE_DURATION     = 1.0       # seconds crossfade between clips
IMAGES_PER_VIDEO  = 12        # max media items per video

# Target durations — Director Agent plans scene count to match these
# TODO: confirm preferred lengths with user
TARGET_YOUTUBE_SECONDS = 60   # ~12 scenes × 5s
TARGET_REELS_SECONDS   = 45   # ~9  scenes × 5s

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
