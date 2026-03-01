from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from vid_analyser.llm.base import LlmVideoRequest
from vid_analyser.llm.response_model import AnalyseResponse
from vid_analyser.overlay import ZoneDefinition
from vid_analyser.person_id.identify import PersonId
from vid_analyser.pipeline.run import OverlayConfig, PersonIdConfig, RunConfig, run


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.last_request: LlmVideoRequest | None = None

    async def analyze_video(self, req: LlmVideoRequest) -> AnalyseResponse:
        self.last_request = req
        return AnalyseResponse(
            ir_mode="unknown",
            parking_spot_status="unknown",
            number_plate=None,
            events_description="none",
            send_notification=False,
        )


class RunPipelineTests(IsolatedAsyncioTestCase):
    async def test_no_optional_modules(self) -> None:
        provider = FakeProvider()
        config = RunConfig(provider=provider, overlay=None, person_id=None)

        response = await run(
            video_path="input.mp4",
            user_prompt="user prompt",
            system_prompt="system prompt",
            config=config,
        )

        self.assertEqual(response.events_description, "none")
        assert provider.last_request is not None
        self.assertEqual(provider.last_request.video_path, "input.mp4")
        self.assertEqual(provider.last_request.system_message, "system prompt")

    async def test_overlay_uses_modified_video_and_prompt(self) -> None:
        provider = FakeProvider()
        zones = [ZoneDefinition(label="Driveway", polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])]
        config = RunConfig(
            provider=provider,
            overlay=OverlayConfig(zones=zones),
            person_id=None,
        )

        with (
            patch("vid_analyser.pipeline.run.overlay_zones", return_value=Path("input_zones.mp4")),
            patch("vid_analyser.pipeline.run.zone_descriptions", return_value="Driveway (color: RED)"),
        ):
            await run(
                video_path="input.mp4",
                user_prompt="user prompt",
                system_prompt="system prompt",
                config=config,
            )

        assert provider.last_request is not None
        self.assertEqual(provider.last_request.video_path, "input_zones.mp4")
        self.assertIn("Additional context:", provider.last_request.system_message)
        self.assertIn("Overlay: Driveway (color: RED)", provider.last_request.system_message)

    async def test_person_id_failure_does_not_crash(self) -> None:
        provider = FakeProvider()
        config = RunConfig(
            provider=provider,
            overlay=None,
            person_id=PersonIdConfig(),
        )

        with patch("vid_analyser.pipeline.run.identify_people", side_effect=RuntimeError("not ready")):
            response = await run(
                video_path="input.mp4",
                user_prompt="user prompt",
                system_prompt="system prompt",
                config=config,
            )

        self.assertEqual(response.events_description, "none")
        assert provider.last_request is not None
        self.assertEqual(provider.last_request.system_message, "system prompt")

    async def test_overlay_and_person_id_are_both_enriched(self) -> None:
        provider = FakeProvider()
        zones = [ZoneDefinition(label="Footpath", polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])]
        config = RunConfig(
            provider=provider,
            overlay=OverlayConfig(zones=zones),
            person_id=PersonIdConfig(),
        )

        with (
            patch("vid_analyser.pipeline.run.overlay_zones", return_value=Path("input_zones.mp4")),
            patch("vid_analyser.pipeline.run.zone_descriptions", return_value="Footpath (color: RED)"),
            patch(
                "vid_analyser.pipeline.run.identify_people",
                return_value=[PersonId(person="Alice", confidence=0.91)],
            ),
        ):
            await run(
                video_path="input.mp4",
                user_prompt="user prompt",
                system_prompt="system prompt",
                config=config,
            )

        assert provider.last_request is not None
        self.assertEqual(provider.last_request.video_path, "input_zones.mp4")
        self.assertIn("Overlay: Footpath (color: RED)", provider.last_request.system_message)
        self.assertIn("Person IDs: Alice (0.91)", provider.last_request.system_message)
