"""Assemble the desktop app's resources in apps/desktop/build/resources (tauri.bundle.json maps them).

    runtime/core/   CPython 3.11 + the receiver's locked packages (with the API's), daycube.pth
    runtime/gaze/   CPython 3.11 + the eye tracker's locked packages (numpy<2 apart from the rest)
    runtime/uv.exe  installs the GPU packs (speech, recon) from their own lockfiles on request
    src/            the Python sources, schemas, venues, the sample day, and each pack's lockfile
    viewer/         the built viewer (npm run build first)
    extension/      the Opera extension, to load unpacked

The interpreters are uv's managed CPython builds (python-build-standalone), which run from any
folder. Packages come from ``uv export --locked`` with hashes, so they match the repository's
environments exactly, less what the app never runs (``Environment``). The sources stay plain
files found through a relative .pth, so the Python code keeps its PROJECT_ROOT layout and nothing
is installed into the vault or the system.

Run on the platform being built (Windows for the installer): ``npm run bundle -w @daycube/desktop``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
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
# In every environment: test suites, type stubs, C sources and headers, link libraries, IDLE, and
# the installers python-build-standalone ships (the packs are installed by uv).
UNUSED = ["**/tests", "**/test", "**/*.pyi", "**/*.pxd", "**/*.pyx", "**/*.[ch]", "**/*.[ch]pp", "**/*.lib",
          "idlelib", "ensurepip", "site-packages/pip", "site-packages/pip-*", "site-packages/setuptools",
          "site-packages/setuptools-*", "site-packages/_distutils_hack", "site-packages/distutils-precedence.pth"]  # fmt: skip
NO_PLOTLY = '''"""Open3D's Plotly viewer: not in DayCube's bundle, which leaves out Plotly and Dash."""


def get_plotly_fig(*args, **kwargs):
    raise ImportError("Open3D's Plotly viewer is not part of the DayCube app")


draw_plotly = draw_plotly_server = get_plotly_fig
'''


@dataclass(frozen=True)
class Environment:
    """One bundled interpreter: a locked project with its extras, the DayCube sources on its path,
    and a check run in isolated mode once it is assembled."""

    project: str
    extras: tuple[str, ...]
    sources: tuple[str, ...]
    check: str
    prune: tuple[str, ...] = ()  # locked packages the app never imports, with what only they need
    drop: tuple[str, ...] = ()  # files in site-packages the app never loads (globs)
    replace: dict[str, str] = field(default_factory=dict)  # a site-packages file and its text here
    read_as_text: tuple[str, ...] = ()  # .py files a package reads itself, kept beside their bytecode


ENVIRONMENTS = {
    "core": Environment(
        "services/capture-win",
        ("desktop", "windows"),
        ("services/ingest", "services/capture-win"),
        # The API, the receiver, meaning search, and Open3D's geometry as the mesh stage uses it.
        check="import ingest.desktop, capture.__main__, uvicorn, fastembed, open3d as o3d\n"
        "scene = o3d.t.geometry.RaycastingScene()\n"
        "scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(o3d.geometry.TriangleMesh.create_box()))\n"
        "d = scene.compute_distance(o3d.core.Tensor([[0.5, 0.5, 2.0]], o3d.core.float32)).numpy()[0]\n"
        "assert abs(d - 1.0) < 1e-4, d",
        # Walks rebuilt from images run in the 3D pack, which has its own COLMAP. Open3D's notebook
        # and web viewers (Plotly, Dash, Flask, Jupyter widgets: about 180 MB) are never opened:
        # the one module of theirs Open3D imports is replaced by NO_PLOTLY.
        prune=("pycolmap", "dash", "flask", "werkzeug", "ipywidgets", "nbformat", "configargparse"),
        replace={"open3d/visualization/draw_plotly.py": NO_PLOTLY},
        # Open3D's renderer assets, its PyTorch and TensorFlow ops, Open3D-ML, examples and notebook extensions.
        drop=(
            "open3d/resources",
            "open3d/*torch_ops*",
            "open3d/*tf_ops*",
            "open3d/_ml3d",
            "open3d/examples",
            "open3d/labextension",
            "open3d/nbextension",
        ),
    ),
    "gaze": Environment(
        "services/gaze",
        (),
        ("services/gaze",),
        check="import gaze.track, eyetrax.gaze, mediapipe.tasks.python.vision, cv2, sklearn.linear_model",
        prune=("opencv-python",),  # opencv-contrib-python, MediaPipe's, is the same cv2 with more in it
        read_as_text=("cv2/config*.py",),  # cv2/__init__.py executes its loader settings from source
    ),
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


def stdlib(env: Path) -> Path:
    return env / "Lib" if WINDOWS else next((env / "lib").glob("python3.*"))


def remove(paths: Iterable[Path]) -> None:
    for path in list(paths):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()


def copy_python(target: Path) -> None:
    """A clean copy of uv's managed CPython (python-build-standalone): relocatable as a folder."""
    run(uv(), "python", "install", PYTHON)
    # --system: the managed build itself, never an active virtual environment made from another.
    source = Path(run(uv(), "python", "find", "--managed-python", "--system", PYTHON).strip())
    home = source.parent if WINDOWS else source.parent.parent
    shutil.copytree(home, target, symlinks=True, ignore=shutil.ignore_patterns("EXTERNALLY-MANAGED", "__pycache__"))


def install_environment(name: str, out: Path) -> None:
    spec = ENVIRONMENTS[name]
    env = out / "runtime" / name
    copy_python(env)
    lock = out / f"{name}-requirements.txt"
    export = [uv(), "export", "--locked", "--project", ROOT / spec.project, "--no-dev", "--no-emit-project",
              "--no-emit-package", "daycube-ingest", "--format", "requirements-txt", "-o", lock]  # fmt: skip
    for extra in spec.extras:
        export += ["--extra", extra]
    for package in spec.prune:
        export += ["--prune", package]
    run(*export)
    run(uv(), "pip", "install", "--python", interpreter(env), "--break-system-packages", "--require-hashes",
        "--no-deps", "-r", lock, env={**os.environ, "UV_LINK_MODE": "copy"})  # fmt: skip
    lock.unlink()
    lib = stdlib(env)
    site = lib / "site-packages"
    remove(path for pattern in UNUSED for path in lib.glob(pattern))
    remove(path for pattern in spec.drop for path in site.glob(pattern))
    for path, text in spec.replace.items():
        (site / path).write_text(text, encoding="utf-8")
    # Relative lines in a .pth resolve against its own folder: the install can move.
    up = os.path.relpath(out, site)
    (site / "daycube.pth").write_text(
        "".join(f"{Path(up, 'src', s).as_posix()}\n" for s in spec.sources), encoding="utf-8"
    )
    bytecode_only(env, spec)
    run(interpreter(env), "-I", "-c", spec.check)  # isolated: only what the bundle itself provides


def bytecode_only(env: Path, spec: Environment) -> None:
    """Python's standard library and the third-party packages as bytecode alone, the way Python's
    own embeddable distribution ships its standard library: written now instead of at the first
    launch (about 7 s of the API's start on a cold install), and without the sources beside it,
    which no import reads (about 80 MB on Windows). Tracebacks there show the file and line without
    the line's text. DayCube's own sources stay sources (``precompile``)."""
    lib = stdlib(env)
    # Failures are files no Python 3 compiles (data in some packages): they are kept as they are.
    subprocess.run([str(interpreter(env)), "-I", "-m", "compileall", "-q", "-b", "-j", "0", str(lib)], check=False)
    keep = {path for pattern in spec.read_as_text for path in (lib / "site-packages").glob(pattern)}
    remove(source for source in lib.rglob("*.py") if source.with_suffix(".pyc").is_file() and source not in keep)
    remove(lib.rglob("__pycache__"))  # written by the runs above, for sources now gone


def precompile(out: Path) -> None:
    """Bytecode for DayCube's own sources, checked by hash so an edited file is still recompiled,
    and independent of file times, which installers do not always keep."""
    run(interpreter(out / "runtime" / "core"), "-I", "-m", "compileall", "-q", "-j", "0",
        "--invalidation-mode", "checked-hash", out / "src")  # fmt: skip


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
        precompile(OUT)
    for part in sorted(p for p in OUT.iterdir() if p.is_dir()):
        print(f"{part.name:10} {size(part) / 1e6:8.1f} MB")
    for name in ENVIRONMENTS:
        print(f"  {name:8} {size(OUT / 'runtime' / name) / 1e6:8.1f} MB")
    print(f"{'total':10} {size(OUT) / 1e6:8.1f} MB")


if __name__ == "__main__":
    main()
