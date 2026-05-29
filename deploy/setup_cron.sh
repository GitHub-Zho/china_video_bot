#!/usr/bin/env bash
# =============================================================================
# Install the daily cron job on Oracle Cloud Ubuntu
# =============================================================================
# Run after setup_server.sh has finished:
#   bash ~/china_video_bot/deploy/setup_cron.sh
#
# Schedule: 09:00 UTC daily  (adjust CRON_HOUR below if you prefer a different time)
#   09:00 UTC = 05:00 ET  = 17:00 CST  — good morning upload for US/EU audiences
# =============================================================================

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/china_video_bot}"
VENV_PYTHON="$INSTALL_DIR/.venv/bin/python"
LOG_FILE="$INSTALL_DIR/logs/cron.log"
CRON_HOUR=9       # UTC hour — change to your preference
CRON_MINUTE=0

# Verify the venv exists
if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found at $VENV_PYTHON"
    echo "       Run setup_server.sh first."
    exit 1
fi

mkdir -p "$INSTALL_DIR/logs"

# Build the cron line
CRON_CMD="$CRON_MINUTE $CRON_HOUR * * *  cd $INSTALL_DIR && $VENV_PYTHON main.py --now >> $LOG_FILE 2>&1"

# Add to crontab (idempotent — won't duplicate if already present)
EXISTING=$(crontab -l 2>/dev/null || true)
if echo "$EXISTING" | grep -qF "china_video_bot"; then
    echo "Cron job already installed — updating…"
    # Remove old china_video_bot lines then re-add
    NEW_CRONTAB=$(echo "$EXISTING" | grep -v "china_video_bot" ; echo "$CRON_CMD")
    echo "$NEW_CRONTAB" | crontab -
else
    echo "Installing new cron job…"
    (echo "$EXISTING"; echo "$CRON_CMD") | crontab -
fi

echo ""
echo "✅ Cron job installed:"
crontab -l | grep china_video_bot
echo ""
echo "Schedule: every day at ${CRON_HOUR}:$(printf '%02d' $CRON_MINUTE) UTC"
echo "Log file: $LOG_FILE"
echo ""
echo "To remove:  crontab -e  →  delete the china_video_bot line"
echo "To test now: cd $INSTALL_DIR && $VENV_PYTHON main.py --dry-run"
