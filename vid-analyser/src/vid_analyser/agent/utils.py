from datetime import datetime


def get_timestamps(video_start_time: datetime) -> str:
    current_time = datetime.now(video_start_time.tzinfo) if video_start_time.tzinfo else datetime.now()
    return f"The current time is {current_time}, the video was recorded at {video_start_time}"
