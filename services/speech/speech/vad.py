"""Where people speak (Silero VAD): regions in seconds, pauses shorter than ``min_gap_s`` bridged;
and the pieces the recogniser is given, which never change language."""

from __future__ import annotations

import numpy as np

from .audio import RATE

_model = None


def stamps(audio: np.ndarray, *, min_speech_s: float = 0.25) -> list[tuple[float, float]]:
    """Silero's speech stamps, split at pauses of 300 ms: the units whose language is told."""
    global _model
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    if _model is None:
        _model = load_silero_vad()
    found = get_speech_timestamps(
        torch.from_numpy(audio),
        _model,
        sampling_rate=RATE,
        return_seconds=True,
        min_speech_duration_ms=int(min_speech_s * 1000),
        min_silence_duration_ms=300,
        speech_pad_ms=150,
    )
    return [(round(float(s["start"]), 3), round(float(s["end"]), 3)) for s in found]


def merge(found: list[tuple[float, float]], *, min_gap_s: float = 0.6) -> list[tuple[float, float]]:
    """Stamps with pauses shorter than ``min_gap_s`` bridged."""
    merged: list[list[float]] = []
    for start, end in found:
        if merged and start - merged[-1][1] < min_gap_s:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def regions(audio: np.ndarray, *, min_gap_s: float = 0.6, min_speech_s: float = 0.25) -> list[tuple[float, float]]:
    return merge(stamps(audio, min_speech_s=min_speech_s), min_gap_s=min_gap_s)


def by_language(
    found: list[tuple[float, float]], languages: list[str], *, max_s: float = 170.0, reliable_s: float = 1.0
) -> list[tuple[float, float, str]]:
    """Pieces that never change language, as (start, end, language): runs of stamps of one
    language, each cut into pieces of at most ``max_s``.

    Qwen3-ASR-1.7B given a piece in two languages transcribes one and drops the other (English then
    Mandarin, or Mandarin then English: only the English came back, 2026-10-25). A stamp shorter
    than ``reliable_s``, or with no language told, takes the language of the stamps before it (the
    first ones: of the first stamp told)."""
    told = [lang if (b - a) >= reliable_s and lang not in ("", "nospeech", "unknown") else None
            for (a, b), lang in zip(found, languages, strict=True)]  # fmt: skip
    first = next((lang for lang in told if lang), "")
    runs: list[tuple[str, list[tuple[float, float]]]] = []
    current = first
    for span, lang in zip(found, told, strict=True):
        current = lang or current
        if runs and runs[-1][0] == current:
            runs[-1][1].append(span)
        else:
            runs.append((current, [span]))
    return [(a, b, lang) for lang, spans in runs for a, b in chunks(spans, max_s=max_s)]


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
