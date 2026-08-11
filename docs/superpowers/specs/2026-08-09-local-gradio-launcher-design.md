# Local Gradio Launcher Design

## Goal

Add a minimal browser-based launcher for China Video Bot so a user can generate videos locally without opening Codex or manually assembling CLI commands.

The launcher is a local-only interface. Version 1 never publishes content and always runs the existing pipeline with `--dry-run` semantics.

## User flow

1. The user double-clicks a macOS launcher script.
2. The launcher starts a Gradio server bound to `127.0.0.1` and opens the browser.
3. The user chooses one of two modes:
   - Mode 1: generate from a topic.
   - Mode 2: generate from a reference video URL and topic.
4. The user fills in the mode-specific fields and starts generation.
5. The page streams process logs and shows running, completed, or failed state.
6. On success, the page displays available YouTube and Reels outputs and identifies the output directory.

## Interface

### Shared controls

- Mode selector: Topic or Reference video.
- Target duration in seconds, optional.
- Review script first, off by default.
- Start button.
- Read-only live log panel.
- Result area with output paths and video players when files exist.

### Mode 1 controls

- Topic or creative prompt, required.
- Content type: `growth`, `info`, or `both`.
- Disable automatic Scout, enabled by default until the known Scout relevance fixes are implemented and verified.

### Mode 2 controls

- Reference video URL, required.
- Topic label, required.
- Sample interval, defaulting to the CLI default.

## Architecture

- Add one Gradio application module dedicated to presentation and process control.
- Keep `scripts/run.py` as the only pipeline entry point. The UI invokes it as a subprocess using an argument list, never a shell command string.
- Use the current Python interpreter and set the subprocess working directory to the repository root.
- Append `--dry-run` unconditionally. There is no publishing control in version 1.
- Stream merged stdout and stderr to the UI while retaining enough recent output to diagnose failures.
- Allow only one active generation process per launcher instance.
- Detect output paths from the completed command output and/or newly created output directory. Only expose files inside the repository output directory.

## Security and privacy

- Bind only to `127.0.0.1`; do not create a public share link.
- Continue loading API credentials through the existing pipeline configuration. Do not add credential fields to the page.
- Never display `.env` contents or inject secrets into command arguments.
- Validate mode-specific required fields and reject malformed or unsupported inputs before starting.
- Do not use `shell=True`.

## Error handling

- Missing Python dependencies: show a short installation-oriented error and preserve the underlying log.
- Invalid fields: show validation errors without launching a process.
- Pipeline non-zero exit: mark the run failed and leave the complete captured log visible.
- Browser closure must not silently start a second task. A new task is accepted only after the active process exits.
- Review-only runs are successful even when no final videos exist; the UI should show the generated `brief.json` path when available.

## Launcher

- Provide a macOS double-clickable `.command` file that changes to the repository directory, starts the Gradio app, and opens the local page through Gradio's launch behavior.
- Keep a documented terminal command as a fallback.
- The launcher must not install packages automatically. Setup documentation will provide the explicit dependency-install command.

## Verification

- Unit-test argument construction for both modes, including unconditional local-only behavior.
- Test validation failures and the single-active-run guard without calling external APIs.
- Smoke-test the Gradio app locally and verify it binds to loopback.
- Run a mocked subprocess to verify streamed logs, success/failure state, and output discovery.
- Perform one manual browser check for responsive layout and mode switching.

## Out of scope for version 1

- YouTube or Instagram publishing.
- API-key editing or account management.
- Multiple concurrent jobs or a persistent job queue.
- Remote/LAN access, authentication, or cloud hosting.
- Editing `brief.json` inside the browser.
- Repairing the three outstanding Scout relevance problems; the UI defaults Scout off as a temporary safeguard.
