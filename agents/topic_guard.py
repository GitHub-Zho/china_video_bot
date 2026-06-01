"""
Topic Guard — prevents the Director from repeating a topic too soon.

Reads published_videos.json (and recent output metadata) and returns the list
of recently-used topics so the Director can avoid them. Pure-function, no LLM.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from config.settings import PUBLISHED_FILE, OUTPUT_DIR


def recent_topics(days: int = 14) -> list[str]:
    """
    Return topics used in the last `days` days, from published_videos.json plus
    any local output/*/metadata.json (covers dry-runs that were never published).
    """
    topics: list[str] = []
    cutoff = date.today() - timedelta(days=days)

    # 1. Published videos
    pub = Path(PUBLISHED_FILE)
    if pub.exists():
        try:
            for v in json.loads(pub.read_text()):
                try:
                    d = date.fromisoformat(v.get("publish_date", "1970-01-01"))
                except Exception:
                    d = date.today()
                if d >= cutoff and v.get("topic"):
                    topics.append(v["topic"])
        except Exception:
            pass

    # 2. Local output metadata (dry-runs)
    out = Path(OUTPUT_DIR)
    if out.exists():
        for meta in out.glob("*/metadata.json"):
            try:
                # output dir names start with YYYYMMDD
                stamp = meta.parent.name[:8]
                d = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
                if d < cutoff:
                    continue
                data = json.loads(meta.read_text())
                if data.get("topic"):
                    topics.append(data["topic"])
            except Exception:
                continue

    # De-dup, preserve order
    return list(dict.fromkeys(topics))


def avoid_clause(days: int = 14) -> str:
    """
    Build a short instruction telling the Director which topics to avoid.
    Empty string if there's no recent history.
    """
    recent = recent_topics(days)
    if not recent:
        return ""
    return (
        "AVOID repeating these recently-covered topics (pick a different angle or "
        "region): " + "; ".join(recent[:10])
    )
