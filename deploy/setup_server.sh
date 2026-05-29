#!/usr/bin/env bash
# =============================================================================
# Oracle Cloud Free Tier — Ubuntu 22.04 ARM — First-time Server Setup
# =============================================================================
# Run once as the default ubuntu user after SSH-ing into the instance:
#   chmod +x setup_server.sh && ./setup_server.sh
#
# What this does:
#   1. System packages: Python 3.11, FFmpeg (with libass), git, pip
#   2. Clone / pull the repo
#   3. Create Python venv + install all requirements
#   4. Create .env from template (you fill in values afterwards)
#   5. Create output dirs
#
# After this script, run:
#   nano ~/china_video_bot/.env    # fill in GROQ_API_KEY + YouTube tokens
#   ./setup_cron.sh                # install the daily cron job
# =============================================================================

set -euo pipefail

REPO_URL="https://github.com/YOUR_GITHUB_USER/china_video_bot.git"
INSTALL_DIR="$HOME/china_video_bot"
PYTHON="python3.11"

# ── 1. System packages ───────────────────────────────────────────────────────
echo "[1/5] Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev \
    python3-pip \
    git \
    ffmpeg \
    libass9 libass-dev \
    fonts-liberation fonts-dejavu \
    curl wget

# Verify ffmpeg has subtitles / libass
echo "  ffmpeg version: $(ffmpeg -version 2>&1 | head -1)"
echo "  libass check:   $(ffmpeg -filters 2>&1 | grep -c subtitles) subtitles filter(s) found"

# ── 2. Clone or pull repo ────────────────────────────────────────────────────
echo "[2/5] Setting up repository…"
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Repo already cloned — pulling latest…"
    git -C "$INSTALL_DIR" pull --ff-only
else
    echo "  Cloning from $REPO_URL…"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ── 3. Python venv + dependencies ────────────────────────────────────────────
echo "[3/5] Setting up Python virtual environment…"
if [ ! -d ".venv" ]; then
    $PYTHON -m venv .venv
fi
source .venv/bin/activate

pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  Installed packages: $(pip list --format=columns | wc -l)"

# ── 4. .env file ─────────────────────────────────────────────────────────────
echo "[4/5] Configuring environment…"
if [ ! -f ".env" ]; then
    cat > .env << 'DOTENV'
# China Video Bot — Environment Variables
# DO NOT commit this file to git

# ── Script generation (free) ─────────────────────────────────────────────────
# Get free key at: https://console.groq.com  (no credit card)
GROQ_API_KEY=YOUR_GROQ_KEY_HERE

# ── Image sources (already configured) ───────────────────────────────────────
PEXELS_API_KEY=YOUR_PEXELS_KEY_HERE
UNSPLASH_ACCESS_KEY=YOUR_UNSPLASH_ACCESS_KEY_HERE
UNSPLASH_SECRET_KEY=YOUR_UNSPLASH_SECRET_KEY_HERE

# ── YouTube OAuth (run scripts/setup_youtube_oauth.py on your Mac first) ─────
YOUTUBE_CLIENT_ID=YOUR_CLIENT_ID
YOUTUBE_CLIENT_SECRET=YOUR_CLIENT_SECRET
YOUTUBE_REFRESH_TOKEN=YOUR_REFRESH_TOKEN
DOTENV
    echo "  Created .env — edit it now:  nano $INSTALL_DIR/.env"
else
    echo "  .env already exists — skipping (not overwritten)"
fi

# ── 5. Output directories ─────────────────────────────────────────────────────
echo "[5/5] Creating output directories…"
mkdir -p output data credentials logs

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Setup complete!                                             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Next steps:                                                 ║"
echo "║  1. Fill in .env:  nano $INSTALL_DIR/.env"
echo "║  2. Install cron:  bash $INSTALL_DIR/deploy/setup_cron.sh"
echo "║  3. Test run:      cd $INSTALL_DIR && source .venv/bin/activate"
echo "║                    python main.py --dry-run                  ║"
echo "╚══════════════════════════════════════════════════════════════╝"
