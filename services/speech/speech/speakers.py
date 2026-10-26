"""Who spoke when: CAM++ speaker embeddings (FunASR ``funasr/campplus``, Apache-2.0) on 1.5 s windows
of speech every 0.75 s, clustered by cosine distance; each word takes the speaker of the windows
it falls in. Speakers are labelled S1, S2, ... by talking time, S1 the most.

No model here needs a sign-in (pyannote's community-1 is gated). Windows of the same speaker
score about 0.35 cosine, of different speakers -0.1 to 0.15 (the FunASR and Qwen3-ASR sample
clips, 2026-10-25), so clusters merge while their average similarity is above ``SAME``.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Callable

import numpy as np

from .audio import RATE

WINDOW_S = 1.5
HOP_S = 0.75
SAME = 0.2  # average cosine similarity above which two clusters are one speaker
MIN_SHARE = 0.04  # a cluster with fewer windows than this share joins its nearest speaker


class Embedder:
    def __init__(self, device: str) -> None:
        from funasr import AutoModel

        self.model = AutoModel(model="funasr/campplus", hub="hf", device=device, disable_update=True,
                               disable_pbar=True, log_level="ERROR")  # fmt: skip

    def __call__(self, wavs: list[np.ndarray], batch: int = 64) -> np.ndarray:
        out = []
        for k in range(0, len(wavs), batch):
            for r in self.model.generate(input=wavs[k : k + batch]):
                e = np.asarray(r["spk_embedding"].cpu() if hasattr(r["spk_embedding"], "cpu") else r["spk_embedding"])
                out.append(e.reshape(-1))
        e = np.stack(out).astype(np.float32)
        return e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-9)


def windows(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """1.5 s windows every 0.75 s over each speech region; a shorter region is one window."""
    out = []
    for start, end in spans:
        if end - start <= WINDOW_S:
            out.append((start, end))
            continue
        t = start
        while t + WINDOW_S <= end + 1e-6:
            out.append((t, t + WINDOW_S))
            t += HOP_S
        if out[-1][1] < end - 0.25:
            out.append((end - WINDOW_S, end))
    return out


def cluster(embeddings: np.ndarray, *, same: float = SAME, min_share: float = MIN_SHARE) -> np.ndarray:
    """Speaker index per window (0 = most windows)."""
    if len(embeddings) == 1:
        return np.zeros(1, int)
    from sklearn.cluster import AgglomerativeClustering

    labels = AgglomerativeClustering(
        n_clusters=None, metric="cosine", linkage="average", distance_threshold=1 - same
    ).fit_predict(embeddings)
    # Small clusters (a cough, an overlap, a noisy window) join the speaker they are most like.
    counts = np.bincount(labels)
    big = [c for c in range(len(counts)) if counts[c] >= max(2, min_share * len(labels))] or [int(counts.argmax())]
    centroids = np.stack([embeddings[labels == c].mean(0) for c in big])
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    for c in range(len(counts)):
        if c not in big:
            members = labels == c
            labels[members] = big[int(np.argmax(centroids @ embeddings[members].mean(0)))]
    order = {c: k for k, c in enumerate(sorted(big, key=lambda c: -int((labels == c).sum())))}
    return np.array([order[c] for c in labels])


def assign(words: list[dict], spans: list[tuple[float, float]], window_times: list[tuple[float, float]],
           labels: np.ndarray) -> list[int]:  # fmt: skip
    """Each word's speaker: the most common label of the windows overlapping it, else the nearest."""
    starts = np.array([a for a, _ in window_times])
    ends = np.array([b for _, b in window_times])
    mids = (starts + ends) / 2
    ordered = bool(
        len(mids)
        and np.isfinite(mids).all()
        and (starts < ends).all()
        and (starts[1:] >= starts[:-1]).all()
        and (ends[1:] >= ends[:-1]).all()
    )
    if ordered:
        # VAD windows are chronological. Search all word bounds once; only overlapping labels
        # need a vote. Keep the general scan for unordered or degenerate caller-supplied windows.
        left = np.searchsorted(ends, [w["start"] for w in words], side="right")
        right = np.searchsorted(starts, [w["end"] for w in words], side="left")
    out = []
    for k, w in enumerate(words):
        if ordered and w["start"] < w["end"] and math.isfinite(w["start"]) and math.isfinite(w["end"]):
            hit = labels[left[k] : right[k]]
        else:
            overlap = np.minimum(ends, w["end"]) - np.maximum(starts, w["start"])
            hit = labels[overlap > 0]
        if len(hit):
            out.append(int(np.bincount(hit).argmax()))
        else:
            middle = (w["start"] + w["end"]) / 2
            if ordered and math.isfinite(middle):
                at = bisect_left(mids, middle)
                nearest = min(at, len(mids) - 1)
                if at and abs(mids[at - 1] - middle) <= abs(mids[nearest] - middle):
                    nearest = at - 1
                nearest = bisect_left(mids, mids[nearest])  # equal distances/midpoints: first window
            else:
                nearest = int(np.argmin(np.abs(mids - middle)))
            out.append(int(labels[nearest]))
    return smooth(out, words)


def smooth(speakers: list[int], words: list[dict], min_s: float = 0.6) -> list[int]:
    """A run of words shorter than ``min_s`` between two runs of one other speaker is theirs."""
    runs: list[list[int]] = []
    for k, s in enumerate(speakers):
        if runs and speakers[runs[-1][0]] == s:
            runs[-1].append(k)
        else:
            runs.append([k])
    out = list(speakers)
    for r in range(1, len(runs) - 1):
        before, here, after = runs[r - 1], runs[r], runs[r + 1]
        length = words[here[-1]]["end"] - words[here[0]]["start"]
        if length < min_s and out[before[0]] == out[after[0]]:
            for k in here:
                out[k] = out[before[0]]
    return out


def diarize(audio: np.ndarray, spans: list[tuple[float, float]], words: list[dict],
            embedder: Callable[[list[np.ndarray]], np.ndarray]) -> tuple[list[int], dict]:  # fmt: skip
    """Speaker index per word, and per speaker its embedding (mean of its windows) and talking time."""
    if not words or not spans:
        return [], {}
    times = windows(spans)
    wavs = [audio[int(a * RATE) : int(b * RATE)] for a, b in times]
    keep = [k for k, w in enumerate(wavs) if len(w) >= int(0.4 * RATE)]
    times = [times[k] for k in keep]
    embeddings = embedder([wavs[k] for k in keep])
    labels = cluster(embeddings)
    per_word = assign(words, spans, times, labels)
    speakers = {}
    for s in sorted(set(per_word)):
        mine = labels == s
        centre = embeddings[mine].mean(0) if mine.any() else embeddings.mean(0)
        talk = sum(w["end"] - w["start"] for w, p in zip(words, per_word, strict=True) if p == s)
        speakers[s] = {"embedding": (centre / np.linalg.norm(centre)).astype(np.float32), "talk_s": round(talk, 1),
                       "windows": int(mine.sum())}  # fmt: skip
    return per_word, speakers
