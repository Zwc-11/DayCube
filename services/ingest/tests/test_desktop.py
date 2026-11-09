"""The desktop app's one origin (ingest/desktop.py): viewer at /, API at /api, the API's lifespan."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.config import Settings
from ingest.desktop import build
from ingest.vault import Vault

DAY = "2026-10-20"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>DayCube</title>", encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return root


def desktop(vault: Vault, dist: Path, *, record: bool = False) -> TestClient:
    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None)
    return TestClient(build(create_app(settings), dist, record=record), base_url="http://127.0.0.1:7701")


def test_viewer_and_api_share_one_origin(vault: Vault, dist: Path) -> None:
    with desktop(vault, dist) as client:
        page = client.get("/")
        assert page.status_code == 200 and "<title>DayCube</title>" in page.text
        assert page.headers["cache-control"] == "no-store"
        asset = client.get("/assets/index-abc123.js")
        assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
        assert client.get("/favicon.svg").text == "<svg/>"
        # The API as the viewer calls it through the dev server's proxy.
        assert client.get("/api/health").json()["capabilities"]["ask_mode"] == "extractive"
        assert client.get("/api/days").json()[0]["date"] == DAY


def test_a_revalidated_file_is_304_not_the_page(vault: Vault, dist: Path) -> None:
    with desktop(vault, dist) as client:
        for path in ("/favicon.svg", "/assets/index-abc123.js"):
            etag = client.get(path).headers["etag"]
            again = client.get(path, headers={"If-None-Match": etag})
            assert again.status_code == 304, path


def test_only_loopback_names_are_served(vault: Vault, dist: Path) -> None:
    """A page that rebinds its own name to 127.0.0.1 sends that name as Host: refused."""
    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None)
    app = build(create_app(settings), dist)
    with TestClient(app, base_url="http://evil.example:7701") as client:
        assert client.get("/api/days").status_code == 400
        assert client.get("/").status_code == 400
    with TestClient(app, base_url="http://localhost:7701") as client:
        assert client.get("/api/days").status_code == 200


def test_viewer_routes_fall_back_to_the_page(vault: Vault, dist: Path) -> None:
    with desktop(vault, dist) as client:
        for path in (f"/{DAY}", "/notes/graph", "/index.html"):
            response = client.get(path)
            assert response.status_code == 200 and "<title>DayCube</title>" in response.text, path
        # A missing hashed asset is a 404, not the page (a stale chunk must fail loudly).
        assert client.get("/assets/index-gone.js").status_code == 404
        # Nothing outside the viewer's folder is reachable.
        assert "DayCube" in client.get("/../vault/.token").text
        assert vault.token not in client.get("/%2e%2e/vault/.token").text


def test_api_token_rules_hold_under_api(vault: Vault, dist: Path) -> None:
    body = {"date": DAY, "question": "demo order"}
    with desktop(vault, dist) as client:
        assert client.post("/api/ask", json=body).status_code == 401
        auth = {"Authorization": f"Bearer {vault.token}"}
        assert client.post("/api/ask", json=body, headers=auth).status_code == 200


def test_api_lifespan_runs_when_mounted(tmp_path: Path, dist: Path) -> None:
    """A fresh vault folder: the API's lifespan initialises it (and makes the token) at startup."""
    root = tmp_path / "fresh"
    settings = Settings(vault_root=root, vector_search=False, _env_file=None)
    with TestClient(build(create_app(settings), dist)):
        assert (root / ".token").is_file()


def test_token_probe(vault: Vault, dist: Path) -> None:
    with desktop(vault, dist) as client:
        assert client.get("/__desktop/token").status_code == 401
        assert client.get("/__desktop/token", headers={"Authorization": "Bearer nope"}).status_code == 401
        good = client.get("/__desktop/token", headers={"Authorization": f"Bearer {vault.token}"})
        assert good.status_code == 204 and good.headers["cache-control"] == "no-store"


def test_selftest_records_only_when_asked(vault: Vault, dist: Path) -> None:
    with desktop(vault, dist) as client:
        assert "<title>" in client.get("/__selftest").text  # the page, no recorder
    with desktop(vault, dist, record=True) as client:
        client.get("/")
        client.get("/api/days", headers={"Authorization": f"Bearer {vault.token}"})
        seen = client.get("/__selftest").json()["requests"]
        assert [(r["path"], r["status"], r["bearer"]) for r in seen] == [("/", 200, False), ("/api/days", 200, True)]


def test_missing_viewer_is_a_clear_error(vault: Vault, tmp_path: Path) -> None:
    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None)
    with pytest.raises(FileNotFoundError, match="npm run build"):
        build(create_app(settings), tmp_path / "nowhere")
