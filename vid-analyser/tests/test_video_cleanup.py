import os
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.storage.local import LocalStorageProvider
from vid_analyser.video_cleanup import cleanup_old_videos, get_video_cleanup_dirs


def test_cleanup_uses_shared_and_local_storage_dirs(tmp_path, monkeypatch):
    shared_root = tmp_path / "shared"
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VID_ANALYSER_SHARED_INPUT_ROOT", str(shared_root))
    monkeypatch.setenv("VID_ANALYSER_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("VID_ANALYSER_STORAGE_ROOT", str(storage_root))

    shared_root.mkdir(parents=True, exist_ok=True)
    shared_video_path = shared_root / "shared.mp4"
    shared_video_path.write_bytes(b"shared")

    provider = LocalStorageProvider(root=storage_root)
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    provider.store_video(
        execution_id="recent",
        filename="recent.mp4",
        source_path=source_path,
        content_type="video/mp4",
    )
    stored_recent_path = storage_root / "videos" / "recent" / "recent.mp4"

    old_timestamp = time.time() - (90 * 24 * 60 * 60)
    os.utime(shared_video_path, (old_timestamp, old_timestamp))
    os.utime(stored_recent_path, (old_timestamp, old_timestamp))

    cleanup_dirs = get_video_cleanup_dirs()

    assert shared_root.resolve() in cleanup_dirs
    assert (storage_root / "videos").resolve() in cleanup_dirs

    deleted_files = cleanup_old_videos(max_age_days=30)

    assert deleted_files == 2
    assert not shared_video_path.exists()
    assert not stored_recent_path.exists()


def test_store_video_refreshes_mtime(tmp_path):
    storage_root = tmp_path / "storage"
    provider = LocalStorageProvider(root=storage_root)
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")

    old_timestamp = time.time() - (90 * 24 * 60 * 60)
    os.utime(source_path, (old_timestamp, old_timestamp))

    reference = provider.store_video(
        execution_id="recent",
        filename="recent.mp4",
        source_path=source_path,
        content_type="video/mp4",
    )
    stored_recent_path = provider.resolve_path(reference.path)

    assert stored_recent_path.exists()
    assert stored_recent_path.stat().st_mtime > old_timestamp
