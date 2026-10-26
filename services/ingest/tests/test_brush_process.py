"""Trainer process ownership with a tiny local Python stand-in; no Brush install, GPU or media."""

import subprocess
import sys
from pathlib import Path

import pytest
from ingest.splat import brush


def trainer(tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "synthetic_trainer.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "target = pathlib.Path(sys.argv[sys.argv.index('--export-path') + 1]) / 'splat.ply'\n" + body,
        encoding="utf-8",
    )
    monkeypatch.setattr(brush, "install", lambda: Path(sys.executable))
    return script


def test_a_silent_trainer_obeys_its_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = trainer(tmp_path, "time.sleep(1)\ntarget.write_bytes(b'synthetic')\n", monkeypatch)
    with pytest.raises(RuntimeError, match="ran past"):
        brush.train(script, tmp_path / "out", timeout_s=0.1)


def test_callback_failure_closes_and_reaps_the_owned_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = trainer(tmp_path, "print('1/1000', flush=True)\ntime.sleep(2)\n", monkeypatch)
    created: list[subprocess.Popen] = []
    popen = subprocess.Popen

    def tracked(*args: object, **kwargs: object) -> subprocess.Popen:
        proc = popen(*args, **kwargs)
        created.append(proc)
        return proc

    def failing(progress: brush.Progress) -> None:
        raise ValueError("synthetic callback failure")

    monkeypatch.setattr(brush.subprocess, "Popen", tracked)
    log = tmp_path / "progress.log"
    try:
        with pytest.raises(ValueError, match="synthetic callback"):
            brush.train(script, tmp_path / "out", on_progress=failing, log=log, timeout_s=30)
        assert len(created) == 1 and created[0].poll() is not None
        assert created[0].stdout is not None and created[0].stdout.closed
        log.unlink()  # Windows also verifies the failure released its log handle.
    finally:
        # The pre-fix reproduction must not leave its stand-in process running after the failure.
        for proc in created:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()


def test_long_output_keeps_a_bounded_diagnostic_tail_and_preserves_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = trainer(
        tmp_path,
        "print('x' * (2 << 20))\nprint('Eval iter 1000: PSNR 20.0, ssim 0.8')\ntarget.write_bytes(b'synthetic')\n",
        monkeypatch,
    )
    progress = brush.train(script, tmp_path / "out", steps=1000, timeout_s=30)
    assert progress.step == 1000 and progress.psnr[-1] == (1000, 20.0) and progress.ssim == 0.8
    assert all(len(line) <= 2000 for line in progress.lines)
