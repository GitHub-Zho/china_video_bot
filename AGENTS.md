# AGENTS.md

## Commands

- Install: `python -m pip install -r requirements.txt`
- Review a script first: `python scripts/run.py --prompt "TOPIC" --review`
- Build locally without publishing: `python scripts/run.py --from-brief PATH --dry-run`

## Rules

- Default to `--review` and `--dry-run`; publishing to YouTube or Instagram requires explicit approval.
- Keep API keys, OAuth credentials, and platform tokens in ignored local environment files only.
- Never print, copy, or commit credentials. Refer to keys by variable name or fingerprint only.
- Treat Unsplash as an optional photo source; verify that Pexels fallback is not hiding Unsplash authentication failures.
- Preserve generated output and learning logs unless the task explicitly targets them.
- Preserve unrelated working-tree changes.

## Unsplash rotation

- The runtime reads `UNSPLASH_ACCESS_KEY`; the secret key is not required by current Python runtime paths.
- Before deleting an Unsplash application, confirm the retained access-key fingerprint belongs to a different application.
- After revocation, run a direct search check and verify both image and media agent paths without relying on fallback.
