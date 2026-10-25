"""Audio as 16 kHz mono float32, decoded by FFmpeg (phone files: AAC in M4A, CAF, ...)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

RATE = 16_000


def load(path: Path) -> np.ndarray:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("Install FFmpeg and put ffmpeg on PATH")
    raw = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "pipe:1"],
        capture_output=True,
        check=True,
        timeout=3600,
    ).stdout
    return np.frombuffer(raw, np.float32).copy()


def clip(audio: np.ndarray, start_s: float, end_s: float) -> np.ndarray:
    return audio[max(0, int(start_s * RATE)) : max(0, int(end_s * RATE))]
