"""The processing stages and what follows each (the stage graph), in one place.

A stage has a name, a version (raise it when its output changes, and its jobs run again), what it
needs (``cpu``, or ``gpu``: only when the PC is free, see ``policy``), and ``run``: the code, or
None for a stage run by a worker outside this environment (the speech worker, which leases over
the receiver's HTTP API). ``follow`` names the jobs a finished job leads to.

``Permanent`` marks a failure that trying again cannot fix (a scan whose poses disagree with its
depth): the job is failed at once, with the reason.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..vault import Vault, read_json
from .store import MAX_ATTEMPTS, Jobs

Say = Callable[[str], None]


class Permanent(RuntimeError):
    """A failure that a retry would only repeat."""


def _no_follow(inputs: dict, outputs: dict) -> list[tuple[str, dict]]:
    return []


def _always() -> bool:
    return True


@dataclass(frozen=True)
class Stage:
    name: str
    version: int
    needs: str  # "cpu" or "gpu"
    run: Callable[[Vault, dict, Say], dict] | None
    follow: Callable[[dict, dict], list[tuple[str, dict]]] = _no_follow
    ready: Callable[[], bool] = _always  # e.g. its tool is installed
    attempts: int = MAX_ATTEMPTS
    title: str = ""
    worker: str = ""  # for a stage run elsewhere: what runs it, for doctor
    install: str = ""  # for a stage that is not ready: how to make it ready, for doctor
    after: Callable[[Vault, dict, dict], None] | None = None  # once done, in the receiver (inputs, outputs)
    extra: dict = field(default_factory=dict)


STAGES: dict[str, Stage] = {}


def register(stage: Stage) -> Stage:
    STAGES[stage.name] = stage
    return stage


def enqueue(jobs: Jobs, name: str, inputs: dict, *, priority: int = 0) -> tuple[int, bool]:
    stage = STAGES[name]
    return jobs.add(stage.name, stage.version, inputs, priority=priority)


# ---- scans into 3D (ingest/splat) -----------------------------------------------------------


def _splat_preview(vault: Vault, inputs: dict, say: Say) -> dict:
    from ..splat.run import SplatError, splat_moment

    try:
        splat = splat_moment(vault, inputs["date"], inputs["moment"], train=False, say=say)
    except SplatError as error:
        raise Permanent(str(error)) from error
    return {"status": splat.get("status"), "points": splat.get("points")}


def _splat_train(vault: Vault, inputs: dict, say: Say) -> dict:
    from ..splat.run import SplatError, splat_moment

    try:
        splat = splat_moment(vault, inputs["date"], inputs["moment"], train=True, say=say)
    except SplatError as error:
        raise Permanent(str(error)) from error
    training = splat.get("training") or {}
    return {"splats": splat.get("splats"), "minutes": training.get("minutes"), "psnr_db": training.get("psnr_db")}


def _training_allowed() -> bool:
    from ..splat import brush

    return brush.allowed()


def _scan_mesh(vault: Vault, inputs: dict, say: Say) -> dict:
    from ..splat.run import SplatError, mesh_moment

    try:
        made = mesh_moment(vault, inputs["date"], inputs["moment"], say=say)
    except SplatError as error:
        raise Permanent(str(error)) from error
    return {"triangles": made["triangles"], "median_m": (made.get("check") or {}).get("median_m")}


def _mesh_available() -> bool:
    from ..splat import mesh

    return mesh.available()


register(Stage("scan_mesh", 1, "cpu", _scan_mesh, ready=_mesh_available, title="3D mesh",
               install="Open3D in the receiver: .\\capture restart after git pull (docs/splat.md)"))  # fmt: skip
register(Stage("splat_preview", 1, "cpu", _splat_preview, follow=lambda i, o: [("splat_train", i)], title="3D preview"))
register(Stage("splat_train", 1, "gpu", _splat_train, ready=_training_allowed, attempts=2, title="3D training",
               install="uv run daycube splat --date <day>, which installs Brush (docs/splat.md)"))  # fmt: skip


# ---- speech (services/speech: its own environment, leasing over HTTP) --------------------------


def _speech_done(vault: Vault, inputs: dict, outputs: dict) -> None:
    # Searches re-check changed files on their own (store/db.py FULL_CHECK_S); this drops what
    # the receiver itself had confirmed about the day.
    vault.invalidate_index(inputs["date"])


def _talks(vault: Vault, inputs: dict, outputs: dict) -> None:
    """The conversations the speech worker found in an hour of sound become talk moments (made
    here, in the receiver, the vault's one writer of days), each queued for its speech."""
    from ..phone.importer import add_talks

    made = add_talks(vault, inputs, outputs.get("spans") or [], say=lambda _: None)
    jobs = Jobs.of(vault.root)
    try:
        day_of = {m: f"{m[8:12]}-{m[12:14]}-{m[14:16]}" for m in made}  # mo_talk_YYYYMMDD_...
        for moment in made:
            enqueue(jobs, "speech", {"date": day_of[moment], "moment": moment})
    finally:
        jobs.close()


register(Stage("conversations", 1, "cpu", None, title="conversations", after=_talks,
               worker="the speech worker (docs/speech.md: uv sync --project services/speech)"))  # fmt: skip
register(Stage("speech", 1, "gpu", None, title="speech", after=_speech_done,
               worker="the speech worker (docs/speech.md: uv sync --project services/speech)"))  # fmt: skip


def audio_inputs(vault_root: Path) -> list[tuple[dict, bool]]:
    """``({"date", "moment"}, whether its speech is done)`` for every recording moment of sound."""
    out = []
    for scene_path in sorted(vault_root.glob("*/moments/*/scene.json"), reverse=True):
        scene = read_json(scene_path) or {}
        if scene.get("kind") == "audio" and scene.get("audio") and (scene_path.parent / scene["audio"]).is_file():
            inputs = {"date": scene_path.parent.parent.parent.name, "moment": scene_path.parent.name}
            out.append((inputs, (scene_path.parent / "speech.json").is_file()))
    return out


def scan_inputs(vault_root: Path) -> list[tuple[dict, dict]]:
    """``({"date", "moment"}, its scene's splat entry or {})`` for every scan in the vault."""
    out = []
    for scene_path in sorted(vault_root.glob("*/moments/mo_scan_*/scene.json"), reverse=True):
        scene = read_json(scene_path) or {}
        if scene.get("kind") == "camera-path":
            out.append(
                (
                    {"date": scene_path.parent.parent.parent.name, "moment": scene_path.parent.name},
                    scene.get("splat") or {},
                )
            )
    return out


def discover(vault: Vault, jobs: Jobs) -> list[int]:
    """Queue what the vault still needs: for a scan with its depth, its mesh, its preview and then
    its training; for a recording of sound, its speech. Ids of the jobs that are new; a stage already run on
    an input (done or failed) is not queued again."""
    from ..splat.run import find_capture

    new = []
    for inputs, splat in scan_inputs(vault.root):
        moment = vault.day_path(inputs["date"]) / "moments" / inputs["moment"]
        has_capture = find_capture(vault, moment) is not None
        if has_capture and not (read_json(moment / "scene.json") or {}).get("mesh"):
            job_id, created = enqueue(jobs, "scan_mesh", inputs)
            if created:
                new.append(job_id)
        if not splat:
            if not has_capture:
                continue
            name = "splat_preview"
        elif splat.get("status") in ("lidar", "training"):
            name = "splat_train"
        else:
            continue
        job_id, created = enqueue(jobs, name, inputs)
        if created:
            new.append(job_id)
    for inputs, done in audio_inputs(vault.root):
        if not done:
            job_id, created = enqueue(jobs, "speech", inputs)
            if created:
                new.append(job_id)
    return new
