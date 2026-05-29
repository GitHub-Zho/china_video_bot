"""
main.py — Entry point.

Usage:
  python main.py              # Start daily scheduler (runs at 9 AM ET every day)
  python main.py --now        # Run pipeline immediately (full upload)
  python main.py --dry-run    # Run pipeline without uploading
  python main.py --analytics  # Only collect pending analytics
"""
import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pipeline.log", encoding="utf-8"),
    ]
)

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron       import CronTrigger
from config.settings import PUBLISH_HOUR, PUBLISH_TZ
from orchestrator import run_pipeline
from agents.analytics_agent import run_pending_analytics


def scheduled_job():
    try:
        run_pipeline()
    except RuntimeError as e:
        if "OPENAI_API_KEY" in str(e):
            logging.warning("⚠️  Skipping: OPENAI_API_KEY not set. Add it to .env and restart.")
        else:
            logging.error(f"Pipeline error: {e}", exc_info=True)
    except Exception as e:
        logging.error(f"Pipeline error: {e}", exc_info=True)


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--analytics" in args:
        n = run_pending_analytics()
        print(f"Collected analytics for {n} video(s).")
        sys.exit(0)

    if "--now" in args:
        audience = None
        if "--newcomer" in args:
            audience = "newcomer"
        elif "--explorer" in args:
            audience = "explorer"
        run_pipeline(audience_type=audience)
        sys.exit(0)

    if "--dry-run" in args:
        run_pipeline(dry_run=True)
        sys.exit(0)

    # Default: start daily scheduler
    scheduler = BlockingScheduler(timezone=PUBLISH_TZ)
    scheduler.add_job(
        scheduled_job,
        trigger=CronTrigger(hour=PUBLISH_HOUR, minute=0),
        id="daily_video",
        replace_existing=True,
        misfire_grace_time=3600,   # run even if missed by up to 1 hour
        coalesce=True,
    )

    logging.info(f"Scheduler started — daily video at {PUBLISH_HOUR}:00 {PUBLISH_TZ}")
    logging.info("Commands: python main.py --now | --dry-run | --analytics")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Scheduler stopped.")
