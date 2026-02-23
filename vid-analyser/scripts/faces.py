from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


# Put these two files in ./models/ (see earlier message for download links)
PROTO = Path("models/deploy.prototxt")
WEIGHTS = Path("models/res10_300x300_ssd_iter_140000.caffemodel")


def extract_face_crops(
    video_path: Path,
    out_dir: Path,
    sample_fps: int = 15,
    max_width: int = 640,
    conf_thresh: float = 0.6,
    min_face: int = 80,
    max_crops: int = 100,
) -> list[Path]:
    """
    Extract face crops from a video and save them as JPEGs.

    Returns a list of paths to the saved crops.
    """
    video_path = Path(video_path)
    out_dir = Path(out_dir)

    if not video_path.exists():
        raise FileNotFoundError(video_path)
    if not PROTO.exists():
        raise FileNotFoundError(PROTO)
    if not WEIGHTS.exists():
        raise FileNotFoundError(WEIGHTS)

    out_dir.mkdir(parents=True, exist_ok=True)

    net = cv2.dnn.readNetFromCaffe(str(PROTO), str(WEIGHTS))
    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(fps / sample_fps)))

    saved: list[Path] = []
    frame_idx = 0
    crop_idx = 0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if frame_idx % step != 0:
                frame_idx += 1
                continue

            # Resize for speed
            h, w = frame.shape[:2]
            if w > max_width:
                scale = max_width / float(w)
                frame = cv2.resize(frame, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)

            H, W = frame.shape[:2]

            # OpenCV DNN face detector expects a 300x300 blob
            blob = cv2.dnn.blobFromImage(
                frame, 1.0, (300, 300),
                (104.0, 177.0, 123.0),
                swapRB=False, crop=False
            )
            net.setInput(blob)
            det = net.forward()  # shape [1,1,N,7]

            # Extract boxes
            boxes = []
            for i in range(det.shape[2]):
                conf = float(det[0, 0, i, 2])
                if conf < conf_thresh:
                    continue
                x1, y1, x2, y2 = (det[0, 0, i, 3:7] * np.array([W, H, W, H])).astype(int).tolist()
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(W - 1, x2), min(H - 1, y2)
                if x2 <= x1 or y2 <= y1:
                    continue
                boxes.append((x1, y1, x2, y2, conf))

            # Biggest faces first
            boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)

            for (x1, y1, x2, y2, conf) in boxes:
                if min(x2 - x1, y2 - y1) < min_face:
                    continue

                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    continue

                out_path = out_dir / f"face_{crop_idx:04d}_frame{frame_idx:06d}_c{conf:.2f}.jpg"
                cv2.imwrite(str(out_path), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                saved.append(out_path)
                crop_idx += 1

                if len(saved) >= max_crops:
                    return saved

            frame_idx += 1

    finally:
        cap.release()

    return saved


if __name__ == "__main__":
    kt = "/home/neil/repos/eufy-client/local_files/20260204073042.mp4"
    neno="/home/neil/repos/eufy-client/local_files/20260206203129.mp4"
    crops = extract_face_crops(
        video_path=Path(neno),
        out_dir=Path("face_crops"),
        sample_fps=15,
        max_width=640,
        conf_thresh=0.2,
        min_face=80,
        max_crops=10,
    )
    print(f"Saved {len(crops)} face crops")