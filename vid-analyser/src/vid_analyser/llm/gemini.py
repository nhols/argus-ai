import logging
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from vid_analyser.llm.base import LLMProvider, LlmVideoRequest
from vid_analyser.llm.response_model import AnalyseResponse

DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"
DEFAULT_FPS = 5
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 8.0

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str = DEFAULT_GEMINI_MODEL) -> None:
        load_dotenv()
        self._client = genai.Client()
        self._model = model
        logger.info("Initialized GeminiProvider model=%s", self._model)

    async def analyze_video(self, req: LlmVideoRequest) -> AnalyseResponse:
        video_bytes = Path(req.video_path).read_bytes()
        start = time.perf_counter()
        logger.info(
            "Sending video to Gemini model=%s size_bytes=%s user_prompt_len=%s system_prompt_len=%s",
            self._model,
            len(video_bytes),
            len(req.user_message),
            len(req.system_message),
        )
        response = await self._generate_content_once(req=req, video_bytes=video_bytes)
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info("Received Gemini response model=%s duration_ms=%.2f", self._model, duration_ms)
        return AnalyseResponse.model_validate(response.parsed)

    @retry(
        stop=stop_after_attempt(DEFAULT_MAX_RETRIES + 1),
        wait=wait_exponential(
            multiplier=DEFAULT_INITIAL_BACKOFF_SECONDS,
            min=0.0,
            max=DEFAULT_MAX_BACKOFF_SECONDS,
        ),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _generate_content_once(
        self, *, req: LlmVideoRequest, video_bytes: bytes
    ) -> types.GenerateContentResponse:
        return await self._client.aio.models.generate_content(
            model=self._model,
            contents=types.Content(
                parts=[
                    types.Part(
                        inline_data=types.Blob(data=video_bytes, mime_type="video/mp4"),
                        video_metadata=types.VideoMetadata(fps=DEFAULT_FPS),
                    ),
                    types.Part(text=req.user_message),
                ]
            ),
            config=types.GenerateContentConfig(
                system_instruction=req.system_message,
                response_mime_type="application/json",
                response_json_schema=AnalyseResponse.model_json_schema(),
                media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            ),
        )


if __name__ == "__main__":
    load_dotenv()
