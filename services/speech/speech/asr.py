"""What was said, with word times: Qwen3-ASR (1.7B on the GPU, 0.6B on a CPU) and
Qwen3-ForcedAligner-0.6B, Apache-2.0 (``qwen-asr``).

On 2026-01 benchmarks Qwen3-ASR-1.7B beats Whisper large-v3 on Mandarin (Fleurs-zh CER 2.41 vs
4.09; WenetSpeech meeting 5.88 vs 19.11) and English (Fleurs-en 3.35 vs 4.08), and its aligner
places words within 33-53 ms (docs/speech.md). The two run one after the other, so on a machine
without a GPU only one is in memory at a time.

The aligner returns words (CJK: characters) without punctuation; ``punctuate`` puts the
transcript's punctuation back on the words before it.
"""

from __future__ import annotations

import gc
import os
import re
import unicodedata

import numpy as np

from .audio import RATE

MODELS = {"gpu": "Qwen/Qwen3-ASR-1.7B", "cpu": "Qwen/Qwen3-ASR-0.6B"}
ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"
LANGUAGES = ("Chinese", "English", "Cantonese", "Japanese", "Korean")


def _dtype(device: str):  # noqa: ANN202 - torch is imported late
    """bfloat16 on a GPU, float32 on a CPU; ``DAYCUBE_SPEECH_DTYPE=bfloat16`` runs the GPU's
    numbers on a CPU (the 1.7B model in 3.4 GB instead of 6.8)."""
    import torch

    wanted = os.environ.get("DAYCUBE_SPEECH_DTYPE")
    if wanted:
        return getattr(torch, wanted)
    return torch.bfloat16 if device.startswith("cuda") else torch.float32


def release() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


class Transcriber:
    def __init__(self, device: str, model: str | None = None) -> None:
        self.device = device
        self.name = (
            model or os.environ.get("DAYCUBE_ASR_MODEL") or MODELS["gpu" if device.startswith("cuda") else "cpu"]
        )
        self._model = None

    def __call__(self, wavs: list[np.ndarray]) -> list[tuple[str, str]]:
        """(language, text) per clip; language is "" for a clip with no speech."""
        from qwen_asr import Qwen3ASRModel

        if self._model is None:
            self._model = Qwen3ASRModel.from_pretrained(
                self.name,
                dtype=_dtype(self.device),
                device_map=self.device,
                max_inference_batch_size=4,
                max_new_tokens=2048,
            )
        results = self._model.transcribe(audio=[(w, RATE) for w in wavs], language=None)
        return [(r.language or "", (r.text or "").strip()) for r in results]

    def close(self) -> None:
        self._model = None
        release()


class Aligner:
    def __init__(self, device: str, model: str = ALIGNER) -> None:
        self.device, self.name = device, model
        self._model = None

    def __call__(
        self, wavs: list[np.ndarray], texts: list[str], languages: list[str]
    ) -> list[list[tuple[str, float, float]]]:
        """(unit, start, end) per clip: words, or characters for CJK text."""
        from qwen_asr import Qwen3ForcedAligner

        if self._model is None:
            self._model = Qwen3ForcedAligner.from_pretrained(
                self.name, dtype=_dtype(self.device), device_map=self.device
            )
        todo = [k for k, t in enumerate(texts) if t.strip()]
        out: list[list[tuple[str, float, float]]] = [[] for _ in texts]
        for k in range(0, len(todo), 4):
            batch = todo[k : k + 4]
            results = self._model.align(
                audio=[(wavs[i], RATE) for i in batch],
                text=[texts[i] for i in batch],
                language=[first_language(languages[i]) for i in batch],
            )
            for i, r in zip(batch, results, strict=True):
                out[i] = [(item.text, float(item.start_time), float(item.end_time)) for item in r]
        return out

    def close(self) -> None:
        self._model = None
        release()


def first_language(merged: str) -> str:
    for name in (merged or "").split(","):
        if name.strip() in LANGUAGES:
            return name.strip()
    return "English"


def word_language(word: str, detected: str) -> str:
    """A word's language from its script, among the languages detected in its piece: Han
    characters are Chinese (or Cantonese or Japanese when that was detected), Latin letters
    English, kana Japanese, Hangul Korean."""
    names = [n.strip() for n in (detected or "").split(",") if n.strip() in LANGUAGES]
    if any("\u3040" <= ch <= "\u30ff" for ch in word):
        return "Japanese"
    if any("\uac00" <= ch <= "\ud7af" for ch in word):
        return "Korean"
    if any("\u4e00" <= ch <= "\u9fff" for ch in word):
        for name in ("Cantonese", "Japanese", "Chinese"):
            if name in names:
                return name
        return "Chinese"
    if any("a" <= ch.lower() <= "z" for ch in word):
        return "English"
    return names[0] if names else "unknown"


def _plain(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", s).lower() if ch.isalnum())


def punctuate(text: str, units: list[tuple[str, float, float]]) -> list[dict]:
    """The aligner's units as words, each carrying the punctuation that follows it in ``text``.

    Units are found in order in the text, compared without case, spacing or punctuation; one that
    cannot be found keeps its own spelling."""
    words: list[dict] = []
    flat, where = [], []  # the text's letters and digits, and where each came from
    for k, ch in enumerate(unicodedata.normalize("NFKC", text)):
        if ch.isalnum():
            flat.append(ch.lower())
            where.append(k)
    flat_s = "".join(flat)
    norm = unicodedata.normalize("NFKC", text)
    cursor = 0
    for unit, start, end in units:
        key = _plain(unit)
        word = {"word": unit, "start": round(start, 3), "end": round(end, 3)}
        at = flat_s.find(key, cursor) if key else -1
        if at >= 0 and at - cursor < 40:
            first, last = where[at], where[at + len(key) - 1]
            if words and "_after" in words[-1]:
                gap = norm[words[-1]["_after"] : first]
                words[-1]["word"] += re.sub(r"\s+", "", gap)
            word["word"] = norm[first : last + 1]
            word["_after"] = last + 1
            cursor = at + len(key)
        words.append(word)
    if words and "_after" in words[-1]:
        words[-1]["word"] += re.sub(r"\s+", "", norm[words[-1]["_after"] :])
    for w in words:
        w.pop("_after", None)
    return words


def join(words: list[dict]) -> str:
    """Words as text: CJK runs without spaces, other words separated by one."""
    out = ""
    for w in words:
        token = w["word"]
        if out and not (_cjk(out[-1]) and _cjk(token[:1])) and not re.match(r"^[,.!?;:，。！？；：、)\]]", token):
            out += " "
        out += token
    return out


def _cjk(ch: str) -> bool:
    return bool(ch) and ("　" <= ch <= "鿿" or "가" <= ch <= "힯" or "＀" <= ch <= "￯")
