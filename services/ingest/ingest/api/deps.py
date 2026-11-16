"""What the routes share: the app's vault, store and settings, the bearer check, the clock and
the conditional (ETag) reply."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from ..config import Settings
from ..store import LifeStore
from ..vault import Vault


def state(name: str) -> Any:
    """A dependency on something ``create_app`` put on the app (``app.state.<name>``)."""

    def get(request: Request) -> Any:
        return getattr(request.app.state, name)

    return Depends(get)


TheVault = Annotated[Vault, state("vault")]
TheStore = Annotated[LifeStore, state("store")]
TheSettings = Annotated[Settings, state("settings")]


def authorize(
    vault: TheVault, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))]
) -> None:
    if credentials is None or not secrets.compare_digest(credentials.credentials, vault.token):
        raise HTTPException(401, "A valid vault bearer token is required", headers={"WWW-Authenticate": "Bearer"})


AUTHORIZED = [Depends(authorize)]  # on every route that changes the vault or returns a secret


def local_now(settings: TheSettings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


Now = Annotated[datetime, Depends(local_now)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def conditional(request: Request, body: bytes, etag: str) -> Response:
    """``body`` with its tag, or an empty 304 when the client already holds that copy."""
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if etag in (tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")):
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)
