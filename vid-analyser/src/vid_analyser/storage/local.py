import shutil
import os
from pathlib import Path

from vid_analyser.storage.base import StorageProvider, VideoReference


class LocalStorageProvider(StorageProvider):
    def __init__(self, *, root: str | Path) -> None:
        self._root = Path(root)

    def store_video(
        self,
        *,
        execution_id: str,
        filename: str | None,
        source_path: str | Path,
        content_type: str | None,
    ) -> VideoReference:
        del content_type
        relative_path = _build_video_path(execution_id=execution_id, filename=filename)
        destination_path = self._root / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        os.utime(destination_path, None)
        return VideoReference(provider="local", path=str(relative_path))

    def resolve_path(self, relative_path: str | Path) -> Path:
        return self._root / relative_path


def _build_video_path(*, execution_id: str, filename: str | None) -> Path:
    safe_filename = filename or "video.mp4"
    return Path("videos") / execution_id / safe_filename
