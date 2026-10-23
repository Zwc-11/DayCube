"""Reject metadata-only receiver distributions and accidental runtime-data packaging."""

from pathlib import Path
from subprocess import run
from tarfile import open as open_tar
from tempfile import TemporaryDirectory
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "services/capture-win"


def verify(names: set[str], expected: set[str]) -> None:
    missing = expected - names
    if missing:
        raise RuntimeError(f"Receiver distribution is missing: {sorted(missing)}")
    if any({"vault", ".venv", "__pycache__"}.intersection(Path(name).parts) for name in names):
        raise RuntimeError("Receiver distribution contains runtime data or caches")


def main() -> None:
    expected = {path.relative_to(PROJECT).as_posix() for path in (PROJECT / "capture").glob("*.py")}
    if "capture/__main__.py" not in expected:
        raise RuntimeError("Receiver source entry point is missing")
    with TemporaryDirectory(prefix="daycube-package-check-") as temporary:
        output = Path(temporary)
        run(["uv", "build", str(PROJECT), "--wheel", "--sdist", "--out-dir", str(output)], check=True, cwd=ROOT)
        with ZipFile(next(output.glob("*.whl"))) as wheel:
            verify(set(wheel.namelist()), expected)
        with open_tar(next(output.glob("*.tar.gz"))) as source:
            # A source distribution wraps its files in one project/version directory.
            verify({name.split("/", 1)[-1] for name in source.getnames()}, expected)
    print(f"Receiver wheel and source archive each contain {len(expected)} Python modules; no runtime data.")


if __name__ == "__main__":
    main()
