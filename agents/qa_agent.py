"""
QA Agent — automatic video quality check via frame sampling + Gemini Vision.

Runs after assembly. Extracts N frames at strategic intervals, sends them to
Gemini Vision (the verifier), and returns a structured list of issues.

Called by the orchestrator after assemble_video(). If issues are found, they
are printed and written to the learning log so the Director can improve.

Checks performed:
  - Hook card: text readable, not clipped, not too dense
  - Subtitle visibility: text visible, not lingering, correct position
  - Visual quality: no black/corrupted frames, scene not blurry
  - Transition: no jarring cuts or freeze frames
"""
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config.settings import FFMPEG_BIN, FFPROBE_BIN


@dataclass
class QAIssue:
    timestamp_s: float
    severity:    str        # "error" | "warning" | "info"
    category:    str        # "subtitle" | "hook" | "visual" | "transition"
    description: str

    def __str__(self):
        icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(self.severity, "?")
        return f"  {icon} [{self.category}] t={self.timestamp_s:.1f}s — {self.description}"


@dataclass
class QAReport:
    video_path:  str
    issues:      list[QAIssue] = field(default_factory=list)
    passed:      bool = True

    def add(self, issue: QAIssue):
        self.issues.append(issue)
        if issue.severity == "error":
            self.passed = False

    def print_summary(self):
        errors   = sum(1 for i in self.issues if i.severity == "error")
        warnings = sum(1 for i in self.issues if i.severity == "warning")
        status   = "✅ PASS" if self.passed else "❌ FAIL"
        print(f"  [QA] {status}  {errors} error(s), {warnings} warning(s)")
        for issue in self.issues:
            print(str(issue))


# ── Frame extraction ──────────────────────────────────────────────────────────

def _extract_frames(video_path: str, timestamps: list[float], out_dir: Path) -> list[tuple[float, Path]]:
    """Extract frames at specific timestamps. Returns [(t, path), ...]."""
    results = []
    for t in timestamps:
        out = out_dir / f"frame_{t:.2f}.jpg"
        # Downscale to 720px wide — keeps subtitles/content legible for the
        # verifier while shrinking the payload ~5× (avoids proxy/timeout issues).
        r = subprocess.run([
            FFMPEG_BIN, "-y", "-ss", str(t), "-i", video_path,
            "-frames:v", "1", "-vf", "scale=720:-1", "-q:v", "5", str(out)
        ], capture_output=True)
        if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            results.append((t, out))
    return results


def _get_video_duration(video_path: str) -> float:
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _strategic_timestamps(duration: float, hook_seconds: float = 2.0) -> list[float]:
    """
    Choose sampling times that cover the most important moments:
    - 0.5s: hook card content
    - hook+0.3s: first moment after hook (check transition)
    - every 4s through the video
    - last 2s: CTA
    """
    ts = [0.5]
    if hook_seconds > 0:
        ts.append(hook_seconds + 0.3)   # right after hook ends
    # Body: sample every 4 seconds
    t = hook_seconds + 4.0
    while t < duration - 3:
        ts.append(round(t, 1))
        t += 4.0
    # Near end
    if duration > 4:
        ts.append(round(duration - 1.5, 1))
    return [t for t in ts if 0 <= t < duration]


# ── Gemini Vision analysis ────────────────────────────────────────────────────

_QA_PROMPT = """\
You are a video QA reviewer for short-form China travel videos (Instagram Reels / YouTube Shorts).

Review each frame (labelled with its timestamp) and identify any quality issues.

Check for:
1. SUBTITLE issues: text not visible, too small to read, positioned too low/high (cut off by platform UI),
   subtitle still showing when it should have cleared, text overflowing the frame, leaked filter code
2. HOOK CARD issues: hook text not readable, too much text, text clipped at edges,
   the transition moment (frames right after the hook should be smooth, not a black frame or freeze)
3. VISUAL issues: black or corrupted frame, severe blur, wrong aspect ratio, repeated/looping visual
4. COMPOSITION issues: important content obscured by text overlay

Return ONLY a JSON array (can be empty if no issues):
[
  {"timestamp_s": 0.5, "severity": "warning", "category": "subtitle",
   "description": "Subtitle text is very small and barely visible"}
]

If everything looks fine, return: []"""


def _analyse_frames(frames: list[tuple[float, Path]]) -> tuple[list[QAIssue], bool]:
    """
    Send sampled frames to the verifier (Gemini).
    Returns (issues, vision_ran). vision_ran=False means the check couldn't run
    (no key / API error) — so an empty list does NOT mean the video is clean.
    """
    from agents.vision import analyse_images_json, vision_available

    if not frames:
        return [], False
    if not vision_available():
        print("  [QA] GEMINI_API_KEY not set — skipping Vision check")
        return [], False

    paths  = [str(p) for _, p in frames]
    labels = [f"[Frame at t={t:.1f}s]" for t, _ in frames]
    data   = analyse_images_json(paths, _QA_PROMPT, labels)

    if data is None:
        print("  [QA] Vision analysis unavailable (API error) — NOT a clean pass")
        return [], False
    if not isinstance(data, list):
        return [], True
    issues = [
        QAIssue(
            timestamp_s=item.get("timestamp_s", 0),
            severity=item.get("severity", "warning"),
            category=item.get("category", "visual"),
            description=item.get("description", ""),
        )
        for item in data if isinstance(item, dict)
    ]
    return issues, True


# ── Public API ────────────────────────────────────────────────────────────────

def qa_check(
    video_path: str,
    hook_seconds: float = 2.0,
    use_vision: bool = True,
) -> QAReport:
    """
    Run QA on a finished video. Returns a QAReport.

    hook_seconds: duration of the hook card prepended to the video (default 2s).
    use_vision:   if True, sends frames to Gemini Vision for content analysis.
                  Set False to skip API cost (frame extraction still runs).
    """
    report = QAReport(video_path=video_path)
    print(f"  [QA] Checking: {Path(video_path).name}")

    if not Path(video_path).exists():
        report.add(QAIssue(0, "error", "visual", "Video file not found"))
        report.print_summary()
        return report

    duration = _get_video_duration(video_path)
    if duration < 5:
        report.add(QAIssue(0, "error", "visual", f"Video too short: {duration:.1f}s"))
        report.print_summary()
        return report

    # Extract frames
    timestamps = _strategic_timestamps(duration, hook_seconds)
    tmp_dir    = Path(tempfile.mkdtemp(prefix="qa_"))
    frames     = _extract_frames(video_path, timestamps, tmp_dir)

    if not frames:
        report.add(QAIssue(0, "error", "visual", "Could not extract any frames from video"))
        report.print_summary()
        return report

    print(f"  [QA] Extracted {len(frames)} frame(s) — sending to Gemini Vision…")

    vision_ran = False
    if use_vision:
        issues, vision_ran = _analyse_frames(frames)
        for issue in issues:
            report.add(issue)

    # Cleanup
    for f in tmp_dir.iterdir():
        f.unlink(missing_ok=True)
    tmp_dir.rmdir()

    if use_vision and not vision_ran:
        print("  [QA] ⚠️  Vision did not run — quality NOT verified (not a pass)")
    else:
        report.print_summary()

    # Write issues to learning log if any warnings/errors found
    if report.issues:
        _log_qa_issues(video_path, report.issues)

    return report


def _log_qa_issues(video_path: str, issues: list[QAIssue]) -> None:
    """Append a QA findings entry to the learning log."""
    try:
        from agents.learning_log_agent import append_entry
        from datetime import date

        issue_lines = "\n".join(f"  {str(i)}" for i in issues)
        append_entry(
            entry_type="ANALYTICS",
            title=f"QA issues in {Path(video_path).name}",
            source=f"qa_agent.qa_check() on {Path(video_path).name}",
            analysis=f"Gemini Vision found {len(issues)} issue(s):\n{issue_lines}",
            action_taken="Issues logged. Review and update guidelines if pattern repeats.",
            expected_effect="If same issue appears in 3+ videos, add a rule to director_guidelines.json.",
            conflicts="None.",
            status="⏳ Monitor — no action taken yet",
        )
    except Exception as e:
        pass   # log failure is non-fatal
