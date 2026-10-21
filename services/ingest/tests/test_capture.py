from datetime import datetime, timezone

from capture.api import create_capture_app
from fastapi.testclient import TestClient
from ingest.config import Settings


def test_capture_receiver_starts_paused_and_requires_auth(vault, auth):
    with TestClient(create_capture_app(Settings(vault_root=vault.root, _env_file=None))) as client:
        assert client.get("/status").json()["paused"] is True
        assert client.post("/resume").status_code == 401
        batch = [{"t": datetime.now(timezone.utc).isoformat(), "url": "https://example.com/",
                  "title": "Example", "scroll_y": 0, "viewport": [1920, 1080]}]
        assert client.post("/events", json=batch, headers=auth).status_code == 409
        assert client.post("/resume", headers=auth).json()["paused"] is False
        assert client.post("/events", json=batch, headers=auth).json() == {"accepted": 1}
        assert len(list((vault.root / "capture").glob("*/browser.jsonl"))) == 1
        assert client.post("/pause", headers=auth).json()["paused"] is True
