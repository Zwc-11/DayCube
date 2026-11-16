import json
import shutil
import subprocess
from pathlib import Path


def executable(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise RuntimeError(f"Install FFmpeg and put {name} on PATH; see docs/runbook.md")
    return binary


def probe(path: Path) -> dict:
    path = path.resolve(strict=True)
    result = subprocess.run(
        [executable("ffprobe"), "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)
