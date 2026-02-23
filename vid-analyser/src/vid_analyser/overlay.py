from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Self

import cv2
import numpy as np
from pydantic import BaseModel, field_validator

ALPHA = 0.05


class Color(Enum):
    RED = (0, 0, 255)
    BLUE = (255, 0, 0)
    GREEN = (0, 180, 0)
    YELLOW = (0, 220, 220)
    WHITE = (255, 255, 255)
    ORANGE = (0, 165, 255)

    @classmethod
    def from_string(cls, s: str) -> Self:
        try:
            return cls[s.upper()]
        except KeyError:
            raise ValueError(f"Invalid color name: {s}")


class ZoneDefinition(BaseModel):
    label: str
    color: Color = Color.RED
    polygon: list[tuple[float, float]]

    @field_validator("color", mode="before")
    def colour_from_string(cls, color_name: Any) -> Any:
        if isinstance(color_name, str):
            return Color.from_string(color_name)
        return color_name


def overlay_zones(video: Path, zones: Iterable[ZoneDefinition]) -> Path:
    if not video.exists():
        raise FileNotFoundError(video)

    output_path = video.parent / f"{video.stem}_zones{video.suffix}"

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore
    writer = cv2.VideoWriter(
        str(output_path),
        fourcc,
        fps,
        (width, height),
    )
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"Could not open video writer for: {output_path}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        overlay = frame.copy()

        for zone in zones:
            raw_pts = np.array(zone.polygon, dtype=np.float32)
            # Accept either normalized coordinates (0..1) or absolute pixel points.
            if raw_pts.size == 0:
                continue
            if float(raw_pts.max()) <= 1.0:
                raw_pts[:, 0] *= width
                raw_pts[:, 1] *= height
            pts = np.round(raw_pts).astype(np.int32).reshape((-1, 1, 2))

            cv2.fillPoly(overlay, [pts], zone.color.value)
            cv2.polylines(frame, [pts], isClosed=True, color=zone.color.value, thickness=2)

        frame = cv2.addWeighted(overlay, ALPHA, frame, 1 - ALPHA, 0)

        writer.write(frame)

    cap.release()
    writer.release()

    return output_path


if __name__ == "__main__":
    zones = zones = [
    ZoneDefinition(
        label="Parking Spot",
        color=Color.RED,
        polygon=[
            (0.6325, 0.4648),
            (0.9231, 0.4157),
            (0.7556, 0.3435),
            (0.5900, 0.3543),
            (0.6325, 0.4639),
        ],
    ),
    ZoneDefinition(
        label="Footpath",
        color=Color.BLUE,
        polygon=[
            (0.0019, 0.3909),
            (0.0638, 0.4230),
            (0.3531, 0.4709),
            (0.6344, 0.4709),
            (0.9444, 0.4157),
            (0.9988, 0.4030),
            (0.9988, 0.5217),
            (0.0825, 0.5222),
            (0.0000, 0.4800),
            (0.0031, 0.3917),
        ],
    ),
    ZoneDefinition(
        label="Footpath",
        color=Color.BLUE,
        polygon=[
            (0.0000, 0.6513),
            (0.9969, 0.6422),
            (0.9981, 0.7800),
            (0.0019, 0.7774),
            (0.0019, 0.6509),
        ],
    ),
    ZoneDefinition(
        label="Doorstep",
        color=Color.GREEN,
        polygon=[
            (0.5294, 0.9970),
            (0.5406, 0.7787),
            (0.2637, 0.7787),
            (0.1606, 0.9743),
            (0.5281, 0.9978),
        ],
    ),
    ZoneDefinition(
        label="Mews",
        color=Color.YELLOW,
        polygon=[
            (0.3563, 0.3300),
            (0.5913, 0.3535),
            (0.7731, 0.3409),
            (0.9981, 0.2939),
            (0.9988, 0.1613),
            (0.3762, 0.2000),
            (0.3563, 0.3300),
        ],
    ),
]

    output = overlay_zones(Path("/home/neil/repos/eufy-client/local_files/20260204073042.mp4"), zones)
    print(f"Annotated video saved to: {output}")
