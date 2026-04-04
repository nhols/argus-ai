import asyncio
import base64
import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_config_ui_loads_and_updates_config(tmp_path, monkeypatch):
    monkeypatch.setenv("UI_BASIC_AUTH_USER", "admin")
    monkeypatch.setenv("UI_BASIC_AUTH_PASSWORD", "secret")
    monkeypatch.setenv("VID_ANALYSER_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("VID_ANALYSER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("VID_ANALYSER_SQLITE_PATH", str(tmp_path / "vid-analyser.db"))

    from vid_analyser.api.app import app
    from vid_analyser.db import ConfigUpdateRepository, init_database

    initial_config = {
        "overlay": {"zones": []},
        "video_analyser_sys_prompt": "initial analyser prompt",
        "notifier_sys_prompt": "initial notifier prompt",
        "notifier_style": "initial notifier style",
        "telegram_chat_id": None,
        "previous_messages_limit": 5,
        "get_bookings": False,
    }
    updated_config = {
        "overlay": {"zones": []},
        "video_analyser_sys_prompt": "updated analyser prompt",
        "notifier_sys_prompt": "updated notifier prompt",
        "notifier_style": "updated notifier style",
        "telegram_chat_id": None,
        "previous_messages_limit": 7,
        "get_bookings": True,
    }

    async def seed_initial_config():
        session_factory = await init_database(str(tmp_path / "vid-analyser.db"))
        repository = ConfigUpdateRepository(session_factory)
        await repository.insert(config=initial_config, source="test-seed")

    asyncio.run(seed_initial_config())

    with TestClient(app) as client:
        headers = _basic_auth_header("admin", "secret")

        response = client.get("/ui", headers=headers)
        assert response.status_code == 200
        assert "Config Admin" in response.text

        response = client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/x-icon"

        response = client.put("/config", headers=headers, json={"config": updated_config, "source": "ui"})
        assert response.status_code == 200
        assert response.json()["config"] == updated_config

        response = client.get("/config", headers=headers)
        assert response.status_code == 200
        assert response.json()["config"] == updated_config
