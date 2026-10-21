import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ingest.config import Settings
from ingest.vault import Vault
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl


class BrowserEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    t: AwareDatetime
    url: HttpUrl
    title: str = Field(max_length=500)
    scroll_y: float = Field(ge=0)
    viewport: list[int] = Field(min_length=2, max_length=2)


def create_capture_app(settings: Settings | None = None, *, receive: bool = False) -> FastAPI:
    settings = settings or Settings()
    vault = Vault(settings.vault_root)
    vault.initialize()
    paused = not receive
    app = FastAPI(title="DAYCUBE browser receiver", version="0.1.0")
    bearer = HTTPBearer(auto_error=False)
    origins = [f"chrome-extension://{settings.extension_id}"] if settings.extension_id else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or not secrets.compare_digest(credentials.credentials, vault.token):
            raise HTTPException(401, "Valid vault bearer token required")

    @app.get("/status")
    def status():
        return {
            "paused": paused,
            "capabilities": ["browser_metadata_receiver"],
            "screenshots": False,
            "webcam": False,
            "native_sampler": False,
        }

    @app.post("/pause", dependencies=[Depends(authorize)])
    def pause():
        nonlocal paused
        paused = True
        return status()

    @app.post("/resume", dependencies=[Depends(authorize)])
    def resume():
        nonlocal paused
        paused = False
        return status()

    @app.post("/events", dependencies=[Depends(authorize)])
    def events(batch: list[BrowserEvent]):
        if paused:
            raise HTTPException(409, "Receiver is paused")
        if len(batch) > 100:
            raise HTTPException(413, "At most 100 browser records per batch")
        with vault.lock:
            groups: dict[str, list[dict]] = {}
            for event in batch:
                # Reject stale/future records to prevent clients writing arbitrary date buckets.
                if abs((datetime.now(event.t.tzinfo) - event.t).total_seconds()) > 3600:
                    raise HTTPException(422, "Browser record must be within one hour of receiver time")
                day = event.t.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
                groups.setdefault(day, []).append(event.model_dump(mode="json"))
            for day, records in groups.items():
                # Appending costs the batch, not the day: rewriting the log made capture O(n^2).
                vault.append_lines(vault.root / "capture" / day / "browser.jsonl", records)
        return {"accepted": len(batch)}

    return app
