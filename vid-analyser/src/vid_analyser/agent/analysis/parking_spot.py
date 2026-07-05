import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pydantic_ai import Agent, BinaryContent
from vid_analyser.agent.models import DEFAULT_GOOGLE_MODEL, ParkingSpotAssessment
from vid_analyser.agent.retry import create_google_retry_model
from vid_analyser.overlay_schema import ZoneDefinition
from vid_analyser.video_utils import extract_frame, probe_video

DEFAULT_SNAPSHOT_COUNT = 5
DEFAULT_ZONE_LABEL = "User's parking spot"
PROMPT = """
You are a parking-space inspection specialist. You have one job: assess the state of the single parking spot
marked by the red rectangle, and read the number plate of a vehicle occupying that spot when it is legible.

The images are time-ordered snapshots from one short security-camera video. The red rectangle marks the same
target parking spot in every image. Ignore vehicles in neighbouring spaces, even when they are prominent or
close to the rectangle.

Use these rules:
- `occupied`: a vehicle occupies the marked spot at the end of the sequence.
- `vacant`: the marked spot is empty throughout and at the end of the sequence.
- `car entering`: a vehicle is visibly moving into the marked spot.
- `car leaving`: a vehicle is visibly moving out of the marked spot.
- `unknown`: the marked area is too obscured or ambiguous to decide.
- Return a number plate only for the vehicle in the marked spot and only when it is clearly readable.
- Do not borrow a plate from a vehicle in a neighbouring spot.
- Describe the vehicle occupying, entering, or leaving the marked spot when relevant. Include its apparent
  colour, type, make, or model only to the level supported by the images. Prefer a cautious description such
  as "a large dark SUV" over an uncertain specific make or model. Do not guess. Return null when there is no
  relevant vehicle or no useful visual detail.
- If the images use IR/night vision, do not comment on the vehicle's colour.
- If snapshots show motion, use their order to distinguish entering from leaving.
"""

parking_spot_agent = Agent[None, ParkingSpotAssessment](
    model=create_google_retry_model(DEFAULT_GOOGLE_MODEL),
    name="parking_spot_agent",
    output_type=ParkingSpotAssessment,
    instructions=PROMPT,
)


def find_zone(
    zones: list[ZoneDefinition], label: str = DEFAULT_ZONE_LABEL
) -> ZoneDefinition:
    matches = [zone for zone in zones if zone.label.casefold() == label.casefold()]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one overlay zone labelled {label!r}; found {len(matches)}."
        )
    return matches[0]


def snapshot_timestamps(
    duration: float, count: int = DEFAULT_SNAPSHOT_COUNT
) -> list[float]:
    if duration <= 0:
        raise ValueError("Video duration must be positive.")
    if count < 2:
        raise ValueError("At least two snapshots are required to infer movement.")
    margin = min(0.1, duration * 0.01)
    usable_duration = max(0.0, duration - (2 * margin))
    return [margin + usable_duration * index / (count - 1) for index in range(count)]


def _scale(value: float, size: int) -> float:
    return value * size if abs(value) <= 1.0 else value


def zone_bounding_box(
    zone: ZoneDefinition, width: int, height: int, padding: int = 6
) -> tuple[int, int, int, int]:
    if not zone.polygon:
        raise ValueError(f"Overlay zone {zone.label!r} has no polygon points.")
    xs = [_scale(point[0], width) for point in zone.polygon]
    ys = [_scale(point[1], height) for point in zone.polygon]
    left = max(0, round(min(xs)) - padding)
    top = max(0, round(min(ys)) - padding)
    right = min(width, round(max(xs)) + padding)
    bottom = min(height, round(max(ys)) + padding)
    return left, top, max(1, right - left), max(1, bottom - top)


def _extract_snapshot(
    video_path: Path,
    output_path: Path,
    timestamp: float,
    box: tuple[int, int, int, int],
) -> None:
    x, y, width, height = box
    extract_frame(
        video_path,
        output_path,
        timestamp=timestamp,
        video_filter=f"drawbox=x={x}:y={y}:w={width}:h={height}:color=red@1:t=6",
    )


@contextmanager
def boxed_snapshots(
    video_path: Path,
    zone: ZoneDefinition,
    count: int = DEFAULT_SNAPSHOT_COUNT,
) -> Iterator[list[Path]]:
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    info = probe_video(video_path)
    box = zone_bounding_box(zone, info.width, info.height)
    with tempfile.TemporaryDirectory(
        prefix=f"{video_path.stem}-spot-snapshots-"
    ) as directory:
        paths = []
        for index, timestamp in enumerate(
            snapshot_timestamps(info.duration, count), start=1
        ):
            output_path = Path(directory) / f"snapshot-{index:02d}.jpg"
            _extract_snapshot(video_path, output_path, timestamp, box)
            paths.append(output_path)
        yield paths


async def assess_parking_spot(
    video_path: Path,
    zone: ZoneDefinition,
    *,
    count: int = DEFAULT_SNAPSHOT_COUNT,
    model_name: str = DEFAULT_GOOGLE_MODEL,
) -> ParkingSpotAssessment:
    with boxed_snapshots(video_path, zone, count) as paths:
        prompt: list[str | BinaryContent] = [
            f"Assess the marked parking spot using these {len(paths)} time-ordered snapshots."
        ]
        for index, path in enumerate(paths, start=1):
            identifier = f"snapshot_{index:02d}"
            prompt.extend(
                [
                    f"Snapshot {index} of {len(paths)} ({identifier}):",
                    BinaryContent(
                        path.read_bytes(),
                        media_type="image/jpeg",
                        identifier=identifier,
                    ),
                ]
            )
        if model_name == DEFAULT_GOOGLE_MODEL:
            result = await parking_spot_agent.run(prompt)
        else:
            result = await parking_spot_agent.run(
                prompt,
                model=create_google_retry_model(model_name),
            )
        return result.output
