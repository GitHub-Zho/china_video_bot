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


def extract_insights() -> dict:
    """
    Phase E — Distil performance_history.json into structured patterns.

    Writes data/insights.json which is read by director_agent._load_insights().
    Returns the insights dict (empty dict if fewer than 3 data points).
    """
    hist_path = Path(HISTORY_FILE)
    if not hist_path.exists():
        return {}

    history = json.loads(hist_path.read_text())
    if len(history) < 3:
        return {}

    # ── High-CTR patterns ──────────────────────────────────────────────────────
    # CTR > 5% is generally considered good for YouTube Shorts / Reels style content
    CTR_THRESHOLD = 0.05          # 5%
    RETENTION_THRESHOLD = 50.0    # 50% average view percentage

    high_ctr = [
        h for h in history
        if h.get("ctr", 0) >= CTR_THRESHOLD and h.get("views", 0) >= 10
    ]
    high_retention = [
        h for h in history
        if h.get("avg_view_pct", 0) >= RETENTION_THRESHOLD and h.get("views", 0) >= 10
    ]

    # Sort best first
    high_ctr.sort(key=lambda h: h.get("ctr", 0), reverse=True)
    high_retention.sort(key=lambda h: h.get("avg_view_pct", 0), reverse=True)

    # Build text patterns from top performers
    high_ctr_patterns = [
        f"{h['topic']} ({h['audience_type']}, CTR {h['ctr']*100:.1f}%)"
        for h in high_ctr[:5]
    ]
    high_retention_topics = [
        f"{h['topic']} ({h['audience_type']}, {h['avg_view_pct']:.0f}% watched)"
        for h in high_retention[:5]
    ]

    # ── Low-performers to avoid ────────────────────────────────────────────────
    low_performers = [
        h for h in history
        if h.get("views", 0) >= 10 and (
            h.get("avg_view_pct", 100) < 25 or h.get("ctr", 1) < 0.01
        )
    ]
    low_performers.sort(key=lambda h: h.get("avg_view_pct", 100))
    avoid = [
        f"{h['topic']} ({h['audience_type']}, {h.get('avg_view_pct',0):.0f}% retention)"
        for h in low_performers[:3]
    ]

    # ── Best audience split ────────────────────────────────────────────────────
    by_type: dict[str, list] = {}
    for h in history:
        at = h.get("audience_type", "unknown")
        by_type.setdefault(at, []).append(h.get("avg_view_pct", 0))
    best_audience = max(by_type, key=lambda k: sum(by_type[k]) / len(by_type[k])) \
                    if by_type else None

    insights = {
        "high_ctr_patterns":      high_ctr_patterns,
        "high_retention_topics":  high_retention_topics,
        "avoid":                  avoid,
        "best_audience_type":     best_audience,
        "total_videos_analyzed":  len(history),
        "updated":                str(date.today()),
    }

    insights_path = Path("data/insights.json")
    insights_path.parent.mkdir(exist_ok=True)
    insights_path.write_text(json.dumps(insights, indent=2))

    print(f"  [Analytics] 📊 Insights updated — "
          f"{len(high_ctr_patterns)} CTR patterns, "
          f"{len(high_retention_topics)} retention topics, "
          f"{len(avoid)} topics to avoid")

    # Write to learning log (transparent audit trail)
    try:
        from agents.learning_log_agent import log_analytics_update
        log_analytics_update(insights, n_videos=len(history))
    except Exception as e:
        print(f"  [Analytics] ⚠️  Learning log write failed: {e}")

    return insights


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
