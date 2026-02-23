import hashlib
from abc import ABC, abstractmethod
from functools import cached_property

from vid_analyser.evals.model import Golden, TestCase


class StoreAbc(ABC):
    @abstractmethod
    def ls_videos(self) -> list[str]:
        pass

    @abstractmethod
    def get_video(self, key: str) -> bytes:
        pass

    @abstractmethod
    def save_golden_case(self, case: Golden, video: bytes, name: str | None = None) -> None:
        pass

    @abstractmethod
    def get_labelled_cases(self) -> list[TestCase]:
        pass

    @property
    def labelled_hashmap(self) -> dict[str, TestCase]:
        cases = self.get_labelled_cases()
        return {case.video_hash: case for case in cases}

    @cached_property
    def video_hashmap(self) -> dict[str, str]:
        return {hash_video(self.get_video(key)): key for key in self.ls_videos()}


def hash_video(video: bytes) -> str:
    return hashlib.sha256(video).hexdigest()
