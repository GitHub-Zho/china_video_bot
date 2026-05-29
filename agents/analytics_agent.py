"""Analytics Agent — queries YouTube Analytics 3+ days after publish, stores feedback data."""
import json
from datetime import date, timedelta
from pathlib import Path

from googleapiclient.discovery import build

from config.settings import (
    HISTORY_FILE, PUBLISHED_FILE, ANALYTICS_DELAY_DAYS
)


def _get_analytics_service():
    from agents.publisher_agent import _get_credentials
    creds = _get_credentials()
    return build("youtubeAnalytics", "v2", credentials=creds)


def collect_analytics(video_id: str, topic: str, audience_type: str) -> dict | None:
    """
    Query YouTube Analytics for a single video.
    Returns metrics dict or None if data not ready.
    NOTE: Data has 48-72h latency — only query videos >= 3 days old.
    """
    service = _get_analytics_service()
    today      = date.today()
    start_date = str(today - timedelta(days=30))
    end_date   = str(today - timedelta(days=1))   # never query today (incomplete)

    try:
        response = (
            service.reports()
            .query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                # videoThumbnailImpressionsClickRate added Jan 2026
                metrics=(
                    "views,"
                    "estimatedMinutesWatched,"
                    "averageViewDuration,"
                    "averageViewPercentage,"
                    "impressions,"
                    "videoThumbnailImpressionsClickRate"
                ),
                dimensions="video",
                filters=f"video=={video_id}",
                maxResults=1,
            )
            .execute()
        )
    except Exception as e:
        print(f"  [Analytics] Query failed for {video_id}: {e}")
        return None

    rows = response.get("rows", [])
    if not rows:
        print(f"  [Analytics] No data yet for {video_id} (normal if < 3 days old)")
        return None

    headers = [c["name"] for c in response["columnHeaders"]]
    row_map  = dict(zip(headers, rows[0]))

    record = {
        "video_id":          video_id,
        "topic":             topic,
        "audience_type":     audience_type,
        "views":             int(row_map.get("views", 0)),
        "minutes_watched":   float(row_map.get("estimatedMinutesWatched", 0)),
        "avg_view_duration": float(row_map.get("averageViewDuration", 0)),
        "avg_view_pct":      float(row_map.get("averageViewPercentage", 0)),
        "impressions":       int(row_map.get("impressions", 0)),
        "ctr":               float(row_map.get("videoThumbnailImpressionsClickRate", 0)),
        "collected_date":    str(today),
    }

    # Upsert into performance_history.json
    hist_path = Path(HISTORY_FILE)
    history   = json.loads(hist_path.read_text()) if hist_path.exists() else []
    idx = next((i for i, h in enumerate(history) if h["video_id"] == video_id), None)
    if idx is not None:
        history[idx] = record
    else:
        history.append(record)
    hist_path.parent.mkdir(exist_ok=True)
    hist_path.write_text(json.dumps(history, indent=2))

    print(f"  [Analytics] {video_id}: {record['views']} views, "
          f"{record['avg_view_duration']:.0f}s avg, "
          f"{record['avg_view_pct']:.1f}% completion")
    return record


def run_pending_analytics() -> int:
    """
    Check all published videos >= ANALYTICS_DELAY_DAYS old that haven't been collected.
    Returns count of videos processed.
    """
    pub_path = Path(PUBLISHED_FILE)
    if not pub_path.exists():
        return 0

    published = json.loads(pub_path.read_text())
    hist_path = Path(HISTORY_FILE)
    history   = json.loads(hist_path.read_text()) if hist_path.exists() else []
    collected = {h["video_id"] for h in history}

    today   = date.today()
    count   = 0
    for v in published:
        pub_date = date.fromisoformat(v["publish_date"])
        days_old = (today - pub_date).days
        if days_old >= ANALYTICS_DELAY_DAYS and v["video_id"] not in collected:
            result = collect_analytics(
                v["video_id"], v.get("topic", ""), v.get("audience_type", "")
            )
            if result:
                count += 1

    return count
