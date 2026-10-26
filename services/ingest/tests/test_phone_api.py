"""The phone's way into the vault (capture.phone_api): pairing, resumable uploads checked by hash,
items that reach the inbox only when complete, and heartbeats; a scan uploaded file by file comes
out as a scan moment with no command."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest.config import Settings
from ingest.vault import Vault
from test_splat import make_capture


@pytest.fixture
def client(vault: Vault) -> TestClient:
    from capture.api import create_capture_app

    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None)
    with TestClient(create_capture_app(settings, receive=True)) as connection:
        yield connection


def _pair(client: TestClient, vault: Vault) -> dict:
    paired = client.post(
        "/phone/pair", json={"name": "iPhone 17 Pro Max"}, headers={"Authorization": f"Bearer {vault.token}"}
    )
    assert paired.status_code == 200
    return {"Authorization": f"Bearer {paired.json()['token']}", "device": paired.json()["device"]}


def _upload(client: TestClient, auth: dict, item: str, name: str, data: bytes, chunk: int = 7000) -> dict:
    headers = {"Authorization": auth["Authorization"]}
    body = {"item": item, "name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    started = client.post("/phone/uploads", json=body, headers=headers).json()
    offset = started["offset"]
    while offset < len(data):
        r = client.put(
            f"/phone/uploads/{started['id']}",
            params={"offset": offset},
            content=data[offset : offset + chunk],
            headers=headers,
        )
        assert r.status_code == 200, r.text
        offset = r.json()["offset"]
    return client.post(f"/phone/uploads/{started['id']}/complete", headers=headers).json()


def test_only_a_paired_phone_uploads_and_its_token_is_not_kept(client: TestClient, vault: Vault) -> None:
    body = {"item": "a", "name": "x.bin", "bytes": 3, "sha256": "0" * 64}
    assert client.post("/phone/pair", json={"name": "x"}).status_code == 401
    assert client.post("/phone/uploads", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    # The vault's own token is the viewer's, not a phone's.
    assert (
        client.post("/phone/uploads", json=body, headers={"Authorization": f"Bearer {vault.token}"}).status_code == 401
    )
    auth = _pair(client, vault)
    assert client.post("/phone/uploads", json=body, headers={"Authorization": auth["Authorization"]}).status_code == 200
    stored = (vault.root / "capture" / "phone-devices.json").read_text()
    assert auth["Authorization"].split()[1] not in stored and "token_sha256" in stored
    status = client.get("/phone/status", headers={"Authorization": f"Bearer {vault.token}"}).json()
    assert "token_sha256" not in json.dumps(status) and status["devices"][auth["device"]]["name"] == "iPhone 17 Pro Max"
    assert (
        client.post(
            "/phone/uploads", json={**body, "name": "../escape"}, headers={"Authorization": auth["Authorization"]}
        ).status_code
        == 422
    )


def test_an_upload_resumes_checks_its_offset_and_hash_and_is_acknowledged_again(
    client: TestClient, vault: Vault
) -> None:
    auth = _pair(client, vault)
    headers = {"Authorization": auth["Authorization"]}
    data = bytes(range(256)) * 100
    body = {
        "item": "audio-20261025T140000Z",
        "name": "audio.m4a",
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    up = client.post("/phone/uploads", json=body, headers=headers).json()
    assert up["offset"] == 0 and not up["done"]
    client.put(f"/phone/uploads/{up['id']}", params={"offset": 0}, content=data[:10000], headers=headers)
    # The phone was suspended; asking again gives where it stopped.
    again = client.post("/phone/uploads", json=body, headers=headers).json()
    assert again["id"] == up["id"] and again["offset"] == 10000
    wrong = client.put(f"/phone/uploads/{up['id']}", params={"offset": 5000}, content=data[5000:6000], headers=headers)
    assert wrong.status_code == 409 and wrong.json()["detail"]["offset"] == 10000
    assert client.post(f"/phone/uploads/{up['id']}/complete", headers=headers).status_code == 409  # not all there yet
    client.put(f"/phone/uploads/{up['id']}", params={"offset": 10000}, content=data[10000:], headers=headers)
    assert client.post(f"/phone/uploads/{up['id']}/complete", headers=headers).json()["done"]
    # Not in the inbox until its item is complete; then there in one step, and acknowledged again later.
    inbox = vault.root / "inbox" / "phone"
    assert not [p for p in inbox.iterdir() if not p.name.startswith(".")]
    done = client.post("/phone/items/complete", json={"item": body["item"]}, headers=headers).json()
    assert (
        done["path"] == f"{auth['device']}-audio-20261025T140000Z.m4a" and (inbox / done["path"]).read_bytes() == data
    )
    (inbox / done["path"]).unlink()  # the importer took it
    assert client.post("/phone/items/complete", json={"item": body["item"]}, headers=headers).json()["done"]
    assert client.post("/phone/uploads", json=body, headers=headers).status_code == 200

    # A corrupted upload is refused and starts over.
    bad = {**body, "item": "audio-2", "sha256": hashlib.sha256(b"other").hexdigest()}
    up = client.post("/phone/uploads", json=bad, headers=headers).json()
    client.put(f"/phone/uploads/{up['id']}", params={"offset": 0}, content=data, headers=headers)
    assert client.post(f"/phone/uploads/{up['id']}/complete", headers=headers).status_code == 422
    assert client.post("/phone/uploads", json=bad, headers=headers).json()["offset"] == 0


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs FFmpeg and ffprobe")
def test_a_scan_uploaded_file_by_file_becomes_a_scan_moment_with_no_command(
    client: TestClient, vault: Vault, tmp_path: Path
) -> None:
    from ingest.phone.inbox import process

    auth = _pair(client, vault)
    capture = make_capture(tmp_path / "phone" / "scan")
    files = sorted(p for p in capture.rglob("*") if p.is_file())
    for path in files:
        assert _upload(client, auth, "scan-20261021-181540", path.relative_to(capture).as_posix(), path.read_bytes())[
            "done"
        ]
    inbox = vault.root / "inbox" / "phone"
    assert process(vault, now=4e9, say=lambda _: None) is None  # nothing there yet: the scan is still being sent
    done = client.post(
        "/phone/items/complete", json={"item": "scan-20261021-181540"}, headers={"Authorization": auth["Authorization"]}
    )
    assert done.json()["files"] == len(files) and (inbox / done.json()["path"] / "odometry.csv").is_file()
    result = process(vault, now=4e9, say=lambda _: None)
    assert result and result["imported"]
    moments = list(vault.root.glob("*/moments/mo_scan_*/scene.json"))
    assert len(moments) == 1 and json.loads(moments[0].read_text())["kind"] == "camera-path"


def test_heartbeats_are_kept_for_doctor(client: TestClient, vault: Vault) -> None:
    from capture.doctor import _phones

    auth = _pair(client, vault)
    headers = {"Authorization": auth["Authorization"]}
    beat = {"t": 1_800_000_000, "battery": 0.62, "charging": False, "storage_free_mb": 41000, "state": {"audio": True, "location": "on"},
            "queue_files": 3, "queue_bytes": 42_000_000, "app_version": "0.1.0"}  # fmt: skip
    assert client.post("/phone/heartbeat", json=beat, headers=headers).json() == {"ok": True}
    last = json.loads((vault.root / "capture" / "phone-devices.json").read_text())[auth["device"]]["last"]
    assert last["battery"] == 0.62 and last["queue_files"] == 3
    assert len((vault.root / "capture" / "phone-heartbeats.jsonl").read_text().splitlines()) == 1
    rows = _phones(vault.root, now=last["received"] + 120)
    assert rows[0][0] == "pass" and "62%" in rows[0][2] and "3 files waiting" in rows[0][2]
    late = _phones(vault.root, now=last["received"] + 3 * 3600)
    assert late[0][0] == "warn" and "3 h" in late[0][2]
