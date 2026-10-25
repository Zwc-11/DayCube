"""A check of the whole pipeline on a clip with known words, speakers and times.

Two published sample clips, an English one (FunASR's SenseVoice example) and a Mandarin one
(Qwen3-ASR's example), are joined as A, B, A, B with a second of silence between them. The check:

- **Words.** English word error rate and Mandarin character error rate against their published
  transcripts.
- **Speakers.** Four turns from two speakers, in the order A B A B.
- **Times.** Each clip's second turn starts as long after its first as the clip was placed later,
  to within ``TIME_TOLERANCE_S``.
"""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .audio import RATE, load
from .pipeline import Models, transcribe

CLIPS = {
    "en": ("https://huggingface.co/FunAudioLLM/SenseVoiceSmall/resolve/main/example/en.mp3",
           "the tribal chieftain called for the boy and presented him with fifty pieces of gold"),
    "zh": ("https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_zh.wav", "甚至出现交易几乎停滞的情况"),
}  # fmt: skip
LIMITS = {"en_wer": 0.15, "zh_cer": 0.10}
TIME_TOLERANCE_S = 0.15
GAP_S = 1.0


def _cache() -> Path:
    folder = Path.home() / ".cache" / "daycube-speech" / "canary"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def fetch() -> dict[str, np.ndarray]:
    out = {}
    for key, (url, _) in CLIPS.items():
        path = _cache() / f"{key}{Path(url).suffix}"
        if not path.is_file():
            urllib.request.urlretrieve(url, path)  # noqa: S310 - fixed https URLs
        out[key] = load(path)
    return out


def build(clips: dict[str, np.ndarray]) -> tuple[np.ndarray, list[tuple[str, float]]]:
    """A, B, A, B and where each clip starts."""
    silence = np.zeros(int(GAP_S * RATE), np.float32)
    parts, placed, t = [], [], 0.0
    for key in ("en", "zh", "en", "zh"):
        placed.append((key, t))
        parts += [clips[key], silence]
        t += (len(clips[key]) + len(silence)) / RATE
    return np.concatenate(parts), placed


def edit_distance(a: list[str], b: list[str]) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def wer(said: str, truth: str) -> float:
    a, b = re.findall(r"[a-z']+", said.lower()), truth.split()
    return edit_distance(a, b) / len(b)


def cer(said: str, truth: str) -> float:
    a = [ch for ch in said if "一" <= ch <= "鿿"]
    return edit_distance(a, list(truth)) / len(truth)


def run(device: str | None = None, say: Callable[[str], None] = print) -> dict:
    audio, placed = build(fetch())
    result = transcribe(audio, Models(device), say)
    turns = result["transcript"]["segments"]
    report: dict = {
        "turns": [(t["speaker"], round(t["start"], 2), t["text"]) for t in turns],
        "timing_s": result["speech"]["timing_s"],
    }
    if len(turns) != 4:
        return {**report, "ok": False, "why": f"{len(turns)} turns, not 4"}
    speakers = [t["speaker"] for t in turns]
    report["speakers_ok"] = speakers[0] == speakers[2] and speakers[1] == speakers[3] and speakers[0] != speakers[1]
    report["en_wer"] = round(np.mean([wer(turns[k]["text"], CLIPS["en"][1]) for k in (0, 2)]), 3)
    report["zh_cer"] = round(np.mean([cer(turns[k]["text"], CLIPS["zh"][1]) for k in (1, 3)]), 3)
    shifts = []
    for first, second in ((0, 2), (1, 3)):
        expected = placed[second][1] - placed[first][1]
        shifts.append(abs((turns[second]["start"] - turns[first]["start"]) - expected))
    report["time_error_s"] = round(max(shifts), 3)
    report["ok"] = bool(
        report["speakers_ok"]
        and report["en_wer"] <= LIMITS["en_wer"]
        and report["zh_cer"] <= LIMITS["zh_cer"]
        and report["time_error_s"] <= TIME_TOLERANCE_S
    )
    return report
