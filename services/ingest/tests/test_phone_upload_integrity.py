"""Phone upload retries must not acknowledge different synthetic evidence."""

import hashlib
import os
from pathlib import Path

import pytest
from capture import phone_api
from capture.phone_api import NewUpload, Phones, router
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def _body(data: bytes) -> NewUpload:
    return NewUpload(item="recording", name="audio.bin", bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def _upload(phones: Phones, body: NewUpload, data: bytes) -> str:
    started = phones.start("ph-test", body)
    phones.put("ph-test", started["id"], 0, data)
    phones.complete("ph-test", started["id"])
    return started["id"]


def test_same_size_different_content_must_be_uploaded(tmp_path: Path) -> None:
    phones = Phones(tmp_path)
    _upload(phones, _body(b"first"), b"first")
    started = phones.start("ph-test", _body(b"other"))
    assert started["done"] is False
    assert started["offset"] == 0
    phones.put("ph-test", started["id"], 0, b"other")
    phones.complete("ph-test", started["id"])
    assert (phones.staging / "ph-test" / "recording" / "audio.bin").read_bytes() == b"other"


def test_completed_upload_can_be_retried_without_leaking_to_another_device(tmp_path: Path) -> None:
    phones = Phones(tmp_path)
    body = _body(b"first")
    upload_id = _upload(phones, body, b"first")
    assert phones.complete("ph-test", upload_id) == {"id": upload_id, "done": True, "sha256": body.sha256}
    with pytest.raises(HTTPException) as exc:
        phones.complete("ph-other", upload_id)
    assert exc.value.status_code == 403
    phones.finish_item("ph-test", "recording")
    assert phones.complete("ph-test", upload_id)["done"] is True


def test_replaced_staged_content_is_not_acknowledged(tmp_path: Path) -> None:
    phones = Phones(tmp_path)
    body = _body(b"first")
    _upload(phones, body, b"first")
    target = phones.staging / "ph-test" / "recording" / "audio.bin"
    target.write_bytes(b"other")
    started = phones.start("ph-test", body)
    assert started["done"] is False
    phones.put("ph-test", started["id"], 0, b"first")
    phones.complete("ph-test", started["id"])
    assert target.read_bytes() == b"first"


def test_complete_recovers_if_receipt_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    phones = Phones(tmp_path)
    body = _body(b"first")
    upload_id = phones.start("ph-test", body)["id"]
    phones.put("ph-test", upload_id, 0, b"first")
    replace = os.replace

    def fail_receipt(source: str | os.PathLike, destination: str | os.PathLike) -> None:
        if Path(destination).parent == phones.completed:
            raise OSError("synthetic receipt failure")
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_receipt)
        with pytest.raises(OSError, match="synthetic receipt failure"):
            phones.complete("ph-test", upload_id)
    assert phones.complete("ph-test", upload_id)["done"] is True
    assert phones.complete("ph-test", upload_id)["done"] is True


def test_receipts_do_not_appear_as_incomplete_uploads(tmp_path: Path) -> None:
    phones = Phones(tmp_path)
    _upload(phones, _body(b"first"), b"first")
    assert phones.status()["uploads"] == []


def test_old_receipt_cannot_acknowledge_failed_reupload(tmp_path: Path) -> None:
    phones = Phones(tmp_path)
    body = _body(b"first")
    upload_id = _upload(phones, body, b"first")
    (phones.staging / "ph-test" / "recording" / "audio.bin").write_bytes(b"other")
    assert phones.start("ph-test", body)["done"] is False
    phones.put("ph-test", upload_id, 0, b"other")
    with pytest.raises(HTTPException) as exc:
        phones.complete("ph-test", upload_id)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        phones.complete("ph-test", upload_id)
    assert exc.value.status_code == 404


def test_receipt_survives_failed_metadata_cleanup_and_item_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    phones = Phones(tmp_path)
    body = _body(b"first")
    upload_id = phones.start("ph-test", body)["id"]
    phones.put("ph-test", upload_id, 0, b"first")
    meta = phones.partial / f"{upload_id}.json"
    unlink = Path.unlink

    def fail_cleanup(path: Path, missing_ok: bool = False) -> None:
        if path == meta:
            raise OSError("synthetic cleanup failure")
        unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(OSError, match="synthetic cleanup failure"):
            phones.complete("ph-test", upload_id)
    phones.finish_item("ph-test", "recording")
    assert phones.complete("ph-test", upload_id)["done"] is True


def test_start_rechecks_identity_after_hashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    phones = Phones(tmp_path)
    body = _body(b"first")
    _upload(phones, body, b"first")
    target = phones.staging / "ph-test" / "recording" / "audio.bin"
    verify = phone_api._verified_file

    def replace_after_hash(path: Path, size: int, digest: str) -> tuple[int, int, int, int, int] | None:
        stamp = verify(path, size, digest)
        replacement = target.with_suffix(".replacement")
        replacement.write_bytes(b"other")
        os.replace(replacement, target)
        return stamp

    monkeypatch.setattr(phone_api, "_verified_file", replace_after_hash)
    assert phones.start("ph-test", body)["done"] is False


def test_retry_protocol_over_http(tmp_path: Path) -> None:
    phones = Phones(tmp_path)
    app = FastAPI()
    app.include_router(router(phones, lambda: "synthetic-vault-token"))
    with TestClient(app) as client:
        paired = client.post(
            "/phone/pair", json={"name": "synthetic"}, headers={"Authorization": "Bearer synthetic-vault-token"}
        ).json()
        headers = {"Authorization": f"Bearer {paired['token']}"}
        first = _body(b"first").model_dump()
        upload_id = client.post("/phone/uploads", json=first, headers=headers).json()["id"]
        assert client.put(f"/phone/uploads/{upload_id}?offset=0", content=b"first", headers=headers).status_code == 200
        completion = client.post(f"/phone/uploads/{upload_id}/complete", headers=headers)
        assert completion.status_code == 200
        assert client.post(f"/phone/uploads/{upload_id}/complete", headers=headers).json() == completion.json()
        changed = client.post("/phone/uploads", json=_body(b"other").model_dump(), headers=headers)
        assert changed.status_code == 200 and changed.json()["done"] is False
