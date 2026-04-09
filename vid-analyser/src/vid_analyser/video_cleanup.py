import os
import time
from pathlib import Path

LOCAL_VIDEO_RETENTION_DAYS_ENV_VAR = "VID_ANALYSER_LOCAL_VIDEO_RETENTION_DAYS"
SHARED_INPUT_ROOT_ENV_VAR = "VID_ANALYSER_SHARED_INPUT_ROOT"
STORAGE_PROVIDER_ENV_VAR = "VID_ANALYSER_STORAGE_PROVIDER"
STORAGE_ROOT_ENV_VAR = "VID_ANALYSER_STORAGE_ROOT"
DEFAULT_STORAGE_ROOT = "/app/data/storage"


def get_video_retention_days() -> int:
    raw = os.getenv(LOCAL_VIDEO_RETENTION_DAYS_ENV_VAR, "30").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{LOCAL_VIDEO_RETENTION_DAYS_ENV_VAR} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise RuntimeError(f"{LOCAL_VIDEO_RETENTION_DAYS_ENV_VAR} must be >= 0, got {value}")
    return value


def get_video_cleanup_dirs() -> list[Path]:
    directories: list[Path] = []
    seen: set[Path] = set()

    shared_root = os.getenv(SHARED_INPUT_ROOT_ENV_VAR)
    if shared_root:
        _append_unique_path(directories, seen, Path(shared_root))

    if os.getenv(STORAGE_PROVIDER_ENV_VAR, "local").strip().lower() == "local":
        storage_root = os.getenv(STORAGE_ROOT_ENV_VAR, DEFAULT_STORAGE_ROOT)
        _append_unique_path(directories, seen, Path(storage_root) / "videos")

    return directories


def cleanup_old_videos(*, max_age_days: int | None = None, directories: list[Path] | None = None) -> int:
    retention_days = get_video_retention_days() if max_age_days is None else max_age_days
    cleanup_dirs = get_video_cleanup_dirs() if directories is None else directories
    cutoff_timestamp = time.time() - (retention_days * 24 * 60 * 60)

    deleted_files = 0
    for cleanup_dir in cleanup_dirs:
        if not cleanup_dir.exists():
            continue
        for path in cleanup_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.stat().st_mtime >= cutoff_timestamp:
                continue
            path.unlink(missing_ok=True)
            deleted_files += 1
        for directory in sorted((path for path in cleanup_dir.rglob("*") if path.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                continue

    return deleted_files


def _append_unique_path(directories: list[Path], seen: set[Path], path: Path) -> None:
    resolved = path.expanduser().resolve(strict=False)
    if resolved in seen:
        return
    directories.append(resolved)
    seen.add(resolved)
