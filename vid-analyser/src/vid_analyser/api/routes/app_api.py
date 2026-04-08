from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from vid_analyser.auth import require_ui_basic_auth
from vid_analyser.config_schema import RunConfig
from vid_analyser.api.runtime import set_active_config_state


class ConfigUpdateRequest(BaseModel):
    config: RunConfig
    source: str | None = "api"

router = APIRouter(prefix="/app/api", dependencies=[Depends(require_ui_basic_auth)])


@router.get("/config")
async def get_config(request: Request):
    app = request.app
    if app.state.run_config is None or app.state.run_config_version_id is None:
        raise HTTPException(status_code=404, detail="Config not initialized")
    return {
        "id": app.state.run_config_version_id,
        "config": app.state.run_config.model_dump(mode="json"),
    }


@router.put("/config")
async def update_config(payload: ConfigUpdateRequest, request: Request):
    app = request.app
    try:
        run_config = payload.config
        config_document = run_config.model_dump(mode="json")
        record = await app.state.config_repository.insert(config=config_document, source=payload.source)
        set_active_config_state(
            app,
            run_config=run_config,
            version_id=record.id,
            source=record.source,
        )
        return {"id": record.id, "config": config_document}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config: {exc}") from None
