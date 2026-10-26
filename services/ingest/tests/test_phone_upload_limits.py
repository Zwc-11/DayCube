"""Synthetic requests stop at the chunk budget before storage sees any data."""

import asyncio
from pathlib import Path

import pytest
from capture.phone_api import CHUNK, Phones, router
from fastapi import HTTPException
from starlette.requests import Request


@pytest.mark.parametrize("upload_id", ["..\\outside", "wrong", "f" * 33, "../outside"])
def test_upload_metadata_accepts_only_its_generated_id(tmp_path: Path, upload_id: str) -> None:
    with pytest.raises(HTTPException) as error:
        Phones(tmp_path)._meta(upload_id)
    assert error.value.status_code == 422


def test_oversized_stream_stops_before_reading_the_rest_of_the_body(tmp_path: Path) -> None:
    received = 0

    async def receive() -> dict:
        nonlocal received
        received += 1
        return {"type": "http.request", "body": b"x" * (CHUNK // 2 + 1), "more_body": received < 3}

    request = Request({"type": "http", "method": "PUT", "path": "/synthetic", "headers": []}, receive)
    endpoint = next(
        route.endpoint for route in router(Phones(tmp_path), lambda: "synthetic").routes if route.name == "put"
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(endpoint("f" * 32, 0, request, "synthetic"))
    assert error.value.status_code == 413
    assert received == 2
    assert not (tmp_path / "inbox").exists()
