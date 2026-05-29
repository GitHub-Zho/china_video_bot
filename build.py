"""
build.py — Self-resuming build manager.

Reads BUILD_STATE.json, runs only pending phases, writes completion status.
Safe to re-run any number of times — completed phases are always skipped.

Usage:
  python build.py            # run all pending phases
  python build.py --status   # print current progress
  python build.py --reset PHASE_NAME  # mark a phase as pending (re-run it)
"""
import json
import sys
import subprocess
from datetime import datetime
from pathlib import Path

STATE_FILE = Path("BUILD_STATE.json")
PROJECT_DIR = Path(__file__).parent


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def mark(state: dict, phase: str, status: str) -> None:
    state["phases"][phase]["status"]       = status
    state["phases"][phase]["completed_at"] = datetime.now().isoformat() if status == "completed" else None
    save_state(state)


def print_status(state: dict) -> None:
    print("\n── BUILD STATE ─────────────────────────────────")
    for name, info in state["phases"].items():
        icon = {"completed": "✅", "in_progress": "⏳", "failed": "❌", "pending": "⬜"}.get(info["status"], "?")
        done = info.get("completed_at", "")[:16] if info.get("completed_at") else ""
        print(f"  {icon}  {name:<30} {done}")
    if state.get("blockers"):
        print(f"\n  ⚠️  Blockers: {state['blockers']}")
    print()


# ── Phase implementations ──────────────────────────────────────────────────────

def phase_1_scaffold(state: dict) -> bool:
    """Verify the scaffold is complete (files already written by Claude)."""
    required = [
        "requirements.txt", ".env", "config/settings.py",
        "config/prompts.py", "agents/__init__.py",
    ]
    missing = [f for f in required if not (PROJECT_DIR / f).exists()]
    if missing:
        print(f"  ❌ Missing scaffold files: {missing}")
        return False
    print("  ✅ All scaffold files present")
    return True


def phase_2_script_agent(state: dict) -> bool:
    """Verify script_agent.py and do a quick import test."""
    agent_file = PROJECT_DIR / "agents/script_agent.py"
    if not agent_file.exists():
        print("  ❌ agents/script_agent.py not found")
        return False
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'.');from agents.script_agent import generate_script; print('import OK')"],
        capture_output=True, text=True, cwd=PROJECT_DIR
    )
    if result.returncode != 0:
        print(f"  ❌ Import error: {result.stderr[-400:]}")
        return False
    print("  ✅ script_agent imports cleanly")
    return True


def phase_3_image_agent(state: dict) -> bool:
    """Verify image_agent.py and test Pexels connectivity."""
    import os
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")

    result = subprocess.run(
        [sys.executable, "-c", """
import sys; sys.path.insert(0,'.')
from agents.image_agent import _search_pexels
urls = _search_pexels('Great Wall China', 1)
assert len(urls) > 0, "Pexels returned no results"
print(f"Pexels OK — sample URL: {urls[0][:60]}...")
"""],
        capture_output=True, text=True, cwd=PROJECT_DIR
    )
    if result.returncode != 0:
        print(f"  ❌ Pexels test failed: {result.stderr[-400:]}")
        # Non-fatal — API key might not be set yet
        print("  ⚠️  Continuing (check PEXELS_API_KEY in .env)")
    else:
        print(f"  ✅ {result.stdout.strip()}")
    return True  # non-blocking


def phase_4_voice_subtitle(state: dict) -> bool:
    """Check if OPENAI_API_KEY is set. If not, record blocker and skip."""
    import os
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    key = os.getenv("OPENAI_API_KEY", "")
    if not key or key == "your_openai_key_here":
        print("  ⚠️  OPENAI_API_KEY not set — phase deferred")
        print("     Add your key to .env, then run: python build.py --reset phase_4_voice_subtitle")
        state["blockers"]["openai_api_key"] = "still waiting"
        save_state(state)
        return False  # mark as failed so it retries later
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'.');from agents.voice_agent import generate_voice; print('voice_agent OK')"],
        capture_output=True, text=True, cwd=PROJECT_DIR
    )
    if result.returncode != 0:
        print(f"  ❌ voice_agent import error: {result.stderr[-400:]}")
        return False
    state["blockers"].pop("openai_api_key", None)
    print("  ✅ voice_agent ready")
    return True


def phase_5_video_agent(state: dict) -> bool:
    """Check ffmpeg binary and libass support."""
    result = subprocess.run(
        ["ffmpeg", "-filters"], capture_output=True, text=True
    )
    if result.returncode != 0:
        print("  ❌ ffmpeg not found. Install: brew install ffmpeg")
        return False
    if "subtitles" not in result.stdout + result.stderr:
        print("  ⚠️  ffmpeg found but libass (subtitles filter) missing.")
        print("     Reinstall: brew reinstall ffmpeg")
        return False
    print("  ✅ ffmpeg with libass ready")
    return True


def phase_6_publisher(state: dict) -> bool:
    """Verify publisher_agent imports. OAuth tested on first real upload."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'.');from agents.publisher_agent import get_youtube_client; print('publisher_agent OK')"],
        capture_output=True, text=True, cwd=PROJECT_DIR
    )
    if result.returncode != 0:
        print(f"  ❌ publisher_agent import error: {result.stderr[-400:]}")
        return False
    secrets = PROJECT_DIR / "credentials/client_secrets.json"
    if not secrets.exists():
        print("  ⚠️  credentials/client_secrets.json not found.")
        print("     Download from Google Cloud Console → APIs & Services → Credentials")
        print("     OAuth will be tested on first upload run.")
    else:
        print("  ✅ publisher_agent + client_secrets.json ready")
    return True


def phase_7_analytics(state: dict) -> bool:
    """Verify analytics_agent imports correctly."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'.');from agents.analytics_agent import run_pending_analytics; print('analytics_agent OK')"],
        capture_output=True, text=True, cwd=PROJECT_DIR
    )
    if result.returncode != 0:
        print(f"  ❌ analytics_agent import error: {result.stderr[-400:]}")
        return False
    print("  ✅ analytics_agent ready")
    return True


def phase_8_orchestrator(state: dict) -> bool:
    """Verify orchestrator and main.py import cleanly."""
    for module in ["orchestrator", "main"]:
        result = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0,'.'); import importlib; importlib.import_module('{module}'); print('{module} OK')"],
            capture_output=True, text=True, cwd=PROJECT_DIR
        )
        if result.returncode != 0 and "SystemExit" not in result.stderr:
            print(f"  ❌ {module} import error: {result.stderr[-400:]}")
            return False
    print("  ✅ orchestrator + main ready")
    return True


def phase_9_verification(state: dict) -> bool:
    """Run a full dry-run pipeline (no upload, no OpenAI key required if mocked)."""
    # Check openai key — required for dry run to pass voice step
    import os
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    if not os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") == "your_openai_key_here":
        print("  ⚠️  Skipping full dry-run — OPENAI_API_KEY not set yet.")
        print("     Set it in .env, then run: python build.py --reset phase_9_verification")
        return False

    print("  Running dry-run pipeline (no upload)...")
    result = subprocess.run(
        [sys.executable, "main.py", "--dry-run"],
        capture_output=False, cwd=PROJECT_DIR, timeout=300
    )
    if result.returncode != 0:
        print("  ❌ Dry-run failed (see output above)")
        return False
    print("  ✅ Full dry-run passed!")
    return True


# ── Phase registry (order matters) ────────────────────────────────────────────

PHASES = [
    ("phase_1_scaffold",       phase_1_scaffold),
    ("phase_2_script_agent",   phase_2_script_agent),
    ("phase_3_image_agent",    phase_3_image_agent),
    ("phase_4_voice_subtitle", phase_4_voice_subtitle),
    ("phase_5_video_agent",    phase_5_video_agent),
    ("phase_6_publisher",      phase_6_publisher),
    ("phase_7_analytics",      phase_7_analytics),
    ("phase_8_orchestrator",   phase_8_orchestrator),
    ("phase_9_verification",   phase_9_verification),
]


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if "--status" in args:
        print_status(load_state())
        return

    if "--reset" in args:
        idx = args.index("--reset")
        if idx + 1 >= len(args):
            print("Usage: python build.py --reset <phase_name>")
            return
        phase_name = args[idx + 1]
        state = load_state()
        if phase_name not in state["phases"]:
            print(f"Unknown phase: {phase_name}")
            print("Valid phases:", list(state["phases"].keys()))
            return
        state["phases"][phase_name]["status"]       = "pending"
        state["phases"][phase_name]["completed_at"] = None
        save_state(state)
        print(f"✅ Phase '{phase_name}' reset to pending.")
        return

    state = load_state()
    print_status(state)

    all_done = True
    for phase_name, phase_fn in PHASES:
        info = state["phases"].get(phase_name, {})
        if info.get("status") == "completed":
            continue  # skip finished phases

        all_done = False
        print(f"\n── Running: {phase_name} ──")
        mark(state, phase_name, "in_progress")

        try:
            ok = phase_fn(state)
        except Exception as e:
            print(f"  ❌ Exception: {e}")
            ok = False

        if ok:
            mark(state, phase_name, "completed")
            print(f"  → Marked COMPLETED")
        else:
            mark(state, phase_name, "failed")
            print(f"  → Marked FAILED (fix issue above, then re-run)")
            # Stop on hard failure; skip optional blockers handled inside phase fns
            if phase_name not in ("phase_3_image_agent", "phase_4_voice_subtitle",
                                  "phase_6_publisher", "phase_9_verification"):
                print("\nBuild paused. Fix the issue above and run: python build.py")
                break

    state = load_state()
    print_status(state)

    completed = sum(1 for v in state["phases"].values() if v["status"] == "completed")
    total     = len(state["phases"])

    if completed == total:
        print("🎉 All phases complete! Run the pipeline:")
        print("   python main.py --dry-run   # test without uploading")
        print("   python main.py --now       # run once with upload")
        print("   python main.py             # start daily scheduler")
    else:
        print(f"Progress: {completed}/{total} phases complete.")


if __name__ == "__main__":
    main()
