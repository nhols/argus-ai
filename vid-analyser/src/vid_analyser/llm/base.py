from typing import Any, Protocol

from pydantic import BaseModel
from vid_analyser.llm.response_model import AnalyseResponse

class LlmVideoRequest(BaseModel):
    video_path: str
    llm_kwargs: dict[str,Any] = {}

class LLMProvider(Protocol):
    name: str

    async def analyze_video(self, req: LlmVideoRequest) -> AnalyseResponse:
        ...