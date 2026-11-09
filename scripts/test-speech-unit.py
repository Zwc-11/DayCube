"""Run speech's deterministic unit tests in the core environment, without model downloads."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = str(root / "services" / "speech")
    sys.path.insert(0, source)
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (source, os.environ.get("PYTHONPATH"))))
    os.environ["DAYCUBE_SPEECH_CANARY"] = "0"
    return pytest.main(["-q", str(root / "services" / "speech" / "tests")])


if __name__ == "__main__":
    raise SystemExit(main())
