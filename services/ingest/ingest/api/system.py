"""About this installation: what the vault holds, training runs, the eye tracker's learning, and
pausing capture."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..vault import Record, Vault
from .deps import AUTHORIZED, TheStore, TheVault, conditional

router = APIRouter()
CAPTURE = "http://127.0.0.1:7700"  # the capture receiver (services/capture-win)


@router.get("/data")
def data_center(store: TheStore) -> Record:
    """Everything the vault holds: its source files by stream (days, capture logs, notes,
    calibration) and the database derived from them (rows per table, kinds, sizes)."""
    from ..datacenter import report

    return report(store)


@router.get("/training")
def training_runs(vault: TheVault) -> list[Record]:
    """Every training run on this PC (vault/training), newest first, with its last measurement."""
    from ..training import runs

    return runs(vault.root)


@router.get("/training/{run_id}")
def training_run(run_id: str, vault: TheVault) -> Record:
    """One run and its measurements as columns."""
    from ..training import series

    found = series(vault.root, run_id)
    if found is None:
        raise FileNotFoundError(run_id)
    return found


@router.get("/gaze/learning")
def gaze_learning(request: Request, vault: TheVault) -> Response:
    """The eye tracker's learning from clicks for the current calibration: every click's live
    error (the model in use and the calibration alone, on clicks neither had learned from), each
    fit with its held-out errors, and when each update reached the tracker's frames. Tagged: a
    client holding the current copy gets an empty 304."""
    from ..gaze_learning import tagged

    return conditional(request, *tagged(vault.root))


def capture_control(vault: Vault, action: str) -> Record:
    try:
        response = httpx.post(f"{CAPTURE}/{action}", headers={"Authorization": f"Bearer {vault.token}"}, timeout=2)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as error:
        raise HTTPException(503, "Capture receiver is not available") from error


@router.post("/pause", dependencies=AUTHORIZED)
def pause(vault: TheVault) -> Record:
    return capture_control(vault, "pause")


@router.post("/resume", dependencies=AUTHORIZED)
def resume(vault: TheVault) -> Record:
    return capture_control(vault, "resume")
