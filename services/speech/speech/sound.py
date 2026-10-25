"""How it sounded: emotion per turn (emotion2vec+ large), sound events over the whole recording
(SenseVoice-Small: laughter, music, applause, coughing, crying, sneezing, breathing), and filler
words (语助词 and English fillers) counted from the transcript."""

from __future__ import annotations

import re

import numpy as np

from .audio import RATE

EVENT_WINDOW_S = 10.0
EVENTS = {"Laughter": "laughter", "BGM": "music", "Applause": "applause", "Cough": "cough", "Cry": "crying",
          "Sneeze": "sneeze", "Breath": "breath"}  # fmt: skip
TAG = re.compile(r"<\|([^|]+)\|>")

# Fillers: Mandarin interjections and particles used as hesitation, and English hesitation words.
# Particles like 吧/呢/嘛 end sentences as much as they fill: counted, and kept apart from pauses (嗯/呃).
FILLERS_ZH = ("嗯", "呃", "额", "啊", "哦", "噢", "哎", "唉", "吧", "呢", "嘛", "呀", "哈", "那个", "就是", "然后")
FILLERS_EN = ("um", "umm", "uh", "uhh", "er", "erm", "hmm", "mm", "like", "you know", "i mean", "kind of", "sort of")


class Emotion:
    def __init__(self, device: str) -> None:
        from funasr import AutoModel

        self.model = AutoModel(model="emotion2vec/emotion2vec_plus_large", hub="hf", device=device,
                               disable_update=True, disable_pbar=True, log_level="ERROR")  # fmt: skip

    def __call__(self, wavs: list[np.ndarray], batch: int = 16) -> list[dict]:
        out = []
        for k in range(0, len(wavs), batch):
            for r in self.model.generate(input=wavs[k : k + batch], granularity="utterance", extract_embedding=True):
                names = [str(label).split("/")[-1] for label in r["labels"]]
                scores = {n: round(float(s), 4) for n, s in zip(names, r["scores"], strict=True) if n != "<unk>"}
                out.append({"label": max(scores, key=scores.get), "scores": scores,
                            "embedding": np.asarray(r["feats"], np.float32).reshape(-1)})  # fmt: skip
        return out


class Events:
    def __init__(self, device: str) -> None:
        from funasr import AutoModel

        self.model = AutoModel(model="FunAudioLLM/SenseVoiceSmall", hub="hf", device=device, disable_update=True,
                               disable_pbar=True, log_level="ERROR")  # fmt: skip

    def __call__(self, audio: np.ndarray, batch: int = 16) -> list[dict]:
        """Sound events in 10 s windows over the whole recording, with SenseVoice's language and
        emotion tags for the window."""
        step = int(EVENT_WINDOW_S * RATE)
        starts = list(range(0, max(1, len(audio) - RATE // 2), step))
        out = []
        for k in range(0, len(starts), batch):
            group = starts[k : k + batch]
            wavs = [audio[s : s + step] for s in group]
            for s, r in zip(group, self.model.generate(input=wavs, language="auto", use_itn=False), strict=True):
                tags = TAG.findall(r.get("text", ""))
                events = [EVENTS[t] for t in tags if t in EVENTS]
                if events:
                    out.append({"start": round(s / RATE, 2), "end": round(min(len(audio), s + step) / RATE, 2),
                                "events": events, "tags": tags})  # fmt: skip
        return out


def fillers(words: list[dict]) -> dict[str, int]:
    """Filler words and particles in a run of words, by kind."""
    counts: dict[str, int] = {}
    text = " ".join(w["word"] for w in words).lower()
    latin = re.findall(r"[a-z']+", text)
    joined = " ".join(latin)
    for f in FILLERS_EN:
        n = len(re.findall(rf"(?<![a-z']){re.escape(f)}(?![a-z'])", joined))
        if n:
            counts[f] = n
    cjk = "".join(w["word"] for w in words)
    for f in FILLERS_ZH:
        n = cjk.count(f)
        if n:
            counts[f] = n
    return counts
