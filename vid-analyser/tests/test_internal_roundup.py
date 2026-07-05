import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def test_internal_weekly_roundup_is_authenticated_and_returns_message(tmp_path, monkeypatch):
    monkeypatch.setenv("VID_ANALYSER_API_KEY", "test-key")
    monkeypatch.setenv("VID_ANALYSER_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("VID_ANALYSER_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("VID_ANALYSER_SQLITE_PATH", str(tmp_path / "vid-analyser.db"))

    from vid_analyser.api.app import app
    from vid_analyser.api.routes import internal

    calls = []

    async def fake_run_weekly_roundup(app_arg):
        calls.append(app_arg)
        return "Your weekly roundup"

    monkeypatch.setattr(internal, "run_weekly_roundup", fake_run_weekly_roundup)

    with TestClient(app) as client:
        unauthorized = client.post("/internal/weekly-roundup")
        assert unauthorized.status_code == 401

        response = client.post(
            "/internal/weekly-roundup",
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "sent",
        "message": "Your weekly roundup",
    }
    assert calls == [app]
