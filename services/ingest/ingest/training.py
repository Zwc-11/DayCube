"""Training runs on this PC, for the Training page: one folder per run in ``vault/training``.

``run.json`` (the ``training`` contract) says what is trained, on what, and how it ended;
``metrics.jsonl`` gets one line per logged step, ``{"step": n, "t": ..., <metric>: number}``. Any
trainer can write them: DayCube's own (a scan's splats) through ``Run``, a research script with
``Run`` or by writing the same two files. ::

    with Run(vault_root, "revisit-predictor", "Revisit predictor, AEA sites 1-4",
             metrics={"loss": ("L1", "lower")}, steps_total=20_000) as run:
        for step in range(20_000):
            ...
            run.log(step, loss=float(loss))
        run.result["val_loss"] = best

A run still ``running`` whose files have not changed for ``STALE_S`` is reported as stalled: its
process ended without saying so.
"""

from __future__ import annotations

import json
import math
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import validate
from .vault import atomic_text, read_json

STALE_S = 900.0
SCHEMA = "daycube.training/1"
NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def folder(vault_root: Path) -> Path:
    return vault_root / "training"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Run:
    """One run, written as it goes. Used as a context manager, an exception marks it failed."""

    def __init__(self, vault_root: Path, model: str, title: str, *, metrics: dict[str, tuple[str, str | None]],
                 steps_total: int | None = None, device: str | None = None, source: str | None = None) -> None:  # fmt: skip
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        self.id = NAME.sub("-", f"{model}-{stamp}-{time.monotonic_ns() % 10**6:06d}")
        self.dir = folder(vault_root) / self.id
        self.dir.mkdir(parents=True, exist_ok=False)
        self.record: dict[str, Any] = {
            "schema": SCHEMA, "id": self.id, "model": model, "title": title, "started": _now(), "ended": None,
            "status": "running", "steps_total": steps_total, "device": device, "source": source,
            "metrics": {name: {"unit": unit, "better": better} for name, (unit, better) in metrics.items()},
            "result": {}, "error": None,
        }  # fmt: skip
        self.result: dict[str, float | str | None] = self.record["result"]
        self._save()
        self._lines = (self.dir / "metrics.jsonl").open("a", encoding="utf-8")

    def _save(self) -> None:
        validate("training", self.record)
        atomic_text(self.dir / "run.json", json.dumps(self.record, indent=2))

    def log(self, step: int, **values: float) -> None:
        """One step's measurements; names outside the declared metrics, and values that are not
        finite numbers, are left out."""
        kept = {k: float(v) for k, v in values.items() if k in self.record["metrics"] and math.isfinite(v)}
        if kept:
            self._lines.write(json.dumps({"step": int(step), "t": _now(), **kept}) + "\n")
            self._lines.flush()

    def finish(self, status: str = "done", error: str | None = None) -> None:
        self._lines.close()
        self.record.update(status=status, ended=_now(), error=error)
        self._save()

    def __enter__(self) -> Run:
        return self

    def __exit__(self, kind: type | None, error: BaseException | None, _: object) -> None:
        if self.record["status"] != "running":
            return
        if kind is None:
            self.finish()
        else:
            self.finish("failed", f"{kind.__name__}: {error}")


def _stalled(run: dict, files: list[Path], now: float) -> bool:
    newest = max((f.stat().st_mtime for f in files if f.exists()), default=0.0)
    return run["status"] == "running" and now - newest > STALE_S


def runs(vault_root: Path) -> list[dict]:
    """Every run, newest first, each with its last logged line and whether it stalled."""
    out, now = [], time.time()
    for path in folder(vault_root).glob("*/run.json"):
        try:
            run = read_json(path)
            validate("training", run)
        except (OSError, ValueError):
            continue  # being written, or not a run
        lines = path.with_name("metrics.jsonl")
        out.append({**run, "last": _last_line(lines), "stalled": _stalled(run, [path, lines], now)})
    return sorted(out, key=lambda r: r["started"], reverse=True)


def _last_line(path: Path) -> dict | None:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, stream.seek(0, 2) - 4096))
            tail = stream.read().splitlines()
    except OSError:
        return None
    for raw in reversed(tail):
        try:
            return json.loads(raw)
        except ValueError:
            continue  # a line cut by the seek, or still being written
    return None


def series(vault_root: Path, run_id: str) -> dict | None:
    """A run and its metrics as columns (what charts take): ``steps`` and, per metric, its values
    (null where a line did not log it). None when there is no such run."""
    if NAME.search(run_id):
        return None
    path = folder(vault_root) / run_id / "run.json"
    try:
        run = read_json(path)
    except (OSError, ValueError):
        return None
    names = list(run["metrics"])
    steps: list[int] = []
    values: dict[str, list[float | None]] = {name: [] for name in names}
    lines = path.with_name("metrics.jsonl")
    if lines.exists():
        for raw in lines.read_text(encoding="utf-8").splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            steps.append(line["step"])
            for name in names:
                values[name].append(line.get(name))
    stalled = _stalled(run, [path, lines], time.time())
    return {"run": run, "stalled": stalled, "steps": steps, "values": values}
