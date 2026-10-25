"""update.cmd, run by Wine's cmd with git, uv, capture.cmd, ping and timeout stood in for: what it
runs in each case, and that it gets to the end. Skipped without Wine."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(shutil.which("wine") is None, reason="needs Wine")

STUBS = {
    "git.cmd": """@echo off
echo git %*>> "%CALLS%"
if "%1"=="rev-parse" (echo 1111111aaaa& exit /b 0)
if "%1"=="pull" (echo Updating 1111111..2222222& exit /b %GIT_PULL_RC%)
if "%1"=="log" (echo 2222222 a change& exit /b 0)
if "%1"=="diff" exit /b %GIT_DIFF_RC%
exit /b 0
""",
    "uv.cmd": """@echo off
echo uv %*>> "%CALLS%"
if "%1"=="sync" exit /b %UV_SYNC_RC%
if "%1"=="run" (echo {"ok": true}& exit /b %CANARY_RC%)
exit /b 0
""",
    "ping.cmd": '@echo off\necho ping %*>> "%CALLS%"\n',
    "timeout.cmd": '@echo off\necho timeout %*>> "%CALLS%"\n',
}
CAPTURE = """@echo off
echo capture %*>> "%CALLS%"
if "%1"=="doctor" echo pass  receiver          running
exit /b 0
"""


def crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode()


def run(
    root: Path, *, speech: bool = True, pull: int = 0, diff: int = 1, sync: int = 0, canary: int = 0
) -> tuple[list[str], str]:
    (root / "bin").mkdir(parents=True)
    (root / "update.cmd").write_bytes(crlf((ROOT / "update.cmd").read_text()))
    (root / "capture.cmd").write_bytes(crlf(CAPTURE))
    for name, body in STUBS.items():
        (root / "bin" / name).write_bytes(crlf(body))
    if speech:
        (root / "services" / "speech" / ".venv").mkdir(parents=True)
    win = lambda p: "Z:" + str(p).replace("/", "\\")  # noqa: E731
    env = {**os.environ, "WINEDEBUG": "-all", "WINEPATH": win(root / "bin"), "CALLS": win(root / "calls.log"),
           "GIT_PULL_RC": str(pull), "GIT_DIFF_RC": str(diff), "UV_SYNC_RC": str(sync), "CANARY_RC": str(canary)}  # fmt: skip
    subprocess.run(["wine", "cmd", "/c", win(root / "update.cmd")], env=env, capture_output=True, timeout=300)
    calls = []
    for line in (root / "calls.log").read_text(errors="replace").splitlines():
        words = line.split()
        if words[0] == "uv":
            calls.append("sync " + words[-1].split("\\")[-1] if words[1] == "sync" else "speech check")
        else:
            calls.append(" ".join(words[:2]))
    return calls, (root / "vault" / "logs" / "update.log").read_text(errors="replace")


START = ["git rev-parse", "git pull", "git log", "sync capture-win", "sync gaze"]
END = ["capture restart", "ping -n", "capture doctor", "timeout /t"]


@pytest.mark.parametrize(
    ("case", "options", "calls", "logged"),
    [
        ("speech changed", {}, [*START, "sync speech", "git diff", "speech check", *END], "speech check: ok"),
        ("speech unchanged", {"diff": 0}, [*START, "sync speech", "git diff", *END], "doctor"),
        ("no speech worker", {"speech": False}, [*START, *END], "doctor"),
        ("speech check fails", {"canary": 1}, [*START, "sync speech", "git diff", "speech check", *END],
         "speech check: FAILED"),
        ("pull fails", {"pull": 1}, ["git rev-parse", "git pull", "timeout /t"], "git pull failed"),
        ("sync fails", {"sync": 1}, [*START[:4], "timeout /t"], "sync of the receiver failed"),
    ],
)  # fmt: skip
def test_update_cmd(tmp_path: Path, case: str, options: dict, calls: list[str], logged: str) -> None:
    got, log = run(tmp_path / case.replace(" ", "-"), **options)
    assert got == calls
    assert logged in log and log.rstrip().endswith("done")
