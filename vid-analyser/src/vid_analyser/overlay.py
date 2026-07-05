import tempfile
from pathlib import Path
from typing import Iterable

from vid_analyser.overlay_schema import Color, ZoneDefinition
from vid_analyser.video_utils import overlay_first_frame, probe_video

ALPHA = 0.05
STROKE_WIDTH = 4


def _scale_point(
    point: tuple[float, float], *, width: int, height: int
) -> tuple[int, int]:
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


def _build_svg_overlay(
    zones: Iterable[ZoneDefinition], *, width: int, height: int
) -> str:
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


def generate_overlay_reference_frame(
    video: Path,
    zones: Iterable[ZoneDefinition],
    *,
    output_dir: Path | None = None,
) -> Path:
    if not video.exists():
        raise FileNotFoundError(video)

    output_path = (output_dir or video.parent) / f"{video.stem}_zones.png"
    info = probe_video(video)
    svg_document = _build_svg_overlay(zones, width=info.width, height=info.height)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".svg", delete=False, encoding="utf-8"
    ) as svg_file:
        svg_path = Path(svg_file.name)
        svg_file.write(svg_document)

    try:
        overlay_first_frame(video, svg_path, output_path)
    finally:
        svg_path.unlink(missing_ok=True)

    return output_path


def zone_descriptions(zones: Iterable[ZoneDefinition]) -> str:
    return "\n".join(set(f"{zone.label} (color: {zone.color.name})" for zone in zones))
