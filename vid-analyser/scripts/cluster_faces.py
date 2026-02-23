from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from faces import embed_face_crops, extract_face_crops


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}
CROP_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass(slots=True)
class CropRecord:
    crop_path: Path
    video_path: Path


def iter_videos(root: Path, recursive: bool) -> list[Path]:
    globber = root.rglob if recursive else root.glob
    videos = [p for p in globber("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
    return sorted(videos)


def iter_existing_crops(crop_dir: Path) -> list[Path]:
    if not crop_dir.exists() or not crop_dir.is_dir():
        return []
    return sorted(
        p for p in crop_dir.iterdir() if p.is_file() and p.suffix.lower() in CROP_EXTENSIONS
    )


def cluster_embeddings(
    records: list[CropRecord],
    embeddings: np.ndarray,
    similarity_threshold: float,
) -> list[list[int]]:
    """
    Quick centroid-based clustering using cosine similarity.
    Embeddings are expected to be L2-normalized.
    """
    if len(records) == 0:
        return []

    clusters: list[list[int]] = []
    centroids: list[np.ndarray] = []

    for idx, emb in enumerate(embeddings):
        best_cluster = None
        best_score = -1.0

        for cluster_idx, centroid in enumerate(centroids):
            score = float(np.dot(emb, centroid))
            if score > best_score:
                best_score = score
                best_cluster = cluster_idx

        if best_cluster is None or best_score < similarity_threshold:
            clusters.append([idx])
            centroids.append(emb.copy())
            continue

        clusters[best_cluster].append(idx)
        stack = np.stack([embeddings[i] for i in clusters[best_cluster]], axis=0)
        centroid = stack.mean(axis=0)
        norm = float(np.linalg.norm(centroid)) + 1e-12
        centroids[best_cluster] = (centroid / norm).astype(np.float32)

    return clusters


def copy_cluster_outputs(records: list[CropRecord], clusters: list[list[int]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    for cluster_idx, member_indices in enumerate(sorted(clusters, key=len, reverse=True), start=1):
        cluster_dir = out_dir / f"cluster_{cluster_idx:03d}"
        cluster_dir.mkdir(parents=True, exist_ok=True)

        for local_idx, rec_idx in enumerate(member_indices, start=1):
            rec = records[rec_idx]
            video_tag = rec.video_path.stem.replace(" ", "_")
            dst = cluster_dir / f"{local_idx:04d}_{video_tag}__{rec.crop_path.name}"
            shutil.copy2(rec.crop_path, dst)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Extract face crops from videos, embed them, cluster similar faces, "
            "and write crops into per-cluster folders."
        )
    )
    p.add_argument("videos_dir", type=Path, help="Directory containing videos")
    p.add_argument("output_dir", type=Path, help="Directory to write clustered face crops")
    p.add_argument("--recursive", action="store_true", help="Search videos recursively")
    p.add_argument("--sample-fps", type=int, default=8, help="Frame sampling rate for face extraction")
    p.add_argument("--max-crops-per-video", type=int, default=200, help="Max crops to extract from each video")
    p.add_argument("--conf-thresh", type=float, default=0.6, help="Face detector confidence threshold")
    p.add_argument("--min-face", type=int, default=80, help="Minimum face crop side length")
    p.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.55,
        help="Cosine similarity threshold for assigning a crop to an existing cluster",
    )
    p.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Intermediate crop directory (default: <output_dir>/_crops)",
    )
    p.add_argument(
        "--keep-work-dir",
        action="store_true",
        help="Keep intermediate extracted crops after clustering",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    videos_dir = args.videos_dir
    output_dir = args.output_dir
    work_dir = args.work_dir or (output_dir / "_crops")

    if not videos_dir.exists() or not videos_dir.is_dir():
        raise FileNotFoundError(f"Video directory not found: {videos_dir}")

    videos = iter_videos(videos_dir, recursive=args.recursive)
    if not videos:
        print(f"No videos found in {videos_dir}")
        return 1

    if output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    work_dir.mkdir(parents=True, exist_ok=True)

    records: list[CropRecord] = []
    print(f"Found {len(videos)} video(s). Extracting face crops...")
    for i, video_path in enumerate(videos, start=1):
        video_work_dir = work_dir / f"{i:03d}_{video_path.stem}"
        print(f"[{i}/{len(videos)}] {video_path}")
        existing_crops = iter_existing_crops(video_work_dir)
        if existing_crops:
            crops = existing_crops
            print(f"  reusing {len(crops)} existing crop(s) from {video_work_dir}")
        else:
            crops = extract_face_crops(
                video_path=video_path,
                out_dir=video_work_dir,
                sample_fps=args.sample_fps,
                conf_thresh=args.conf_thresh,
                min_face=args.min_face,
                max_crops=args.max_crops_per_video,
            )
            print(f"  extracted {len(crops)} crop(s)")
        records.extend(CropRecord(crop_path=c, video_path=video_path) for c in crops)

    if not records:
        print("No face crops found or extracted.")
        return 1

    print(f"Embedding {len(records)} crop(s)...")
    crop_paths = [r.crop_path for r in records]
    emb_map = embed_face_crops(crop_paths)

    filtered_records: list[CropRecord] = []
    emb_list: list[np.ndarray] = []
    for rec in records:
        emb = emb_map.get(rec.crop_path)
        if emb is None:
            continue
        filtered_records.append(rec)
        emb_list.append(emb)

    if not emb_list:
        print("No embeddings produced from extracted crops.")
        return 1

    embeddings = np.stack(emb_list, axis=0).astype(np.float32)
    print(
        f"Clustering {len(filtered_records)} embeddings "
        f"(similarity threshold={args.similarity_threshold:.2f})..."
    )
    clusters = cluster_embeddings(filtered_records, embeddings, args.similarity_threshold)

    copy_cluster_outputs(filtered_records, clusters, output_dir)

    print(f"Wrote {len(clusters)} cluster folder(s) to {output_dir}")
    sizes = sorted((len(c) for c in clusters), reverse=True)
    print(f"Cluster sizes: {sizes}")

    if not args.keep_work_dir and work_dir.exists():
        shutil.rmtree(work_dir)
        print(f"Removed intermediate crops: {work_dir}")
    elif args.keep_work_dir:
        print(f"Kept intermediate crops: {work_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
