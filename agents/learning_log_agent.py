"""
Learning Log Agent — transparent audit trail for all Director learning events.

Every time the Director's knowledge is updated (analytics insights OR human-guided
rules), this agent appends a structured entry to data/learning_log.md.

The log is designed for human review: you can read it, spot wrong conclusions,
mark conflicts, and override decisions before they affect the next video.

Entry types:
  ANALYTICS  — auto-extracted from YouTube performance data
  GUIDELINE  — manually triggered after human feedback to Claude
  CONFLICT   — detected contradiction between data and human rules
  RESOLVED   — a conflict that was resolved (by human or by data)
"""
import json
from datetime import date
from pathlib import Path
from typing import Literal

LOG_PATH        = Path("data/learning_log.md")
GUIDELINES_PATH = Path("data/director_guidelines.json")
INSIGHTS_PATH   = Path("data/insights.json")

EntryType = Literal["ANALYTICS", "GUIDELINE", "CONFLICT", "RESOLVED"]


# ── Core writer ───────────────────────────────────────────────────────────────

def append_entry(
    entry_type: EntryType,
    title: str,
    source: str,
    analysis: str,
    action_taken: str,
    expected_effect: str,
    conflicts: str = "None detected.",
    status: str = "✅ Applied",
) -> None:
    """
    Append one structured entry to the learning log.
    Called automatically after analytics extraction or guideline updates.
    """
    today = str(date.today())

    # Count existing entries for a sequential ID
    existing = LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else ""
    entry_num = existing.count("\n## ") + 1

    block = f"""
## [{entry_num}] {today} · {entry_type} — {title}

**Source:** {source}

**Analysis:**
{analysis}

**Action taken:**
{action_taken}

**Expected effect:**
{expected_effect}

**Conflicts with existing rules:**
{conflicts}

**Status:** {status}

---
"""
    LOG_PATH.parent.mkdir(exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text(
            "# Director Learning Log\n\n"
            "Every entry records a change to the Director Agent's knowledge.\n"
            "Review this file to audit, question, or override any decision.\n"
            "Mark a conflict's status as `❌ Rejected` or `⚠️ Review needed` to flag it.\n\n"
            "---\n",
            encoding="utf-8",
        )
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(block)

    print(f"  [LearningLog] 📝 Entry #{entry_num} written: [{entry_type}] {title}")


# ── Conflict detector ─────────────────────────────────────────────────────────

def detect_conflicts(insights: dict, guidelines: dict) -> list[str]:
    """
    Compare analytics insights with human guidelines and return a list of
    plain-English conflict descriptions (empty list = no conflicts).
    """
    conflicts = []

    # Check 1: best audience type from data vs guideline bias
    # (guidelines don't have audience preference explicitly, but we can infer
    # from the examples — they currently default to explorer)
    best_audience = insights.get("best_audience_type")
    guideline_examples = " ".join(
        guidelines.get("example_good_narrations", []) +
        guidelines.get("do", [])
    ).lower()

    if best_audience == "newcomer" and "newcomer" not in guideline_examples:
        conflicts.append(
            f"Data shows '{best_audience}' audience has highest retention, "
            f"but current guidelines and examples lean toward 'explorer' content. "
            f"Possible cause: small sample size, or guideline bias."
        )

    # Check 2: topics to avoid from data vs topics featured in guidelines
    data_avoid = [t.lower() for t in insights.get("avoid", [])]
    guideline_dos = " ".join(guidelines.get("do", [])).lower()
    for avoided_topic in data_avoid:
        # Extract just the topic name (before the parenthesis)
        topic_name = avoided_topic.split("(")[0].strip()
        if topic_name and topic_name in guideline_dos:
            conflicts.append(
                f"Analytics says avoid '{topic_name}', "
                f"but a DO rule in guidelines mentions it positively. "
                f"Check whether the guideline was written before this data existed."
            )

    # Check 3: high-CTR patterns vs AVOID rules
    high_ctr = [t.lower() for t in insights.get("high_ctr_patterns", [])]
    guideline_avoids = " ".join(guidelines.get("avoid", [])).lower()
    for pattern in high_ctr:
        topic_name = pattern.split("(")[0].strip()
        if topic_name and topic_name in guideline_avoids:
            conflicts.append(
                f"Analytics shows high CTR for '{topic_name}', "
                f"but an AVOID rule in guidelines discourages it. "
                f"Human rule may be too broad or based on incorrect assumption."
            )

    return conflicts


# ── High-level helpers ────────────────────────────────────────────────────────

def log_analytics_update(insights: dict, n_videos: int) -> None:
    """
    Log an analytics-driven insights update.
    Call this right after extract_insights() runs.
    """
    guidelines = {}
    if GUIDELINES_PATH.exists():
        try:
            guidelines = json.loads(GUIDELINES_PATH.read_text())
        except Exception:
            pass

    conflicts_found = detect_conflicts(insights, guidelines)
    conflicts_text = (
        "\n".join(f"  ⚠️  {c}" for c in conflicts_found)
        if conflicts_found
        else "None detected."
    )

    # Build readable summary of what insights contain
    analysis_lines = [f"Analysed {n_videos} published video(s)."]
    if insights.get("high_ctr_patterns"):
        analysis_lines.append(
            "High CTR (>5%): " + "; ".join(insights["high_ctr_patterns"][:3])
        )
    if insights.get("high_retention_topics"):
        analysis_lines.append(
            "Best retention (>50% watched): "
            + "; ".join(insights["high_retention_topics"][:3])
        )
    if insights.get("avoid"):
        analysis_lines.append(
            "Low performers to avoid: " + "; ".join(insights["avoid"][:3])
        )
    if insights.get("best_audience_type"):
        analysis_lines.append(
            f"Best-performing audience type: {insights['best_audience_type']}"
        )

    action = "Updated data/insights.json. Director will read on next run."
    if conflicts_found:
        action += f"\n⚠️  {len(conflicts_found)} conflict(s) detected — see Conflicts section."

    status = "✅ Applied" if not conflicts_found else "⚠️  Applied with conflicts — review needed"

    append_entry(
        entry_type="ANALYTICS",
        title=f"Insights from {n_videos} video(s)",
        source="YouTube Analytics API → analytics_agent.extract_insights()",
        analysis="\n".join(analysis_lines),
        action_taken=action,
        expected_effect=(
            "Director will lean toward high-CTR topics and audiences. "
            "Low-performing topic patterns will be injected as soft avoids."
        ),
        conflicts=conflicts_text,
        status=status,
    )

    if conflicts_found:
        _log_conflicts(conflicts_found, source="analytics vs guidelines")


def log_guideline_update(
    reason: str,
    changes: dict,
    version_before: int,
    version_after: int,
) -> None:
    """
    Log a human-guided guidelines update.

    changes should be a dict like:
      {
        "added_do":    ["new rule text"],
        "removed_do":  ["old rule text"],
        "added_avoid": ["new avoid text"],
        "added_examples_good": ["..."],
        "added_examples_bad":  ["..."],
        "style_notes": ["..."],
      }
    """
    lines = []
    for key, items in changes.items():
        if items:
            label = {
                "added_do":            "Added DO rule",
                "removed_do":          "Removed DO rule",
                "added_avoid":         "Added AVOID rule",
                "removed_avoid":       "Removed AVOID rule",
                "added_examples_good": "Added good example",
                "added_examples_bad":  "Added bad example",
                "style_notes":         "Added style note",
            }.get(key, key)
            for item in items:
                lines.append(f"  [{label}] {item}")

    action_text = "\n".join(lines) if lines else "  (no specific changes recorded)"
    action_text += f"\n  Guidelines version: v{version_before} → v{version_after}"

    # Check for conflicts with current analytics
    insights = {}
    if INSIGHTS_PATH.exists():
        try:
            insights = json.loads(INSIGHTS_PATH.read_text())
        except Exception:
            pass

    guidelines = {}
    if GUIDELINES_PATH.exists():
        try:
            guidelines = json.loads(GUIDELINES_PATH.read_text())
        except Exception:
            pass

    conflicts_found = detect_conflicts(insights, guidelines) if insights else []
    conflicts_text = (
        "\n".join(f"  ⚠️  {c}" for c in conflicts_found)
        if conflicts_found
        else "None detected."
    )

    status = "✅ Applied" if not conflicts_found else "⚠️  Applied with conflicts — review needed"

    append_entry(
        entry_type="GUIDELINE",
        title=f"v{version_before} → v{version_after}: {reason[:60]}",
        source=f"Human feedback → Claude analysis → director_guidelines.json",
        analysis=f"Feedback received: {reason}",
        action_taken=action_text,
        expected_effect=(
            "Director Groq prompt will include updated rules on next run. "
            "Critic scores for specificity/reels_fit/word_count should reflect the change."
        ),
        conflicts=conflicts_text,
        status=status,
    )


def _log_conflicts(conflicts: list[str], source: str) -> None:
    """Write a dedicated CONFLICT entry when contradictions are found."""
    for i, conflict in enumerate(conflicts, 1):
        append_entry(
            entry_type="CONFLICT",
            title=f"Contradiction #{i} detected ({source})",
            source=source,
            analysis=conflict,
            action_taken="No automatic resolution. Flagged for human review.",
            expected_effect="No change until human resolves this conflict.",
            conflicts="N/A — this entry IS the conflict.",
            status="⏳ Awaiting human decision — edit this entry's Status line when resolved",
        )
