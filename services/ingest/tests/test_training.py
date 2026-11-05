"""Training runs (ingest/training.py) and their endpoints, for the Training page."""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient
from ingest.contracts import validate
from ingest.training import STALE_S, Run, runs, series
from ingest.vault import Vault


def test_a_run_records_its_steps_and_how_it_ended(vault: Vault) -> None:
    with Run(vault.root, "revisit-predictor", "Revisit predictor", metrics={"loss": ("L1", "lower")},
             steps_total=3, device="RTX 5070") as run:  # fmt: skip
        run.log(0, loss=1.5)
        run.log(1, loss=float("nan"), other=9.0)  # nothing it can keep: no line
        run.log(2, loss=0.75, other=9.0)
        run.result["val_loss"] = 0.8
    record = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
    validate("training", record)
    assert record["status"] == "done" and record["ended"] and record["result"] == {"val_loss": 0.8}
    lines = [json.loads(line) for line in (run.dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(line["step"], line["loss"]) for line in lines] == [(0, 1.5), (2, 0.75)]
    assert all("other" not in line for line in lines)


def test_an_exception_marks_the_run_failed(vault: Vault) -> None:
    with pytest.raises(RuntimeError), Run(vault.root, "splat", "3D", metrics={"psnr_db": ("dB", "higher")}) as run:
        raise RuntimeError("out of memory")
    record = json.loads((run.dir / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed" and record["error"] == "RuntimeError: out of memory"


def test_runs_newest_first_with_the_last_line_and_stalls(vault: Vault) -> None:
    old = Run(vault.root, "splat", "First", metrics={"psnr_db": ("dB", "higher"), "splats": ("splats", None)})
    old.log(3000, psnr_db=21.0)
    time.sleep(1.1)  # started a second later
    new = Run(vault.root, "splat", "Second", metrics={"psnr_db": ("dB", "higher")})
    new.finish()
    listed = runs(vault.root)
    assert [r["title"] for r in listed] == ["Second", "First"]
    assert listed[1]["last"]["psnr_db"] == 21.0 and listed[0]["last"] is None
    assert not listed[1]["stalled"]
    # The process of the first ended without a word: its files stopped changing.
    stale = time.time() - STALE_S - 60
    for name in ("run.json", "metrics.jsonl"):
        os.utime(old.dir / name, (stale, stale))
    assert runs(vault.root)[1]["stalled"] and not runs(vault.root)[0]["stalled"]  # a finished run never stalls
    (vault.root / "training" / "junk").mkdir()
    (vault.root / "training" / "junk" / "run.json").write_text("{not json", encoding="utf-8")
    assert len(runs(vault.root)) == 2


def test_series_are_columns_and_ids_stay_in_the_folder(vault: Vault) -> None:
    run = Run(vault.root, "splat", "3D", metrics={"psnr_db": ("dB", "higher"), "splats": ("splats", None)})
    run.log(100, splats=5000)
    run.log(3000, psnr_db=20.5, splats=90_000)
    got = series(vault.root, run.id)
    assert got["steps"] == [100, 3000]
    assert got["values"] == {"psnr_db": [None, 20.5], "splats": [5000.0, 90_000.0]}
    assert series(vault.root, "../" + run.id) is None and series(vault.root, "nothing") is None


def test_endpoints(client: TestClient, vault: Vault) -> None:
    assert client.get("/training").json() == []
    run = Run(vault.root, "splat", "3D", metrics={"psnr_db": ("dB", "higher")})
    run.log(3000, psnr_db=19.0)
    listed = client.get("/training").json()
    assert [r["id"] for r in listed] == [run.id] and listed[0]["status"] == "running"
    assert client.get(f"/training/{run.id}").json()["values"] == {"psnr_db": [19.0]}
    assert client.get("/training/missing").status_code == 404


def test_splat_training_logs_each_evaluation_once(vault: Vault) -> None:
    from ingest.splat.brush import Progress
    from ingest.splat.run import training_progress

    run = Run(vault.root, "splat", "3D", metrics={"psnr_db": ("dB", "higher"), "splats": ("splats", None)})
    said: list[str] = []
    progress = training_progress(run, said.append)
    report = Progress(step=1200, total=30_000, splats=40_000)
    progress(report)
    report.step, report.splats, report.psnr = 3000, 90_000, [(3000, 20.25)]
    progress(report)
    report.step = 4000
    progress(report)  # no new evaluation: PSNR is not written again
    got = series(vault.root, run.id)
    assert got["steps"] == [1200, 3000, 3000, 4000]
    assert got["values"]["psnr_db"] == [None, 20.25, None, None]
    assert said[-1].startswith("Training 4,000/30,000 steps, 90,000 splats") and said[-1].endswith("PSNR 20.2 dB")
