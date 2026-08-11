"""Gradio interface for local-only China Video Bot generation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import gradio as gr

from launcher.core import LaunchRequest, PipelineRunner, RunEvent, RunOutputs


REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCH_OPTIONS = {
    "server_name": "127.0.0.1",
    "server_port": 7860,
    "share": False,
    "inbrowser": True,
}

CSS = """
.gradio-container { max-width: 1120px !important; margin: 0 auto !important; }
.local-note { border-left: 4px solid #2f7d57; padding: 10px 14px; background: #eef8f2; }
.mode-panel { border-radius: 14px; }
"""


class Runner(Protocol):
    def run(self, request: LaunchRequest) -> Iterator[RunEvent]: ...


def _optional_number(value: float | int | None) -> float | None:
    if value is None:
        return None
    return float(value)


def request_from_form(
    mode_label: str,
    prompt: str,
    video_type: str,
    disable_scout: bool,
    video_url: str,
    topic: str,
    sample_interval: float | int,
    seconds: float | int | None,
    review: bool,
) -> LaunchRequest:
    """Translate UI values into the framework-independent launch request."""
    if mode_label == "参考视频生成":
        return LaunchRequest(
            mode="video",
            video_url=video_url or "",
            topic=topic or "",
            sample_interval=float(sample_interval or 0),
            seconds=_optional_number(seconds),
            review=bool(review),
        )
    return LaunchRequest(
        mode="topic",
        prompt=prompt or "",
        video_type=video_type or "both",
        disable_scout=bool(disable_scout),
        seconds=_optional_number(seconds),
        review=bool(review),
    )


def _paths_text(outputs: tuple[RunOutputs, ...] | None) -> str:
    if not outputs:
        return ""
    lines: list[str] = []
    for index, output in enumerate(outputs, start=1):
        label = output.output_dir.name if output.output_dir else f"版本 {index}"
        lines.append(f"[{label}]")
        if output.output_dir:
            lines.append(f"输出目录：{output.output_dir}")
        if output.brief:
            lines.append(f"脚本文件：{output.brief}")
        if output.youtube:
            lines.append(f"YouTube：{output.youtube}")
        if output.reels:
            lines.append(f"Reels：{output.reels}")
    return "\n".join(lines)


def stream_run(
    runner: Runner,
    mode_label: str,
    prompt: str,
    video_type: str,
    disable_scout: bool,
    video_url: str,
    topic: str,
    sample_interval: float | int,
    seconds: float | int | None,
    review: bool,
) -> Iterator[
    tuple[
        str,
        str,
        str | None,
        str | None,
        str | None,
        str | None,
        str,
    ]
]:
    """Run one request and stream display-ready updates to Gradio."""
    request = request_from_form(
        mode_label,
        prompt,
        video_type,
        disable_scout,
        video_url,
        topic,
        sample_interval,
        seconds,
        review,
    )
    logs: list[str] = []
    yield "⏳ 正在生成…", "", None, None, None, None, ""

    for event in runner.run(request):
        if event.kind == "heartbeat":
            yield (
                "⏳ 正在生成…",
                "\n".join(logs),
                None,
                None,
                None,
                None,
                "",
            )
            continue
        if event.kind == "log":
            logs.append(event.message)
            yield "⏳ 正在生成…", "\n".join(logs), None, None, None, None, ""
            continue
        if event.kind == "error":
            yield f"❌ {event.message}", "\n".join(logs), None, None, None, None, ""
            continue

        outputs = event.outputs or ()
        first = outputs[0] if outputs else RunOutputs()
        second = outputs[1] if len(outputs) > 1 else RunOutputs()
        yield (
            f"✅ {event.message}",
            "\n".join(logs),
            str(first.youtube) if first.youtube else None,
            str(first.reels) if first.reels else None,
            str(second.youtube) if second.youtube else None,
            str(second.reels) if second.reels else None,
            _paths_text(outputs),
        )


def create_app(runner: Runner | None = None) -> gr.Blocks:
    """Create the local UI without starting a network server."""
    active_runner = runner or PipelineRunner(REPO_ROOT)

    with gr.Blocks(title="China Video Bot") as app:
        gr.Markdown(
            "# China Video Bot\n"
            "输入一个主题，或者提供参考视频，让流水线在本地生成横版和竖版成片。"
        )
        gr.Markdown(
            "**本地安全模式：** 此页面始终使用 `--dry-run`，不会发布到 YouTube 或 Instagram。",
            elem_classes=["local-note"],
        )

        mode = gr.Radio(
            ["主题生成", "参考视频生成"],
            value="主题生成",
            label="生成方式",
        )

        with gr.Group(visible=True, elem_classes=["mode-panel"]) as topic_group:
            prompt = gr.Textbox(
                label="主题",
                placeholder="例如：Chengdu hot pot for first-time visitors",
                lines=2,
            )
            with gr.Row():
                video_type = gr.Dropdown(
                    ["growth", "info", "both"],
                    value="both",
                    label="内容类型",
                )
                disable_scout = gr.Checkbox(
                    value=True,
                    label="关闭自动 Scout",
                    info="暂时建议开启，避免自动纪录片素材出现语义错配。",
                )

        with gr.Group(visible=False, elem_classes=["mode-panel"]) as video_group:
            video_url = gr.Textbox(
                label="参考视频网址",
                placeholder="Bilibili 或 YouTube 链接",
            )
            topic = gr.Textbox(
                label="主题说明",
                placeholder="例如：Beijing Roast Duck preparation",
            )
            sample_interval = gr.Number(
                value=4,
                minimum=0.5,
                label="采样间隔（秒）",
            )

        with gr.Row():
            seconds = gr.Number(
                value=None,
                minimum=1,
                label="目标时长（秒，可选）",
            )
            review = gr.Checkbox(
                value=False,
                label="先审核脚本",
                info="只生成 brief.json，不继续合成视频。",
            )

        start = gr.Button("开始生成", variant="primary")
        status = gr.Markdown("等待开始")
        logs = gr.Textbox(
            label="运行日志",
            lines=18,
            interactive=False,
            autoscroll=True,
        )

        gr.Markdown("### 版本 1")
        with gr.Row():
            youtube = gr.Video(label="版本 1 · YouTube 横版")
            reels = gr.Video(label="版本 1 · Reels 竖版")
        gr.Markdown("### 版本 2（选择 both 时显示）")
        with gr.Row():
            youtube_second = gr.Video(label="版本 2 · YouTube 横版")
            reels_second = gr.Video(label="版本 2 · Reels 竖版")
        paths = gr.Textbox(label="输出位置", lines=4, interactive=False)

        mode.change(
            lambda value: (
                gr.update(visible=value == "主题生成"),
                gr.update(visible=value == "参考视频生成"),
            ),
            inputs=mode,
            outputs=[topic_group, video_group],
        )

        def run_from_form(*values):
            yield from stream_run(active_runner, *values)

        start.click(
            run_from_form,
            inputs=[
                mode,
                prompt,
                video_type,
                disable_scout,
                video_url,
                topic,
                sample_interval,
                seconds,
                review,
            ],
            outputs=[
                status,
                logs,
                youtube,
                reels,
                youtube_second,
                reels_second,
                paths,
            ],
            concurrency_limit=1,
            concurrency_id="video-generation",
        )

    return app


def main() -> None:
    app = create_app()
    app.queue(max_size=1).launch(
        **LAUNCH_OPTIONS,
        css=CSS,
        allowed_paths=[str((REPO_ROOT / "output").resolve())],
    )


if __name__ == "__main__":
    main()
