import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SkipValidation

from vid_analyser.llm.base import LLMProvider, LlmVideoRequest
from vid_analyser.llm.gemini import GeminiProvider
from vid_analyser.llm.response_model import AnalyseResponse
from vid_analyser.overlay import ZoneDefinition, overlay_zones, zone_descriptions
from vid_analyser.person_id.identify import identify_people

logger = logging.getLogger(__name__)


class OverlayConfig(BaseModel):
    zones: list[ZoneDefinition] = Field(default_factory=list)


class PersonIdConfig(BaseModel):
    # TODO: add person-ID specific options when implementation is complete.
    pass


class ProviderConfig(BaseModel):
    kind: str = "gemini"
    model: str


class _RunConfigInput(BaseModel):
    provider: ProviderConfig
    overlay_zones: list[ZoneDefinition] = Field(default_factory=list)
    enable_person_id: bool = False

    def to_run_config(self) -> "RunConfig":
        if self.provider.kind != "gemini":
            raise ValueError(f"Unsupported analysis provider: {self.provider.kind}")
        overlay = OverlayConfig(zones=self.overlay_zones) if self.overlay_zones else None
        person_id = PersonIdConfig() if self.enable_person_id else None
        return RunConfig(
            provider=GeminiProvider(model=self.provider.model),
            overlay=overlay,
            person_id=person_id,
        )


class RunConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: SkipValidation[LLMProvider]
    overlay: OverlayConfig | None = None
    person_id: PersonIdConfig | None = None

    @classmethod
    def from_json_path(cls, path: str | Path) -> "RunConfig":
        raw_json = Path(path).read_text(encoding="utf-8")
        raw = _RunConfigInput.model_validate_json(raw_json)
        return raw.to_run_config()


def _build_enriched_system_prompt(
    system_prompt: str,
    overlay_summary: str | None,
    person_id_summary: str | None,
) -> str:
    context_lines: list[str] = []
    if overlay_summary:
        context_lines.append(f"Overlay: {overlay_summary}")
    if person_id_summary:
        context_lines.append(f"Person IDs: {person_id_summary}")
    if not context_lines:
        return system_prompt

    return f"{system_prompt}\n\nAdditional context:\n" + "\n".join(context_lines)


def _format_people_summary(people: list) -> str | None:
    if not people:
        return None
    return ", ".join(f"{person.person} ({person.confidence:.2f})" for person in people)


async def run(
    video_path: str | Path,
    user_prompt: str,
    system_prompt: str,
    config: RunConfig,
) -> AnalyseResponse:
    original_video_path = Path(video_path)
    effective_video_path = original_video_path
    overlay_summary: str | None = None
    person_id_summary: str | None = None
    cleanup_paths: list[Path] = []

    logger.info(
        "Pipeline run started video_path=%s overlay_enabled=%s person_id_enabled=%s",
        effective_video_path,
        bool(config.overlay is not None and config.overlay.zones),
        config.person_id is not None,
    )

    if config.overlay is not None and config.overlay.zones:
        logger.info("Applying overlay zones count=%s", len(config.overlay.zones))
        effective_video_path = overlay_zones(effective_video_path, config.overlay.zones)
        if effective_video_path != original_video_path:
            cleanup_paths.append(effective_video_path)
        overlay_summary = zone_descriptions(config.overlay.zones)

    if config.person_id is not None:
        try:
            logger.info("Running person identification")
            people = identify_people(effective_video_path)
            person_id_summary = _format_people_summary(people)
        except Exception as exc:
            logger.warning("Person ID failed, continuing without it: %s", exc)

    enriched_system_prompt = _build_enriched_system_prompt(
        system_prompt=system_prompt,
        overlay_summary=overlay_summary,
        person_id_summary=person_id_summary,
    )

    request = LlmVideoRequest(
        video_path=str(effective_video_path),
        user_message=user_prompt,
        system_message=enriched_system_prompt,
    )
    logger.info("Dispatching pipeline request to provider=%s", config.provider.name)
    try:
        return await config.provider.analyze_video(request)
    finally:
        for cleanup_path in cleanup_paths:
            cleanup_path.unlink(missing_ok=True)
            logger.info("Deleted derived pipeline artifact %s", cleanup_path)
