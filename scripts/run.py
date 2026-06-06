#!/usr/bin/env python3
"""
China Video Bot — command-line entry point.

Designed for autonomous server operation (AWS / Oracle). No Claude in the loop:
the pipeline self-drives with Groq (generation) + Gemini (verification) + Kokoro
(voice) + Pexels/Pixabay (media).

Usage:
  python scripts/run.py                                  # daily auto mode (publishes)
  python scripts/run.py --dry-run                        # build locally, no upload
  python scripts/run.py --prompt "Chengdu hot pot"       # topic-driven
  python scripts/run.py --prompt "..." --audience newcomer
  python scripts/run.py --from-folder ~/my_photos --dry-run   # user's own media
  python scripts/run.py --seconds 24                     # override target length

Exit code 0 on success, 1 on failure (so cron / CI can detect problems).
"""
import argparse
import sys
import traceback
from pathlib import Path

# Make the project root importable when run as `python scripts/run.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="China Video Bot — auto-generate short-form China travel videos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--prompt", default="",
                        help="Free-text creative direction (e.g. 'Chengdu hot pot for newcomers')")
    parser.add_argument("--audience", choices=["explorer", "newcomer"], default=None,
                        help="Target audience type (default: Director picks)")
    parser.add_argument("--seconds", type=float, default=None,
                        help="Target video length in seconds (default from settings)")
    parser.add_argument("--from-folder", metavar="PATH", default=None,
                        help="Build from your own images/clips instead of stock footage")
    parser.add_argument("--style", default="",
                        help="Name of a saved StyleProfile to imitate")
    parser.add_argument("--learn-style", nargs=2, metavar=("SOURCE", "NAME"), default=None,
                        help="Analyse a reference video (file or URL) and save it as a named style, then exit")
    parser.add_argument("--review", action="store_true",
                        help="Generate the SCRIPT only and stop for approval (edit brief.json, then --from-brief)")
    parser.add_argument("--from-brief", metavar="BRIEF_JSON", default=None,
                        help="Build a video from an approved/edited brief.json")
    parser.add_argument("--fix", metavar="VIDEO_ID", default=None,
                        help="Apply a chosen alternative clip to a flagged scene and re-assemble")
    parser.add_argument("--scene", type=int, default=None, help="Scene index to fix (with --fix)")
    parser.add_argument("--pick", type=int, default=None, help="Alternative index to use (with --fix)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Assemble locally, skip YouTube upload")
    args = parser.parse_args()

    try:
        # Build from an approved/edited script
        if args.from_brief:
            from orchestrator import run_pipeline_from_brief
            result = run_pipeline_from_brief(args.from_brief, dry_run=args.dry_run,
                                             style=args.style)
            print(f"\n[run.py] ✅ Done: {result}")
            return 0 if result else 1
        # Fix mode — swap a scene's clip for a user-picked alternative, re-assemble.
        if args.fix:
            if args.scene is None or args.pick is None:
                print("[run.py] --fix needs --scene N --pick K (see output/<id>/review.json)")
                return 1
            from orchestrator import apply_alternative
            out = apply_alternative(args.fix, args.scene, args.pick)
            print(f"\n[run.py] ✅ Fixed scene {args.scene}: {out}")
            return 0 if out else 1

        # Style learning mode — analyse a reference, save profile, exit.
        if args.learn_style:
            from agents.style_analyst_agent import analyse_style
            source, name = args.learn_style
            sp = analyse_style(source, name)
            if sp:
                print(f"\n[run.py] ✅ Style '{name}' learned "
                      f"({sp.color_mood}, {sp.subtitle_size} {sp.subtitle_position} subs, "
                      f"~{sp.avg_clip_seconds}s/shot)")
                return 0
            print("\n[run.py] ❌ Style analysis failed")
            return 1

        from orchestrator import run_pipeline, run_pipeline_from_folder

        if args.from_folder:
            result = run_pipeline_from_folder(
                args.from_folder, dry_run=args.dry_run,
                target_seconds=args.seconds, style=args.style
            )
        else:
            result = run_pipeline(
                audience_type=args.audience, dry_run=args.dry_run,
                prompt=args.prompt, style=args.style, review=args.review,
                target_seconds=args.seconds
            )

        if result:
            print(f"\n[run.py] ✅ Done: {result}")
            return 0
        print("\n[run.py] ⚠️  Pipeline returned no result")
        return 1

    except KeyboardInterrupt:
        print("\n[run.py] Interrupted")
        return 1
    except Exception as e:
        print(f"\n[run.py] ❌ Pipeline failed: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
