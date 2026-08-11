# Local Gradio Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only Gradio UI that safely launches Mode 1 and Mode 2 video generation and displays logs and local outputs.

**Architecture:** Put command validation, subprocess execution, single-run coordination, and output discovery in a framework-independent `launcher/core.py`. Keep Gradio layout and event wiring in `launcher/app.py`, with `scripts/start_ui.command` as the macOS entry point. The existing `scripts/run.py` remains the sole pipeline entry point.

**Tech Stack:** Python 3, Gradio Blocks, subprocess, pytest, existing China Video Bot CLI.

## Global Constraints

- Every generation command includes `--dry-run`; version 1 has no publishing control.
- Bind Gradio to `127.0.0.1` with `share=False`.
- Never expose `.env` values or pass secrets as command arguments.
- Never invoke the pipeline with `shell=True`.
- Allow one active generation process at a time.
- Default Mode 1 to `--no-scout` until Scout relevance fixes are verified.
- Preserve all unrelated working-tree changes.

---

### Task 1: Safe command construction and output discovery

**Files:**
- Create: `launcher/__init__.py`
- Create: `launcher/core.py`
- Create: `tests/test_launcher_core.py`

**Interfaces:**
- Produces: `LaunchRequest`, `LaunchValidationError`, `build_command(request, python_executable) -> list[str]`, and `discover_outputs(output_root, started_at) -> RunOutputs`.

- [ ] **Step 1: Write failing command-construction tests**

```python
def test_mode1_command_is_local_only_and_disables_scout():
    request = LaunchRequest(mode="topic", prompt="Guilin", video_type="info")
    command = build_command(request, "/venv/bin/python")
    assert command == [
        "/venv/bin/python", "scripts/run.py", "--dry-run", "--prompt", "Guilin",
        "--type", "info", "--no-scout",
    ]

def test_mode2_command_uses_reference_url():
    request = LaunchRequest(
        mode="video", video_url="https://www.bilibili.com/video/BV1x",
        topic="Roast duck", sample_interval=4.0,
    )
    command = build_command(request, "/venv/bin/python")
    assert command[:5] == [
        "/venv/bin/python", "scripts/run.py", "--dry-run", "--from-video",
        "https://www.bilibili.com/video/BV1x",
    ]
    assert "--no-scout" not in command

def test_empty_required_field_is_rejected():
    with pytest.raises(LaunchValidationError, match="请输入主题"):
        build_command(LaunchRequest(mode="topic", prompt=""), sys.executable)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_launcher_core.py -v`
Expected: FAIL because `launcher.core` does not exist.

- [ ] **Step 3: Implement minimal typed request and argument-list builder**

```python
@dataclass(frozen=True)
class LaunchRequest:
    mode: Literal["topic", "video"]
    prompt: str = ""
    video_type: str = "both"
    video_url: str = ""
    topic: str = ""
    seconds: float | None = None
    review: bool = False
    disable_scout: bool = True
    sample_interval: float = 4.0

def build_command(request: LaunchRequest, python_executable: str) -> list[str]:
    command = [python_executable, "scripts/run.py", "--dry-run"]
    # Validate mode-specific fields, append only explicit list elements, and return.
```

- [ ] **Step 4: Add failing output-discovery tests**

```python
def test_discover_outputs_returns_newest_run_files(tmp_path):
    run = tmp_path / "20260811_demo_info"
    run.mkdir()
    (run / "youtube.mp4").touch()
    (run / "reels.mp4").touch()
    result = discover_outputs(tmp_path, started_at=0)
    assert result.output_dir == run.resolve()
    assert result.youtube == (run / "youtube.mp4").resolve()
    assert result.reels == (run / "reels.mp4").resolve()

def test_discover_outputs_supports_review_only_brief(tmp_path):
    run = tmp_path / "20260811_review"
    run.mkdir()
    (run / "brief.json").touch()
    result = discover_outputs(tmp_path, started_at=0)
    assert result.brief == (run / "brief.json").resolve()
```

- [ ] **Step 5: Run output tests and verify RED, then implement discovery**

Run: `python -m pytest tests/test_launcher_core.py -v`
Expected: FAIL because `discover_outputs` is absent; then implement `RunOutputs` and select only resolved children of `output_root` modified at or after `started_at`.

- [ ] **Step 6: Run tests and commit Task 1**

Run: `python -m pytest tests/test_launcher_core.py -v`
Expected: PASS.

```bash
git add launcher/__init__.py launcher/core.py tests/test_launcher_core.py
git commit -m "feat(ui): add safe launcher core"
```

### Task 2: Streaming process runner and single-task guard

**Files:**
- Modify: `launcher/core.py`
- Modify: `tests/test_launcher_core.py`

**Interfaces:**
- Consumes: `LaunchRequest`, `build_command`, `discover_outputs`.
- Produces: `RunEvent(kind: Literal["log", "complete", "error"], message: str, outputs: RunOutputs | None)` and `PipelineRunner.run(request) -> Iterator[RunEvent]`.

- [ ] **Step 1: Write failing streamed-run and concurrency tests**

```python
def test_runner_streams_logs_and_completion(tmp_path):
    process = FakeProcess(lines=["first\n", "second\n"], returncode=0)
    runner = PipelineRunner(tmp_path, popen=lambda *a, **k: process)
    events = list(runner.run(LaunchRequest(mode="topic", prompt="Guilin")))
    assert [event.message for event in events if event.kind == "log"] == ["first", "second"]
    assert events[-1].kind == "complete"

def test_runner_rejects_second_active_run(tmp_path):
    runner = PipelineRunner(tmp_path)
    runner._lock.acquire()
    try:
        events = list(runner.run(LaunchRequest(mode="topic", prompt="Guilin")))
        assert events[-1].kind == "error"
        assert "已有任务" in events[-1].message
    finally:
        runner._lock.release()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_launcher_core.py -v`
Expected: FAIL because `PipelineRunner` and `RunEvent` are absent.

- [ ] **Step 3: Implement minimal runner**

```python
class PipelineRunner:
    def run(self, request: LaunchRequest) -> Iterator[RunEvent]:
        if not self._lock.acquire(blocking=False):
            yield RunEvent("error", "已有任务正在运行，请等待完成。")
            return
        try:
            process = self._popen(
                build_command(request, sys.executable), cwd=self.repo_root,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1,
            )
            for line in process.stdout or ():
                yield RunEvent("log", line.rstrip())
            # Yield complete with discovered outputs on code 0, otherwise error.
        finally:
            self._lock.release()
```

- [ ] **Step 4: Run tests and commit Task 2**

Run: `python -m pytest tests/test_launcher_core.py -v`
Expected: PASS.

```bash
git add launcher/core.py tests/test_launcher_core.py
git commit -m "feat(ui): stream pipeline runs safely"
```

### Task 3: Gradio page and macOS launcher

**Files:**
- Create: `launcher/app.py`
- Create: `tests/test_launcher_app.py`
- Create: `scripts/start_ui.command`
- Modify: `requirements.txt`
- Modify: `README.md`

**Interfaces:**
- Consumes: `LaunchRequest`, `PipelineRunner`, and `RunEvent`.
- Produces: `create_app(runner: PipelineRunner | None = None) -> gr.Blocks` and executable user launcher.

- [ ] **Step 1: Write failing UI configuration tests**

```python
def test_create_app_returns_blocks():
    app = create_app(PipelineRunner(REPO_ROOT))
    assert isinstance(app, gr.Blocks)

def test_launch_configuration_is_loopback_only():
    assert LAUNCH_OPTIONS == {
        "server_name": "127.0.0.1", "server_port": 7860,
        "share": False, "inbrowser": True,
    }
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_launcher_app.py -v`
Expected: FAIL because `launcher.app` does not exist.

- [ ] **Step 3: Build the Gradio Blocks page**

```python
def create_app(runner: PipelineRunner | None = None) -> gr.Blocks:
    runner = runner or PipelineRunner(REPO_ROOT)
    with gr.Blocks(title="China Video Bot") as app:
        gr.Markdown("# China Video Bot\n仅生成到本地，不会发布。")
        mode = gr.Radio(["主题生成", "参考视频生成"], value="主题生成")
        with gr.Group(visible=True) as topic_group:
            prompt = gr.Textbox(label="主题")
            video_type = gr.Dropdown(["growth", "info", "both"], value="both")
            disable_scout = gr.Checkbox(value=True, label="关闭自动 Scout")
        with gr.Group(visible=False) as video_group:
            video_url = gr.Textbox(label="参考视频网址")
            topic = gr.Textbox(label="主题说明")
            sample_interval = gr.Number(value=4.0, label="采样间隔（秒）")
        seconds = gr.Number(label="目标时长（秒）")
        review = gr.Checkbox(value=False, label="先审核脚本")
        start = gr.Button("开始生成", variant="primary")
        status = gr.Markdown("等待开始")
        logs = gr.Textbox(label="运行日志", lines=18, interactive=False)
        youtube = gr.Video(label="YouTube 横版")
        reels = gr.Video(label="Reels 竖版")
        paths = gr.Textbox(label="输出位置", interactive=False)
        mode.change(
            lambda value: (gr.update(visible=value == "主题生成"),
                           gr.update(visible=value == "参考视频生成")),
            mode, [topic_group, video_group],
        )
        start.click(
            run_from_form,
            [mode, prompt, video_type, disable_scout, video_url, topic,
             sample_interval, seconds, review],
            [status, logs, youtube, reels, paths],
        )
    return app
```

- [ ] **Step 4: Add dependency, double-click launcher, and usage docs**

Add `gradio>=5.0` to `requirements.txt`. Create `scripts/start_ui.command` using the repository-relative script directory and `.venv/bin/python` when present, otherwise `python3`; execute `-m launcher.app`. Document setup and launch in README, including that publishing is impossible from this UI.

- [ ] **Step 5: Run automated tests and make launcher executable**

Run: `python -m pytest tests/test_launcher_core.py tests/test_launcher_app.py -v`
Expected: PASS.

Run: `chmod +x scripts/start_ui.command`
Expected: executable bit set.

- [ ] **Step 6: Start local server and verify browser-visible behavior**

Run: `.venv/bin/python -m launcher.app` (or `python3 -m launcher.app`) and verify `http://127.0.0.1:7860/` loads, switches modes, validates required fields, and contains no publishing control.

- [ ] **Step 7: Run full regression checks and commit Task 3**

Run: `python -m pytest -v`
Expected: PASS.

Run: `python -m compileall launcher tests`
Expected: PASS.

```bash
git add launcher/app.py tests/test_launcher_app.py scripts/start_ui.command requirements.txt README.md
git commit -m "feat(ui): add local Gradio launcher"
```
