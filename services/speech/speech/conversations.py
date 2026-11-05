"""Conversations in an hour of sound (DayCube Capture records all day): stretches of speech with
less than ``GAP_S`` of silence between them, kept when they hold at least ``MIN_SPEECH_S`` of
speech, each padded by ``PAD_S``. The receiver makes a talk moment of each, and each gets its
speech job.

Silero VAD finds the speech. It runs on a CPU at hundreds of times real time, so this job does not
wait for the GPU.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import vad
from .audio import RATE, load

GAP_S = 120.0
MIN_SPEECH_S = 8.0
PAD_S = 1.0


def find(
    audio: np.ndarray, *, gap_s: float = GAP_S, min_speech_s: float = MIN_SPEECH_S, pad_s: float = PAD_S
) -> list[dict]:
    """Conversations as ``{start_s, end_s, speech_s}``, in order."""
    duration = len(audio) / RATE
    groups: list[list[tuple[float, float]]] = []
    for start, end in vad.regions(audio):
        if groups and start - groups[-1][-1][1] < gap_s:
            groups[-1].append((start, end))
        else:
            groups.append([(start, end)])
    out = []
    for group in groups:
        speech = sum(b - a for a, b in group)
        if speech >= min_speech_s:
            out.append({"start_s": round(max(0.0, group[0][0] - pad_s), 3), "end_s": round(min(duration, group[-1][1] + pad_s), 3),
                        "speech_s": round(speech, 1)})  # fmt: skip
    return out


def run_job(vault_root: Path, inputs: dict) -> dict:
    """The ``conversations`` job: ``inputs["audio"]`` is the hour's path in the vault."""
    audio = load(vault_root / inputs["audio"])
    found = find(audio)
    return {"spans": [[c["start_s"], c["end_s"]] for c in found], "speech_s": [c["speech_s"] for c in found],
            "duration_s": round(len(audio) / RATE, 1)}  # fmt: skip
