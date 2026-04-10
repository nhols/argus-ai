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


def _telegram_payload(*, update_id: int = 1, chat_id: int = 3, text: str | None = "hello", is_bot: bool = False):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 2,
            "chat": {"id": chat_id, "type": "private"},
            "from": {
                "id": 4,
                "is_bot": is_bot,
                "first_name": "Neil",
                "username": "neil",
            },
            "text": text,
        },
    }


def test_config_ui_loads_and_updates_config(tmp_path, monkeypatch):
    monkeypatch.setenv("UI_BASIC_AUTH_USER", "admin")
    monkeypatch.setenv("UI_BASIC_AUTH_PASSWORD", "secret")
    monkeypatch.setenv("VID_ANALYSER_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("VID_ANALYSER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("VID_ANALYSER_SQLITE_PATH", str(tmp_path / "vid-analyser.db"))

    from vid_analyser.api.app import app
    from vid_analyser.db import init_database

    initial_config = {
        "overlay": {"zones": []},
        "video_analyser_sys_prompt": "initial analyser prompt",
        "notifier_sys_prompt": "initial notifier prompt",
        "notifier_style": "initial notifier style",
        "telegram_operator_sys_prompt": "initial telegram operator prompt",
        "telegram_chat_id": None,
        "previous_messages_limit": 5,
        "agent_memory_limit": 10,
        "agent_memory_decay_days": 7.0,
        "get_bookings": False,
    }
    updated_config = {
        "overlay": {"zones": []},
        "video_analyser_sys_prompt": "updated analyser prompt",
        "notifier_sys_prompt": "updated notifier prompt",
        "notifier_style": "updated notifier style",
        "telegram_operator_sys_prompt": "updated telegram operator prompt",
        "telegram_chat_id": None,
        "previous_messages_limit": 7,
        "agent_memory_limit": 8,
        "agent_memory_decay_days": 3.0,
        "get_bookings": True,
    }

    async def seed_initial_config():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        await db.insert_config(config=initial_config, source="test-seed")

    asyncio.run(seed_initial_config())

    with TestClient(app) as client:
        headers = _basic_auth_header("admin", "secret")

        response = client.get("/app", headers=headers)
        assert response.status_code == 200
        assert "Config Admin" in response.text

        response = client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/x-icon"

        response = client.put("/app/api/config", headers=headers, json={"config": updated_config, "source": "ui"})
        assert response.status_code == 200
        assert response.json()["config"] == updated_config

        response = client.get("/app/api/config", headers=headers)
        assert response.status_code == 200
        assert response.json()["config"] == updated_config


def test_telegram_webhook_requires_matching_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("VID_ANALYSER_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("VID_ANALYSER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("VID_ANALYSER_SQLITE_PATH", str(tmp_path / "vid-analyser.db"))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_PATH_SECRET", "path-secret")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HEADER_SECRET", "header-secret")

    from vid_analyser.api.app import app
    from vid_analyser.db import init_database

    async def seed_initial_config():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        await db.insert_config(
            config={
                "overlay": {"zones": []},
                "video_analyser_sys_prompt": None,
                "notifier_sys_prompt": None,
                "notifier_style": None,
                "telegram_operator_sys_prompt": None,
                "telegram_chat_id": "3",
                "previous_messages_limit": 5,
                "agent_memory_limit": 10,
                "agent_memory_decay_days": 7.0,
                "get_bookings": False,
            },
            source="test-seed",
        )

    asyncio.run(seed_initial_config())

    with TestClient(app) as client:
        async def fake_run_operator_agent(**kwargs):
            await app.state.db.insert_telegram_chat_message(
                chat_id=kwargs["chat_id"],
                chat_type=kwargs["chat_type"],
                message_id="999",
                direction="outbound",
                text=f"echo: {kwargs['message_text']}",
                sender_display_name="ArgusAI Bot",
            )
            return f"echo: {kwargs['message_text']}"

        monkeypatch.setattr("vid_analyser.api.routes.webhooks.run_telegram_operator_agent", fake_run_operator_agent)

        response = client.post(
            "/webhooks/telegram/path-secret",
            headers={"X-Telegram-Bot-Api-Secret-Token": "header-secret"},
            json=_telegram_payload(),
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert len(asyncio.run(app.state.db.get_recent_telegram_chat_messages(chat_id="3", limit=10))) == 2

        response = client.post(
            "/webhooks/telegram/wrong",
            headers={"X-Telegram-Bot-Api-Secret-Token": "header-secret"},
            json={"update_id": 1},
        )
        assert response.status_code == 401

        response = client.post(
            "/webhooks/telegram/path-secret",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            json={"update_id": 1},
        )
        assert response.status_code == 401


def test_telegram_webhook_ignores_wrong_chat_empty_text_duplicate_and_bots(tmp_path, monkeypatch):
    monkeypatch.setenv("VID_ANALYSER_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("VID_ANALYSER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("VID_ANALYSER_SQLITE_PATH", str(tmp_path / "vid-analyser.db"))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_PATH_SECRET", "path-secret")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_HEADER_SECRET", "header-secret")

    from vid_analyser.api.app import app
    from vid_analyser.db import init_database

    async def seed_initial_config():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        await db.insert_config(
            config={
                "overlay": {"zones": []},
                "video_analyser_sys_prompt": None,
                "notifier_sys_prompt": None,
                "notifier_style": "initial style",
                "telegram_operator_sys_prompt": "operator style",
                "telegram_chat_id": "3",
                "previous_messages_limit": 5,
                "agent_memory_limit": 10,
                "agent_memory_decay_days": 7.0,
                "get_bookings": False,
            },
            source="test-seed",
        )

    asyncio.run(seed_initial_config())

    async def fake_run_operator_agent(**kwargs):
        await app.state.db.insert_agent_memory(
            agent_name="global",
            memory_text=f"Last sender: {kwargs['sender_display_name']}",
        )
        await app.state.db.insert_telegram_chat_message(
            chat_id=kwargs["chat_id"],
            chat_type=kwargs["chat_type"],
            message_id="500",
            direction="outbound",
            text="Handled",
            sender_display_name="ArgusAI Bot",
        )
        updated_config = app.state.run_config.model_copy(update={"notifier_style": "updated by operator"})
        record = asyncio.create_task(app.state.db.insert_config(
            config=updated_config.model_dump(mode="json"),
            source="telegram-operator",
        ))
        inserted = await record
        from vid_analyser.api.runtime import set_active_config_state
        set_active_config_state(app, run_config=updated_config, version_id=inserted.id, source=inserted.source)
        return "Handled"

    monkeypatch.setattr("vid_analyser.api.routes.webhooks.run_telegram_operator_agent", fake_run_operator_agent)

    with TestClient(app) as client:
        headers = {"X-Telegram-Bot-Api-Secret-Token": "header-secret"}

        response = client.post("/webhooks/telegram/path-secret", headers=headers, json=_telegram_payload(chat_id=999))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}

        response = client.post("/webhooks/telegram/path-secret", headers=headers, json=_telegram_payload(text="   "))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}

        response = client.post("/webhooks/telegram/path-secret", headers=headers, json=_telegram_payload(is_bot=True))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}

        response = client.post("/webhooks/telegram/path-secret", headers=headers, json=_telegram_payload(update_id=10, text="update style"))
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        response = client.post("/webhooks/telegram/path-secret", headers=headers, json=_telegram_payload(update_id=10, text="update style"))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}

        recent = asyncio.run(app.state.db.get_recent_telegram_chat_messages(chat_id="3", limit=10))
        assert len(recent) == 2
        assert recent[0].direction == "outbound"
        assert recent[1].direction == "inbound"
        assert recent[1].sender_username == "neil"

        memories = asyncio.run(
            app.state.db.get_ranked_agent_memories(agent_name="global", limit=1, decay_days=7.0)
        )
        assert len(memories) == 1
        assert memories[0].memory_text == "Last sender: Neil"

        assert app.state.run_config.notifier_style == "updated by operator"
