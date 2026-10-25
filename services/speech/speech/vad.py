"""Where people speak (Silero VAD): regions in seconds, pauses shorter than ``min_gap_s`` bridged."""

from __future__ import annotations

import numpy as np

from .audio import RATE

_model = None


def regions(audio: np.ndarray, *, min_gap_s: float = 0.6, min_speech_s: float = 0.25) -> list[tuple[float, float]]:
    global _model
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    if _model is None:
        _model = load_silero_vad()
    stamps = get_speech_timestamps(
        torch.from_numpy(audio),
        _model,
        sampling_rate=RATE,
        return_seconds=True,
        min_speech_duration_ms=int(min_speech_s * 1000),
        min_silence_duration_ms=300,
        speech_pad_ms=150,
    )
    merged: list[list[float]] = []
    for s in stamps:
        start, end = float(s["start"]), float(s["end"])
        if merged and start - merged[-1][1] < min_gap_s:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def chunks(spans: list[tuple[float, float]], max_s: float = 170.0) -> list[tuple[float, float]]:
    """Consecutive regions grouped into pieces of at most ``max_s`` (the aligner takes 180 s);
    a region longer than that is cut."""
    out: list[list[float]] = []
    for start, end in spans:
        while end - start > max_s:
            out.append([start, start + max_s])
            start += max_s
        if out and end - out[-1][0] <= max_s:
            out[-1][1] = end
        else:
            out.append([start, end])
    return [(a, b) for a, b in out]
