from abc import ABC, abstractmethod
from pathlib import Path


class NotificationService(ABC):
    @abstractmethod
    async def send_video(self, *, chat_id: str, video_path: str | Path, caption: str) -> None:
        """Send a video notification."""
