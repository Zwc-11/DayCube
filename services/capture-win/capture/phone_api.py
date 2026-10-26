"""Uploads from the phone (the DayCube Capture app) into ``vault/inbox/phone``, where the importer
takes them as it takes exports dropped there by hand (docs/phone.md).

- **Pairing.** ``POST /phone/pair`` (with the vault's token, from the viewer) gives a device its
  own token. The phone shows it as a QR code; only its SHA-256 is kept.
- **Files.** Each file is uploaded in chunks that resume where they stopped:
  - ``POST /phone/uploads {item, name, bytes, sha256}`` returns the upload's id and how much has
    arrived.
  - ``PUT /phone/uploads/{id}?offset=`` appends a chunk at that offset.
  - ``POST /phone/uploads/{id}/complete`` checks the size and hash.
  - Every step can be repeated. A finished file answers ``done``, so the phone deletes its copy
    only after that acknowledgement.
- **Items.** An item is what is imported together: one audio hour, one sensor recording, one
  scan folder. Its files collect in ``inbox/phone/.staging/<device>/<item>``.
  ``POST /phone/items/complete {item}`` moves it into the inbox in one step. A scan whose upload
  pauses half way is never imported half made: the inbox imports what has stopped changing.
- **Heartbeats.** ``POST /phone/heartbeat`` carries battery, free storage, recording state and the
  upload queue. The last one per device is kept for doctor, and all of them are appended to
  ``vault/capture/phone-heartbeats.jsonl``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

CHUNK = 4 * 1024 * 1024
MAX_FILE = 8 * 1024**3
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class Pair(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class NewUpload(BaseModel):
    item: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=400)  # a path inside the item: "rgb.mp4", "depth/000007.png"
    bytes: int = Field(ge=0, le=MAX_FILE)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")


class Item(BaseModel):
    item: str = Field(min_length=1, max_length=128)


class Heartbeat(BaseModel):
    t: float | None = None
    battery: float | None = Field(default=None, ge=0, le=1)
    charging: bool | None = None
    storage_free_mb: float | None = None
    state: dict[str, Any] = Field(default_factory=dict)  # what is recording: {"audio": true, "location": "on"}
    queue_files: int | None = None
    queue_bytes: int | None = None
    app_version: str | None = None


def _safe_parts(name: str) -> list[str]:
    parts = name.replace("\\", "/").split("/")
    if not parts or any(not SAFE.match(p) for p in parts):
        raise HTTPException(422, f"unsafe name: {name}")
    return parts


class Phones:
    """Devices, uploads in progress and heartbeats, in ``vault/capture`` and ``vault/inbox/phone``."""

    def __init__(self, vault_root: Path) -> None:
        self.root = vault_root
        self.inbox = vault_root / "inbox" / "phone"
        self.staging = self.inbox / ".staging"
        self.partial = self.inbox / ".partial"
        self.devices_file = vault_root / "capture" / "phone-devices.json"
        self.beats = vault_root / "capture" / "phone-heartbeats.jsonl"
        self.lock = threading.Lock()

    # -- devices --------------------------------------------------------------------------
    def devices(self) -> dict:
        try:
            return json.loads(self.devices_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _save(self, devices: dict) -> None:
        self.devices_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.devices_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(devices, indent=1), encoding="utf-8")
        os.replace(tmp, self.devices_file)

    def pair(self, name: str) -> dict:
        token = secrets.token_urlsafe(32)
        device = "ph-" + secrets.token_hex(4)
        with self.lock:
            devices = self.devices()
            devices[device] = {"name": name, "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                               "paired": time.time()}  # fmt: skip
            self._save(devices)
        return {"device": device, "token": token}

    def device_of(self, token: str) -> str | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        for device, info in self.devices().items():
            if secrets.compare_digest(info.get("token_sha256", ""), digest):
                return device
        return None

    # -- uploads --------------------------------------------------------------------------
    def _meta(self, upload_id: str) -> dict:
        if re.fullmatch(r"[0-9a-f]{32}", upload_id) is None:
            raise HTTPException(422, "invalid upload id")
        try:
            return json.loads((self.partial / f"{upload_id}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise HTTPException(404, f"No upload {upload_id}") from None

    def start(self, device: str, body: NewUpload) -> dict:
        item = _safe_parts(body.item)
        if len(item) != 1:
            raise HTTPException(422, "an item is one name")
        _safe_parts(body.name)
        upload_id = hashlib.sha256(f"{device}/{body.item}/{body.name}/{body.sha256}".encode()).hexdigest()[:32]
        target = self.staging.joinpath(device, body.item, *body.name.split("/"))
        with self.lock:
            if target.is_file() and target.stat().st_size == body.bytes:
                return {"id": upload_id, "offset": body.bytes, "done": True}
            self.partial.mkdir(parents=True, exist_ok=True)
            meta = self.partial / f"{upload_id}.json"
            if not meta.exists():
                meta.write_text(json.dumps({"device": device, **body.model_dump()}), encoding="utf-8")
                (self.partial / f"{upload_id}.part").touch()
            offset = (self.partial / f"{upload_id}.part").stat().st_size
        return {"id": upload_id, "offset": offset, "done": False, "chunk": CHUNK}

    def put(self, device: str, upload_id: str, offset: int, data: bytes) -> dict:
        meta = self._meta(upload_id)
        if meta["device"] != device:
            raise HTTPException(403, "not this device's upload")
        part = self.partial / f"{upload_id}.part"
        with self.lock:
            have = part.stat().st_size
            if offset != have:
                raise HTTPException(409, detail={"offset": have})
            if have + len(data) > meta["bytes"]:
                raise HTTPException(413, "more than the file's size")
            with part.open("ab") as handle:
                handle.write(data)
            return {"id": upload_id, "offset": have + len(data)}

    def complete(self, device: str, upload_id: str) -> dict:
        meta = self._meta(upload_id)
        if meta["device"] != device:
            raise HTTPException(403, "not this device's upload")
        part = self.partial / f"{upload_id}.part"
        with self.lock:
            size = part.stat().st_size
            if size != meta["bytes"]:
                raise HTTPException(409, detail={"offset": size, "bytes": meta["bytes"]})
            digest = hashlib.sha256()
            with part.open("rb") as handle:
                while block := handle.read(1 << 20):
                    digest.update(block)
            if digest.hexdigest() != meta["sha256"]:
                part.unlink()
                (self.partial / f"{upload_id}.json").unlink()
                raise HTTPException(422, "sha256 does not match: upload it again")
            target = self.staging.joinpath(device, meta["item"], *meta["name"].split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(part, target)
            (self.partial / f"{upload_id}.json").unlink()
        return {"id": upload_id, "done": True, "sha256": meta["sha256"]}

    def finish_item(self, device: str, item: str) -> dict:
        """The item's files into the inbox at once (a folder, or its one file)."""
        _safe_parts(item)
        source = self.staging / device / item
        destination = self.inbox / f"{device}-{item}"
        finished = self.staging / "finished.jsonl"
        with self.lock:
            if not source.exists():
                # Done before (the importer may have taken it already): the phone asks again
                # after a lost answer, and must hear that it may delete its copy.
                if finished.is_file():
                    for line in finished.read_text(encoding="utf-8").splitlines():
                        record = json.loads(line)
                        if record["device"] == device and record["item"] == item:
                            return {"item": item, "done": True, "path": record["path"]}
                raise HTTPException(404, f"No files of {item}")
            files = [p for p in source.rglob("*") if p.is_file()]
            if len(files) == 1 and files[0].parent == source:
                destination = self.inbox / f"{device}-{item}{files[0].suffix if not Path(item).suffix else ''}"
                os.replace(files[0], destination)
                shutil.rmtree(source)
            else:
                if destination.exists():
                    shutil.rmtree(destination)
                os.replace(source, destination)
            with finished.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"device": device, "item": item, "path": destination.name, "t": time.time()}) + "\n"
                )
        return {"item": item, "done": True, "path": destination.name, "files": len(files)}

    # -- heartbeats -----------------------------------------------------------------------
    def heartbeat(self, device: str, beat: Heartbeat) -> dict:
        record = {"device": device, "received": time.time(), **beat.model_dump(exclude_none=True)}
        with self.lock:
            devices = self.devices()
            if device in devices:
                devices[device]["last"] = record
                self._save(devices)
            self.beats.parent.mkdir(parents=True, exist_ok=True)
            with self.beats.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        return {"ok": True}

    def status(self) -> dict:
        uploads = []
        if self.partial.is_dir():
            for meta in self.partial.glob("*.json"):
                info = json.loads(meta.read_text(encoding="utf-8"))
                part = meta.with_suffix(".part")
                uploads.append({"id": meta.stem, "device": info["device"], "item": info["item"], "name": info["name"],
                                "offset": part.stat().st_size if part.exists() else 0, "bytes": info["bytes"]})  # fmt: skip
        devices = {d: {k: v for k, v in info.items() if k != "token_sha256"} for d, info in self.devices().items()}
        return {"devices": devices, "uploads": uploads}


def router(phones: Phones, vault_token: Any) -> APIRouter:
    """``vault_token``: a callable giving the vault's bearer token (the viewer's)."""
    api = APIRouter(prefix="/phone")
    bearer = HTTPBearer(auto_error=False)

    def vault_only(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if credentials is None or not secrets.compare_digest(credentials.credentials, vault_token()):
            raise HTTPException(401, "Valid vault bearer token required")

    def device(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
        found = phones.device_of(credentials.credentials) if credentials is not None else None
        if found is None:
            raise HTTPException(401, "Pair this phone first (POST /phone/pair)")
        return found

    @api.post("/pair", dependencies=[Depends(vault_only)])
    def pair(body: Pair) -> dict:
        return phones.pair(body.name)

    @api.get("/status", dependencies=[Depends(vault_only)])
    def status() -> dict:
        return phones.status()

    @api.post("/uploads")
    def start(body: NewUpload, who: str = Depends(device)) -> dict:
        return phones.start(who, body)

    @api.put("/uploads/{upload_id}")
    async def put(upload_id: str, offset: int, request: Request, who: str = Depends(device)) -> dict:
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > CHUNK:
                raise HTTPException(413, f"chunks of at most {CHUNK} bytes")
            data.extend(chunk)
        return phones.put(who, upload_id, offset, data)

    @api.post("/uploads/{upload_id}/complete")
    def complete(upload_id: str, who: str = Depends(device)) -> dict:
        return phones.complete(who, upload_id)

    @api.post("/items/complete")
    def finish(body: Item, who: str = Depends(device)) -> dict:
        return phones.finish_item(who, body.item)

    @api.post("/heartbeat")
    def heartbeat(body: Heartbeat, who: str = Depends(device)) -> dict:
        return phones.heartbeat(who, body)

    return api
