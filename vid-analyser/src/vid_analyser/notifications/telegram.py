from pathlib import Path

from vid_analyser.notifications.base import NotificationService


class TelegramNotificationService(NotificationService):
    def __init__(self, token: str) -> None:
        from telegram import Bot

        self._bot = Bot(token=token)

    async def send_video(self, *, chat_id: str, video_path: str | Path, caption: str) -> None:
        path = Path(video_path)
        with path.open("rb") as video_file:
            await self._bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=caption,
            )
