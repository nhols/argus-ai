from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from vid_analyser.overlay_schema import ZoneDefinition

ALPHA = 0.05


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


def zone_descriptions(zones: Iterable[ZoneDefinition]) -> str:
    return "\n".join(set(f"{zone.label} (color: {zone.color.name})" for zone in zones))
