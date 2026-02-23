
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

# Put these two files in ./models/ (see earlier message for download links)
PROTO = Path("models/deploy.prototxt")
WEIGHTS = Path("models/res10_300x300_ssd_iter_140000.caffemodel")
EMBEDDER_ONNX = Path("models/w600k_mbf.onnx")

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

def embed_face_crops(crop_paths: list[Path]) -> dict[Path, np.ndarray]:
    if not EMBEDDER_ONNX.exists():
        raise FileNotFoundError(
            f"Missing embedder model: {EMBEDDER_ONNX}\n"
            "Put your MobileFaceNet ONNX file at models/mobilefacenet.onnx"
        )

    # CPU-only ONNXRuntime session
    session = ort.InferenceSession(str(EMBEDDER_ONNX), providers=["CPUExecutionProvider"])
    input_meta = session.get_inputs()[0]
    input_name = input_meta.name

    # Try to infer expected input size (usually 112x112)
    # Many exports are (1,3,112,112) but sometimes dynamic.
    shape = input_meta.shape  # e.g. [1, 3, 112, 112] or [None, 3, 112, 112]
    try:
        in_h = int(shape[2])
        in_w = int(shape[3])
    except Exception:
        in_h, in_w = 112, 112

    out: dict[Path, np.ndarray] = {}

    for p in crop_paths:
        p = Path(p)
        img = cv2.imread(str(p))
        if img is None:
            continue  # skip unreadable images

        # Preprocess: BGR -> RGB, resize, float32, normalize.
        # Common MobileFaceNet normalization is to roughly [-1, 1].
        img = cv2.resize(img, (in_w, in_h), interpolation=cv2.INTER_AREA)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)
        img = (img - 127.5) / 128.0

        x = np.transpose(img, (2, 0, 1))[None, ...]  # (1, 3, H, W)

        # Run inference
        y = session.run(None, {input_name: x})[0]
        emb = np.asarray(y).reshape(-1).astype(np.float32)

        # L2 normalize so dot-product == cosine similarity
        norm = float(np.linalg.norm(emb)) + 1e-12
        emb = emb / norm

        out[p] = emb

    return out
