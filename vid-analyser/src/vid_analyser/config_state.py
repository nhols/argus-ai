import json

from fastapi import FastAPI

from vid_analyser.pipeline import RunConfig


def apply_config_update(app: FastAPI, *, config: dict, created_at: str, source: str | None) -> dict[str, object]:
    validated_config = RunConfig.from_json_text(json.dumps(config))
    record = app.state.config_repository.insert_config_version(
        config=config,
        created_at=created_at,
        source=source,
    )
    app.state.run_config = validated_config
    app.state.run_config_document = config
    app.state.run_config_version_id = record.id
    return {
        "id": record.id,
        "config": config,
    }
