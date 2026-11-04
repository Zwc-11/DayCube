"""The desktop app's one origin: the built viewer at ``/`` and the vault API at ``/api``.

The viewer calls ``/api/...`` (the Vite dev server proxies that prefix to 127.0.0.1:7701), so one
loopback origin serving both needs no path, proxy or CORS changes. The app runs::

    python -m uvicorn ingest.desktop:create_desktop_app --factory --host 127.0.0.1 --port <port>

with the built viewer's folder in ``DAYCUBE_VIEWER_DIST``. Starlette does not run a mounted app's
lifespan, so this app runs the API's (vault initialisation, search warm-up) itself.

``/__desktop/token`` answers 204 to a request carrying the vault token and 401 otherwise: the app
checks the token it injected into the window from inside that window. ``DAYCUBE_DESKTOP_SELFTEST=1``
(only ``DayCube.exe --self-test`` sets it) also keeps a list of the requests the window made, at
``/__selftest``: proof that the window ran the viewer, without a person looking at it.
"""

from __future__ import annotations

import os
import secrets
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

NO_STORE = {"Cache-Control": "no-store"}
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}  # Vite names assets by content hash


class Viewer:
    """The built viewer. Files are served as they are; any other path that is not a file gets
    ``index.html``, because the viewer routes on ``location.pathname`` (``/2026-10-20``, ...)."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.index = self.root / "index.html"
        if not self.index.is_file():
            raise FileNotFoundError(f"no built viewer at {self.root} (npm run build)")
        self.files = StaticFiles(directory=self.root)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            raise HTTPException(404)
        path = scope["path"]
        if path.startswith("/assets/"):
            response = await self._file(scope)
            if response is not None:
                response.headers.update(IMMUTABLE)
                await response(scope, receive, send)
                return
            await Response("Not found", 404, headers=NO_STORE)(scope, receive, send)
            return
        response = await self._file(scope) if path not in ("", "/", "/index.html") else None
        if response is None:
            response = FileResponse(self.index, media_type="text/html", headers=NO_STORE)
        await response(scope, receive, send)

    async def _file(self, scope: Scope) -> Response | None:
        try:
            response = await self.files.get_response(self.files.get_path(scope), scope)
        except HTTPException:
            return None
        return response if response.status_code == 200 else None


class Recorder:
    """The window's requests, for ``--self-test``: path, status and whether a token came with it."""

    def __init__(self, app: ASGIApp, limit: int = 2000) -> None:
        self.app, self.limit = app, limit
        self.seen: list[dict] = []
        self.lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/__selftest":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        entry = {"method": scope["method"], "path": scope["path"], "bearer": headers.get(b"authorization", b"")
                 .startswith(b"Bearer "), "status": None}  # fmt: skip

        async def sending(message: dict) -> None:
            if message["type"] == "http.response.start":
                entry["status"] = message["status"]
            await send(message)

        with self.lock:
            if len(self.seen) < self.limit:
                self.seen.append(entry)
        await self.app(scope, receive, sending)


def build(api: ASGIApp, viewer_dist: Path, *, record: bool = False) -> ASGIApp:
    """The parent app: ``api`` under ``/api`` (with its lifespan run here), the viewer at ``/``."""
    lifespan_of = getattr(getattr(api, "router", None), "lifespan_context", None)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        if lifespan_of is None:
            yield
            return
        async with lifespan_of(api):
            yield

    recorder: Recorder | None = None

    async def token(request: Request) -> Response:
        vault = getattr(getattr(api, "state", None), "vault", None)
        header = request.headers.get("authorization", "")
        good = vault is not None and header.startswith("Bearer ") and secrets.compare_digest(header[7:], vault.token)
        return Response(status_code=204 if good else 401, headers=NO_STORE)

    async def selftest(_: Request) -> JSONResponse:
        assert recorder is not None
        with recorder.lock:
            return JSONResponse({"requests": list(recorder.seen)}, headers=NO_STORE)

    routes = [Route("/__desktop/token", token), *([Route("/__selftest", selftest)] if record else [])]
    app = Starlette(routes=[*routes, Mount("/api", app=api), Mount("/", app=Viewer(viewer_dist))], lifespan=lifespan)
    if record:
        recorder = Recorder(app)
        return _Lifespanned(recorder, app)
    return app


class _Lifespanned:
    """The recorder in front of the parent app; lifespan messages go straight to the parent."""

    def __init__(self, outer: ASGIApp, inner: Starlette) -> None:
        self.outer, self.inner = outer, inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await (self.inner if scope["type"] == "lifespan" else self.outer)(scope, receive, send)


def create_desktop_app() -> ASGIApp:
    from .api import app as api  # the module's app: created once, warm

    dist = os.environ.get("DAYCUBE_VIEWER_DIST")
    if not dist:
        raise SystemExit("DAYCUBE_VIEWER_DIST is not set: the built viewer's folder")
    return build(api, Path(dist), record=os.environ.get("DAYCUBE_DESKTOP_SELFTEST") == "1")
