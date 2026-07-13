import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: float


def _positive_float(value: object) -> float | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def probe_video(path: Path) -> VideoInfo:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    duration = _positive_float(stream.get("duration"))
    if duration is None:
        duration = _positive_float(payload.get("format", {}).get("duration"))
    if duration is None:
        raise ValueError(f"Could not determine video duration for {path}")
    return VideoInfo(
        width=int(stream["width"]),
        height=int(stream["height"]),
        duration=duration,
    )


def _run_ffmpeg(args: list[str], error_message: str) -> None:
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or error_message)


def extract_frame(
    video: Path,
    output: Path,
    *,
    timestamp: float,
    video_filter: str | None = None,
    quality: int = 2,
) -> None:
    args = ["-ss", f"{timestamp:.6f}", "-i", str(video), "-frames:v", "1"]
    if video_filter:
        args.extend(["-vf", video_filter])
    args.extend(["-q:v", str(quality), str(output)])
    _run_ffmpeg(args, f"Could not extract frame from {video}")
    if not output.exists():
        raise RuntimeError(f"Could not extract frame from {video}")


def overlay_first_frame(video: Path, overlay: Path, output: Path) -> None:
    _run_ffmpeg(
        [
            "-i",
            str(video),
            "-loop",
            "1",
            "-i",
            str(overlay),
            "-filter_complex",
            "[0:v][1:v]overlay",
            "-frames:v",
            "1",
            str(output),
        ],
        f"Could not overlay frame from {video}",
    )
    if not output.exists():
        raise RuntimeError(f"Could not overlay frame from {video}")
