from pathlib import Path
from uuid import uuid4

from vid_analyser.evals.model import Golden, TestCase
from vid_analyser.evals.store import StoreAbc, hash_video

VIDEOS = "videos"
GOLDEN = "golden"


class LocalStore(StoreAbc):
    def __init__(self, root: str | Path = "_evals") -> None:
        self.root = Path(root)
        self.videos_dir = self.root / VIDEOS
        self.golden_dir = self.root / GOLDEN
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        self.golden_dir.mkdir(parents=True, exist_ok=True)

    def ls_videos(self) -> list[str]:
        return sorted(str(path.relative_to(self.videos_dir)) for path in self.videos_dir.rglob("*") if path.is_file())

    def get_video(self, key: str) -> bytes:
        return (self.videos_dir / key).read_bytes()

    def save_golden_case(self, case: Golden, video: bytes, name: str | None = None) -> None:
        name = name or f"{uuid4()}.mp4"
        if not name.endswith(".mp4"):
            raise ValueError("Name must end with .mp4")

        rel_video_path = Path(name)
        video_path = self.videos_dir / rel_video_path
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(video)

        test_case = TestCase(
            video_path=rel_video_path.as_posix(),
            video_hash=hash_video(video),
            golden=case,
        )

        golden_path = self.golden_dir / rel_video_path.with_suffix(".json")
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(test_case.model_dump_json(indent=2), encoding="utf-8")

    def get_labelled_cases(self) -> list[TestCase]:
        return [
            TestCase.model_validate_json(json_path.read_text(encoding="utf-8"))
            for json_path in sorted(self.golden_dir.rglob("*.json"))
        ]
