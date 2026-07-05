import tempfile
from datetime import datetime
from pathlib import Path

from vid_analyser.agent.analysis.parking_spot import assess_parking_spot, find_zone
from vid_analyser.agent.analysis.scene import analyse_scene
from vid_analyser.agent.models import VidAnalysis
from vid_analyser.overlay import generate_overlay_reference_frame
from vid_analyser.overlay_schema import ZoneDefinition


async def analyse_video(
    video_path: Path,
    content_type: str,
    overlay_zones: list[ZoneDefinition],
    *,
    scene_system_prompt: str | None,
    parking_spot_system_prompt: str | None = None,
    video_start_time: datetime,
) -> VidAnalysis:
    with tempfile.TemporaryDirectory(
        prefix=f"{video_path.stem}-analysis-"
    ) as directory:
        overlay_reference_path = generate_overlay_reference_frame(
            video_path,
            overlay_zones,
            output_dir=Path(directory),
        )
        parking_zone = find_zone(overlay_zones)
        if parking_spot_system_prompt is None:
            parking = await assess_parking_spot(video_path, parking_zone)
        else:
            parking = await assess_parking_spot(
                video_path,
                parking_zone,
                system_prompt=parking_spot_system_prompt,
            )
        scene = await analyse_scene(
            video_path,
            content_type,
            overlay_reference_path,
            overlay_zones,
            parking,
            system_prompt=scene_system_prompt,
            video_start_time=video_start_time,
        )
    return VidAnalysis(
        ir_mode=scene.ir_mode,
        events_description=scene.events_description,
        parking_spot_status=parking.parking_spot_status,
        number_plate=parking.number_plate,
        vehicle_description=parking.vehicle_description,
    )
