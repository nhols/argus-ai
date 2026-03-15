import importlib
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from pytest import MonkeyPatch, raises

from vid_analyser.llm.response_model import AnalyseResponse
from vid_analyser.pipeline import RunConfig

api_module = importlib.import_module("vid_analyser.api.app")
CONFIG_S3_BUCKET_ENV_VAR = api_module.CONFIG_S3_BUCKET_ENV_VAR
CONFIG_S3_KEY_ENV_VAR = api_module.CONFIG_S3_KEY_ENV_VAR
app = api_module.app


class FakeProvider:
    name = "fake"


def _write_config(tmp_path: Path, *, provider_kind: str = "gemini") -> Path:
    config_path = tmp_path / "run_config.json"
    config_path.write_text(
        (
            "{"
            f'"provider":{{"kind":"{provider_kind}","model":"gemini-3-flash-preview"}},'
            '"overlay_zones":[],"enable_person_id":false'
            "}"
        ),
        encoding="utf-8",
    )
    return config_path


def _config_json(
    *,
    provider_kind: str = "gemini",
    system_prompt: str | None = None,
    user_prompt: str | None = None,
) -> str:
    body = {
        "provider": {"kind": provider_kind, "model": "gemini-3-flash-preview"},
        "overlay_zones": [],
        "enable_person_id": False,
    }
    if system_prompt is not None:
        body["system_prompt"] = system_prompt
    if user_prompt is not None:
        body["user_prompt"] = user_prompt
    return json.dumps(body)


def test_run_config_from_json_path(tmp_path: Path) -> None:
    config = RunConfig.from_json_path(_write_config(tmp_path))

    assert config.provider.name == "gemini"
    assert config.overlay is None
    assert config.person_id is None


def test_run_config_from_json_path_rejects_invalid_provider(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, provider_kind="other")

    with raises(ValueError, match="Unsupported analysis provider"):
        RunConfig.from_json_path(config_path)


def test_run_config_from_json_text_includes_optional_prompts() -> None:
    config = RunConfig.from_json_text(
        _config_json(system_prompt="system from s3", user_prompt="user from s3")
    )

    assert config.system_prompt == "system from s3"
    assert config.user_prompt == "user from s3"


def test_analyse_video_calls_run_and_cleans_up(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_S3_BUCKET_ENV_VAR, "test-bucket")
    monkeypatch.setenv(CONFIG_S3_KEY_ENV_VAR, "config/run_config.json")
    monkeypatch.setattr(
        api_module,
        "_load_run_config_from_s3",
        lambda bucket, key: RunConfig.from_json_text(
            _config_json(system_prompt="system from s3", user_prompt="user from s3")
        ),
    )

    response_model = AnalyseResponse(
        ir_mode="unknown",
        parking_spot_status="unknown",
        number_plate=None,
        events_description="none",
        message_for_user="Nothing relevant happened at your property.",
        send_notification=False,
    )
    captured: dict[str, object] = {}

    async def fake_run(video_path: str | Path, user_prompt: str, system_prompt: str, config: RunConfig):
        captured["video_path"] = Path(video_path)
        captured["user_prompt"] = user_prompt
        captured["system_prompt"] = system_prompt
        captured["config"] = config
        assert Path(video_path).exists()
        return response_model

    monkeypatch.setattr(api_module, "run", fake_run)

    with TestClient(app) as client:
        response = client.post(
            "/analyse-video",
            files={"video": ("clip.mp4", b"video-bytes", "video/mp4")},
            data={"storage_path": "abc", "start_time": "2026-03-15T10:00:00Z"},
        )

    assert response.status_code == 200
    assert response.json() == response_model.model_dump(mode="json")
    assert captured["user_prompt"] == (
        "user from s3\n\n"
        "Event metadata:\n"
        "- storage_path: abc\n"
        "- start_time: 2026-03-15T10:00:00Z"
    )
    assert captured["system_prompt"] == "system from s3"
    assert isinstance(captured["config"], RunConfig)
    assert not Path(captured["video_path"]).exists()


def test_analyse_video_cleans_up_temp_file_on_failure(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_S3_BUCKET_ENV_VAR, "test-bucket")
    monkeypatch.setattr(api_module, "_load_run_config_from_s3", lambda bucket, key: RunConfig.from_json_text(_config_json()))
    captured: dict[str, object] = {}

    async def fake_run(video_path: str | Path, user_prompt: str, system_prompt: str, config: RunConfig):
        captured["video_path"] = Path(video_path)
        raise RuntimeError("boom")

    monkeypatch.setattr(api_module, "run", fake_run)

    with TestClient(app) as client:
        response = client.post(
            "/analyse-video",
            files={"video": ("clip.mp4", b"video-bytes", "video/mp4")},
            data={"station_serial_number": "homebase-1"},
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Video analysis failed"}
    assert not Path(captured["video_path"]).exists()


def test_analyse_video_rejects_empty_upload(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_S3_BUCKET_ENV_VAR, "test-bucket")
    monkeypatch.setattr(api_module, "_load_run_config_from_s3", lambda bucket, key: RunConfig.from_json_text(_config_json()))
    mocked_run = AsyncMock()
    monkeypatch.setattr(api_module, "run", mocked_run)

    with TestClient(app) as client:
        response = client.post(
            "/analyse-video",
            files={"video": ("clip.mp4", b"", "video/mp4")},
            data={"station_serial_number": "homebase-1"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Uploaded video is empty"}
    mocked_run.assert_not_awaited()


def test_analyse_video_accepts_request_without_metadata(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_S3_BUCKET_ENV_VAR, "test-bucket")
    monkeypatch.setattr(api_module, "_load_run_config_from_s3", lambda bucket, key: RunConfig.from_json_text(_config_json()))
    response_model = AnalyseResponse(
        ir_mode="unknown",
        parking_spot_status="unknown",
        number_plate=None,
        events_description="none",
        message_for_user="Nothing relevant happened at your property.",
        send_notification=False,
    )
    captured: dict[str, object] = {}

    async def fake_run(video_path: str | Path, user_prompt: str, system_prompt: str, config: RunConfig):
        captured["user_prompt"] = user_prompt
        return response_model

    monkeypatch.setattr(api_module, "run", fake_run)

    with TestClient(app) as client:
        response = client.post(
            "/analyse-video",
            files={"video": ("clip.mp4", b"video-bytes", "video/mp4")},
        )

    assert response.status_code == 200
    assert captured["user_prompt"] == api_module.DEFAULT_USER_PROMPT


def test_configure_logging_adds_root_handler(monkeypatch: MonkeyPatch) -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    try:
        root_logger.handlers = []
        root_logger.setLevel(logging.NOTSET)
        api_module.configure_logging()
        assert root_logger.handlers
        assert root_logger.level == logging.INFO
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
