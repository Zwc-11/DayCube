"""Brush (github.com/ArthurBrussee/brush, Apache-2.0) trains the splats: a Rust trainer that reaches
the GPU through WebGPU (DX12 or Vulkan), so the RTX 5070 (Blackwell, sm_120) trains with no CUDA
toolkit and no compiler. gsplat's prebuilt Windows wheels stop at PyTorch 2.4 with CUDA 12.4, which
cannot run on sm_120.

The release is pinned by version and SHA-256 and unpacked once into a per-user tools folder
(``DAYCUBE_TOOLS``, else ``%LOCALAPPDATA%/DayCube/tools`` or ``~/.cache/daycube/tools``);
``DAYCUBE_BRUSH`` points at a Brush of your own instead. It runs headless (a dataset on the
command line opens no window), and its log says how far it is and how well it fits the frames it
holds out (PSNR).
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.3.0"
RELEASES = {
    "Windows": ("brush-app-x86_64-pc-windows-msvc.zip", "b68e3e9cf052d51bf3ee30776fa5a364de7f2ba13b58443128ff797bb7bcfcd6", "brush_app.exe"),
    "Linux": ("brush-app-x86_64-unknown-linux-gnu.tar.xz", "4f0f9a8785d1951c62df26aae247c02c5bba32b00f40b06df4e1c9b867399e20", "brush_app"),
}
URL = "https://github.com/ArthurBrussee/brush/releases/download/v{version}/{asset}"

EVAL = re.compile(r"Eval iter (\d+): PSNR ([\d.]+), ssim ([\d.]+)")
REFINE = re.compile(r"Refine iter (\d+), (\d+) splats")
STEP = re.compile(r"(\d+)/(\d+)")


def tools_dir() -> Path:
    if os.environ.get("DAYCUBE_TOOLS"):
        return Path(os.environ["DAYCUBE_TOOLS"])
    if platform.system() == "Windows" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "DayCube" / "tools"
    return Path.home() / ".cache" / "daycube" / "tools"


def locate() -> Path | None:
    """The Brush to run, if there is one yet."""
    own = os.environ.get("DAYCUBE_BRUSH")
    if own:
        return Path(own) if Path(own).is_file() else None
    release = RELEASES.get(platform.system())
    if release is None:
        return None
    found = list((tools_dir() / f"brush-{VERSION}").rglob(release[2]))
    return found[0] if found else None


def allowed() -> bool:
    """Whether scans may be trained without being asked: Brush is installed (the first
    ``daycube splat`` installs it), or ``DAYCUBE_SPLAT_TRAIN=1``. Nothing fetches it on its own."""
    return locate() is not None or os.environ.get("DAYCUBE_SPLAT_TRAIN") == "1"


def install(say: Callable[[str], None] = print) -> Path:
    """Download the pinned release, check its hash, unpack it; returns the executable."""
    found = locate()
    if found:
        return found
    system = platform.system()
    if system not in RELEASES:
        raise RuntimeError(f"No Brush release for {system}; set DAYCUBE_BRUSH to a Brush executable")
    asset, digest, exe = RELEASES[system]
    folder = tools_dir() / f"brush-{VERSION}"
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / asset
    url = URL.format(version=VERSION, asset=asset)
    say(f"Downloading Brush {VERSION} ({url})")
    with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as out:  # noqa: S310 - pinned https URL
        shutil.copyfileobj(response, out, length=1 << 20)
    h = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != digest:
        archive.unlink()
        raise RuntimeError(f"Brush download has SHA-256 {h.hexdigest()}, expected {digest}; not used")
    if asset.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(folder)
    else:
        with tarfile.open(archive) as t:
            t.extractall(folder, filter="data")
    archive.unlink()
    found = locate()
    if found is None:
        raise RuntimeError(f"{exe} not found in the Brush release")
    if system != "Windows":
        found.chmod(0o755)
    return found


@dataclass
class Progress:
    step: int = 0
    total: int = 0
    splats: int = 0
    psnr: list[tuple[int, float]] = field(default_factory=list)
    ssim: float | None = None
    seconds: float = 0.0
    lines: list[str] = field(default_factory=list)

    def read(self, line: str) -> None:
        if m := EVAL.search(line):
            self.psnr.append((int(m[1]), float(m[2])))
            self.ssim = float(m[3])
            self.step = max(self.step, int(m[1]))
        if m := REFINE.search(line):
            self.splats = int(m[2])
            # Brush 0.3.0 run headless prints no progress bar: its refine lines carry the step.
            self.step = max(self.step, int(m[1]) - 1)
            return
        if "INFO" in line:
            return  # log lines name files and sizes, not progress
        for m in STEP.finditer(line):
            done, total = int(m[1]), int(m[2])
            if total == self.total or (self.total == 0 and total >= 100):
                self.step, self.total = done, total


def train(
    dataset: Path,
    export: Path,
    *,
    steps: int = 30_000,
    max_resolution: int = 1440,
    eval_every_n_frames: int = 25,
    sh_degree: int = 3,
    max_splats: int = 4_000_000,
    log: Path | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    timeout_s: float = 6 * 3600,
) -> Progress:
    """Train on ``dataset`` (its transforms.json) and write ``export/splat.ply``."""
    exe = install()
    export.mkdir(parents=True, exist_ok=True)
    args = [
        str(exe), str(dataset),
        "--total-steps", str(steps),
        "--max-resolution", str(max_resolution),
        "--sh-degree", str(sh_degree),
        "--max-splats", str(max_splats),
        "--eval-split-every", str(eval_every_n_frames),
        "--eval-every", str(max(1000, steps // 10)),
        "--export-every", str(steps),
        "--export-path", str(export.resolve()),
        "--export-name", "splat.ply",
    ]  # fmt: skip
    env = {**os.environ, "RUST_LOG": os.environ.get("RUST_LOG", "info")}
    progress = Progress(total=steps)
    started = time.monotonic()
    stream = log.open("w", encoding="utf-8") if log else None
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(export))
        assert proc.stdout is not None
        buffer = b""
        last = 0.0
        while chunk := proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096):
            buffer += chunk
            # Progress bars redraw with carriage returns: either ends a line.
            *done, buffer = re.split(rb"[\r\n]", buffer)
            for raw in done:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                progress.read(line)
                if stream and not STEP.fullmatch(line) and "load_scene_img" not in line:
                    stream.write(line[:2000] + "\n")  # each image load dumps the dataset's file table (22 KB)
                progress.lines = (progress.lines + [line])[-40:]
            now = time.monotonic()
            if on_progress and now - last > 5:
                progress.seconds = now - started
                on_progress(progress)
                last = now
            if now - started > timeout_s:
                proc.kill()
                raise RuntimeError(f"Brush ran past {timeout_s / 3600:.0f} h; stopped")
        code = proc.wait()
    finally:
        if stream:
            stream.close()
    progress.seconds = time.monotonic() - started
    if code != 0 or not (export / "splat.ply").is_file():
        tail = "\n".join(progress.lines[-12:])
        raise RuntimeError(f"Brush exited with {code} and no splat.ply:\n{tail}")
    return progress
