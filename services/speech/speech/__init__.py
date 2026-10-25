"""Speech in DayCube recordings (docs/speech.md).

The models (about 7 GB) are kept in the checkout's ``.cache/huggingface`` unless ``HF_HOME`` says
otherwise: beside the vault's drive, not the system drive."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface"))
