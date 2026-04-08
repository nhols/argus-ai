from vid_analyser.api.routes.app_api import router as app_api_router
from vid_analyser.api.routes.internal import router as internal_router
from vid_analyser.api.routes.webhooks import router as webhook_router

__all__ = ["app_api_router", "internal_router", "webhook_router"]
