from __future__ import annotations

import json
from pathlib import Path

from launcher.core import RESULT_MARKER
from scripts.run import _emit_result_marker


def test_emit_result_marker_serializes_nested_paths(capsys) -> None:
    _emit_result_marker(
        {
            "growth": Path("output/demo_growth/youtube.mp4"),
            "info": "output/demo_info/brief.json",
        }
    )

    line = capsys.readouterr().out.strip()
    assert line.startswith(RESULT_MARKER)
    assert json.loads(line[len(RESULT_MARKER) :]) == {
        "growth": "output/demo_growth/youtube.mp4",
        "info": "output/demo_info/brief.json",
    }
