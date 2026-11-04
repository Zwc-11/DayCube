"""Assemble the desktop app's resources in apps/desktop/build/resources (tauri.bundle.json maps them).

    runtime/core/   CPython 3.11 + the receiver's locked packages (with the API's), daycube.pth
    runtime/gaze/   CPython 3.11 + the eye tracker's locked packages (numpy<2 apart from the rest)
    runtime/uv.exe  installs the GPU packs (speech, recon) from their own lockfiles on request
    src/            the Python sources, schemas, venues, the sample day, and each pack's lockfile
    viewer/         the built viewer (npm run build first)
    extension/      the Opera extension, to load unpacked

The interpreters are uv's managed CPython builds (python-build-standalone), which run from any
folder. Packages come from ``uv export --locked`` with hashes, so they match the repository's
environments exactly. The sources stay plain files found through a relative .pth, so the Python code
keeps its PROJECT_ROOT layout and nothing is installed into the vault or the system.

Run on the platform being built (Windows for the installer): ``npm run bundle -w @daycube/desktop``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "apps" / "desktop" / "build" / "resources"
PYTHON = "3.11"
WINDOWS = sys.platform == "win32"
SKIP = shutil.ignore_patterns(
    ".venv", "__pycache__", "*.pyc", "tests", "browser-tests", "*.egg-info", ".pytest_cache", ".ruff_cache"
)
# Source folders the Python code reads at run time through PROJECT_ROOT.
DATA = ["packages/schemas", "venues", "fixtures/sample-day"]
SERVICES = ["ingest", "capture-win", "gaze", "speech", "recon"]
# Which environment gets which locked project, extras, and source folders on its path.
ENVIRONMENTS = {
    "core": ("services/capture-win", ["desktop", "windows"], ["services/ingest", "services/capture-win"]),
    "gaze": ("services/gaze", [], ["services/gaze"]),
}


def run(*args: str | Path, **kw: object) -> str:
    print("+", " ".join(map(str, args)), flush=True)
    done = subprocess.run([str(a) for a in args], text=True, capture_output=True, **kw)
    if done.returncode != 0:
        print(done.stdout, done.stderr, sep="\n", file=sys.stderr)
        raise SystemExit(f"failed ({done.returncode}): {args[0]} {args[1] if len(args) > 1 else ''}")
    return done.stdout


def uv() -> str:
    found = shutil.which("uv")
    if not found:
        raise SystemExit("uv is not on PATH")
    return found


def interpreter(env: Path) -> Path:
    return env / "python.exe" if WINDOWS else env / "bin" / "python3"


def site_packages(env: Path) -> Path:
    if WINDOWS:
        return env / "Lib" / "site-packages"
    return next((env / "lib").glob("python3.*")) / "site-packages"


def copy_python(target: Path) -> None:
    """A clean copy of uv's managed CPython (python-build-standalone): relocatable as a folder."""
    run(uv(), "python", "install", PYTHON)
    source = Path(run(uv(), "python", "find", "--managed-python", PYTHON).strip())
    home = source.parent if WINDOWS else source.parent.parent
    shutil.copytree(home, target, symlinks=True, ignore=shutil.ignore_patterns("EXTERNALLY-MANAGED", "__pycache__"))
    for unused in ("Lib/test", "Lib/idlelib", "lib/python3.11/test"):
        shutil.rmtree(target / unused, ignore_errors=True)


def install_environment(name: str, out: Path) -> None:
    project, extras, sources = ENVIRONMENTS[name]
    env = out / "runtime" / name
    copy_python(env)
    lock = out / f"{name}-requirements.txt"
    export = [uv(), "export", "--locked", "--project", ROOT / project, "--no-dev", "--no-emit-project",
              "--no-emit-package", "daycube-ingest", "--format", "requirements-txt", "-o", lock]  # fmt: skip
    for extra in extras:
        export += ["--extra", extra]
    run(*export)
    run(uv(), "pip", "install", "--python", interpreter(env), "--break-system-packages", "--require-hashes",
        "--no-deps", "-r", lock, env={**os.environ, "UV_LINK_MODE": "copy"})  # fmt: skip
    lock.unlink()
    # Relative lines in a .pth resolve against its own folder: the install can move.
    site = site_packages(env)
    up = os.path.relpath(out, site)
    (site / "daycube.pth").write_text("".join(f"{Path(up, 'src', s).as_posix()}\n" for s in sources), encoding="utf-8")
    smoke = {"core": "import ingest.desktop, capture.__main__, uvicorn, fastembed", "gaze": "import gaze, eyetrax"}[
        name
    ]
    run(interpreter(env), "-I", "-c", smoke)  # isolated: only what the bundle itself provides


def copy_sources(out: Path) -> None:
    for service in SERVICES:
        shutil.copytree(ROOT / "services" / service, out / "src" / "services" / service, ignore=SKIP)
    for data in DATA:
        shutil.copytree(ROOT / data, out / "src" / data, ignore=SKIP)


def size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-runtimes", action="store_true", help="Sources, viewer and extension only")
    args = parser.parse_args()
    viewer = ROOT / "apps" / "viewer" / "dist"
    if not (viewer / "index.html").is_file():
        raise SystemExit("build the viewer first: npm run build")
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    copy_sources(OUT)
    shutil.copytree(viewer, OUT / "viewer")
    shutil.copytree(ROOT / "extensions" / "chrome", OUT / "extension", ignore=SKIP)
    if not args.skip_runtimes:
        for name in ENVIRONMENTS:
            install_environment(name, OUT)
        shutil.copy2(uv(), OUT / "runtime" / Path(uv()).name)
    for part in sorted(p for p in OUT.iterdir() if p.is_dir()):
        print(f"{part.name:10} {size(part) / 1e6:8.1f} MB")
    print(f"{'total':10} {size(OUT) / 1e6:8.1f} MB")


if __name__ == "__main__":
    main()
