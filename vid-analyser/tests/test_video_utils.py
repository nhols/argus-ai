import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.video_utils import probe_video  # noqa: E402


def _stub_probe(monkeypatch, payload: dict) -> list[str]:
    captured_args = []

    def fake_run(args, **kwargs):
        captured_args.extend(args)
        return SimpleNamespace(stdout=json.dumps(payload))

    monkeypatch.setattr("vid_analyser.video_utils.subprocess.run", fake_run)
    return captured_args


def test_probe_video_uses_video_stream_duration(monkeypatch):
    captured_args = _stub_probe(
        monkeypatch,
        {
            "streams": [{"width": 1600, "height": 2300, "duration": "9.934"}],
            "format": {"duration": "10.432"},
        },
    )

    info = probe_video(Path("clip.mp4"))

    assert info.width == 1600
    assert info.height == 2300
    assert info.duration == pytest.approx(9.934)
    assert "stream=width,height,duration:format=duration" in captured_args


def test_probe_video_falls_back_to_container_duration(monkeypatch):
    _stub_probe(
        monkeypatch,
        {
            "streams": [{"width": 1920, "height": 1080}],
            "format": {"duration": "12.5"},
        },
    )

    assert probe_video(Path("clip.mp4")).duration == pytest.approx(12.5)


def test_probe_video_rejects_missing_duration(monkeypatch):
    _stub_probe(
        monkeypatch,
        {
            "streams": [{"width": 1920, "height": 1080, "duration": "N/A"}],
            "format": {},
        },
    )

    with pytest.raises(ValueError, match="Could not determine video duration"):
        probe_video(Path("clip.mp4"))
