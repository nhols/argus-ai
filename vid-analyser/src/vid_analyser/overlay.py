import json
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from vid_analyser.overlay_schema import Color, ZoneDefinition

ALPHA = 0.05
STROKE_WIDTH = 2


def _ffprobe_dimensions(video: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _scale_point(point: tuple[float, float], *, width: int, height: int) -> tuple[int, int]:
    x, y = point
    if max(abs(x), abs(y)) <= 1.0:
        x *= width
        y *= height
    return round(x), round(y)


def _to_svg_rgb(color: Color) -> tuple[int, int, int]:
    blue, green, red = color.value
    return red, green, blue


def _zone_polygon(zone: ZoneDefinition, *, width: int, height: int) -> str | None:
    points = [_scale_point(point, width=width, height=height) for point in zone.polygon]
    if len(points) < 3:
        return None

    rgb = _to_svg_rgb(zone.color)
    points_attr = " ".join(f"{x},{y}" for x, y in points)
    return (
        f'<polygon points="{points_attr}" '
        f'fill="rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {ALPHA})" '
        f'stroke="rgb({rgb[0]}, {rgb[1]}, {rgb[2]})" '
        f'stroke-width="{STROKE_WIDTH}" />'
    )


def _build_svg_overlay(zones: Iterable[ZoneDefinition], *, width: int, height: int) -> str:
    polygons = [
        polygon
        for zone in zones
        if (polygon := _zone_polygon(zone, width=width, height=height)) is not None
    ]
    body = "\n  ".join(polygons)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n  {body}\n</svg>\n'
    )


def generate_overlay_reference_frame(video: Path, zones: Iterable[ZoneDefinition]) -> Path:
    if not video.exists():
        raise FileNotFoundError(video)

    output_path = video.parent / f"{video.stem}_zones.png"
    width, height = _ffprobe_dimensions(video)
    svg_document = _build_svg_overlay(zones, width=width, height=height)

    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as svg_file:
        svg_path = Path(svg_file.name)
        svg_file.write(svg_document)

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(video),
                "-loop",
                "1",
                "-i",
                str(svg_path),
                "-filter_complex",
                "[0:v][1:v]overlay",
                "-frames:v",
                "1",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or f"ffmpeg overlay failed for {video}") from exc
    finally:
        svg_path.unlink(missing_ok=True)

    return output_path


def zone_descriptions(zones: Iterable[ZoneDefinition]) -> str:
    return "\n".join(set(f"{zone.label} (color: {zone.color.name})" for zone in zones))
