import os
from pathlib import Path

from telegram import Bot
from vid_analyser.notifications.base import NotificationService

TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"


class TelegramNotificationService(NotificationService):
    def __init__(self, token: str | None = None) -> None:
        token = token or os.getenv(TOKEN_ENV_VAR)
        if token is None:
            raise RuntimeError("Telegram bot token is not configured")
        self._bot = Bot(token=token)

    async def send_video(self, *, chat_id: str, video_path: str | Path, caption: str) -> None:
        path = Path(video_path)
        with path.open("rb") as video_file:
            await self._bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=caption,
            )
