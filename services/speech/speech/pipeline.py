"""One recording through every stage: where speech is (VAD), what was said and when (ASR, aligner),
who said it (speakers), how it sounded (emotion per turn, sound events, fillers).

Writes, for a vault moment:
- ``moments/<id>/transcript.json``: transcript.schema.json, one segment per turn of one speaker
  (``S1`` the one who talks most), words with times. A faster-whisper transcript there is kept as
  ``transcript.whisper.json`` the first time.
- ``moments/<id>/speech.json``: languages, speakers, per turn its emotion and fillers, sound events,
  the models and how long each stage took.
- ``vault/derived/speech/<date>/<id>/embeddings.npz``: each speaker's voice embedding (CAM++, 192)
  for recognising them across days, and each turn's emotion2vec+ embedding (1024) for training.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import asr as asr_mod
from . import vad
from .audio import RATE, clip, load
from .sound import Emotion, Events, fillers
from .speakers import Embedder, diarize

VERSION = 2  # 2: pieces of one language each
TURN_GAP_S = 1.5  # a pause this long starts a new segment, even for one speaker
TURN_MAX_S = 30.0  # past this, a segment ends at the next sentence end
EMOTION_MIN_S = 1.0
SENTENCE_END = tuple(".!?。！？")


def device() -> str:
    wanted = os.environ.get("DAYCUBE_SPEECH_DEVICE")
    if wanted:
        return wanted
    import torch

    return "cuda:0" if torch.cuda.is_available() else "cpu"


def _asr(where: str, name: str, wavs: list[np.ndarray]) -> list[tuple[str, str]]:
    return asr_mod.Transcriber(where, name)(wavs)


def _align(where: str, name: str, wavs: list[np.ndarray], texts: list[str], langs: list[str]) -> list:
    return asr_mod.Aligner(where, name)(wavs, texts, langs)


def _embed(where: str, wavs: list[np.ndarray]) -> np.ndarray:
    return Embedder(where)(wavs)


def _emotion(where: str, wavs: list[np.ndarray]) -> list[dict]:
    return Emotion(where)(wavs)


def _events(where: str, audio: np.ndarray) -> list[dict]:
    return Events(where)(audio)


def _languages(where: str, wavs: list[np.ndarray]) -> list[str]:
    return Events(where).languages(wavs)


def _apart(fn: Callable, *args: object) -> object:
    """``fn(*args)`` in a process of its own, so the model it loads is gone when it returns."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
        return pool.submit(fn, *args).result()


class Models:
    """The stages' models. On a GPU they load once and stay for the next recording. On a CPU each
    stage runs in a process of its own: freeing a transformers model inside a process gave back
    600 MB of its 3.2 GB (Qwen3-ASR-0.6B, 2026-10-25), and the next model then ran out of memory."""

    def __init__(self, where: str | None = None) -> None:
        self.device = where or device()
        self.keep = self.device.startswith("cuda")
        self.asr_name = os.environ.get("DAYCUBE_ASR_MODEL") or asr_mod.MODELS["gpu" if self.keep else "cpu"]
        self.aligner_name = asr_mod.ALIGNER
        self._loaded: dict[str, object] = {}

    def _get(self, key: str, make: Callable[[], object]) -> object:
        if key not in self._loaded:
            self._loaded[key] = make()
        return self._loaded[key]

    def asr(self, wavs: list[np.ndarray]) -> list[tuple[str, str]]:
        if not self.keep:
            return _apart(_asr, self.device, self.asr_name, wavs)  # type: ignore[return-value]
        return self._get("asr", lambda: asr_mod.Transcriber(self.device, self.asr_name))(wavs)  # type: ignore[operator]

    def align(self, wavs: list[np.ndarray], texts: list[str], langs: list[str]) -> list:
        if not self.keep:
            return _apart(_align, self.device, self.aligner_name, wavs, texts, langs)  # type: ignore[return-value]
        return self._get("aligner", lambda: asr_mod.Aligner(self.device, self.aligner_name))(wavs, texts, langs)  # type: ignore[operator]

    def embed(self, wavs: list[np.ndarray]) -> np.ndarray:
        if not self.keep:
            return _apart(_embed, self.device, wavs)  # type: ignore[return-value]
        return self._get("embedder", lambda: Embedder(self.device))(wavs)  # type: ignore[operator]

    def emotion(self, wavs: list[np.ndarray]) -> list[dict]:
        if not self.keep:
            return _apart(_emotion, self.device, wavs)  # type: ignore[return-value]
        return self._get("emotion", lambda: Emotion(self.device))(wavs)  # type: ignore[operator]

    def languages(self, wavs: list[np.ndarray]) -> list[str]:
        if not self.keep:
            return _apart(_languages, self.device, wavs)  # type: ignore[return-value]
        return self._get("events", lambda: Events(self.device)).languages(wavs)  # type: ignore[attr-defined]

    def events(self, audio: np.ndarray) -> list[dict]:
        if not self.keep:
            return _apart(_events, self.device, audio)  # type: ignore[return-value]
        return self._get("events", lambda: Events(self.device))(audio)  # type: ignore[operator]

    def names(self) -> dict:
        return {"vad": "silero-vad", "asr": self.asr_name, "aligner": self.aligner_name, "speakers": "funasr/campplus",
                "emotion": "emotion2vec/emotion2vec_plus_large", "events": "FunAudioLLM/SenseVoiceSmall"}  # fmt: skip


def segments(words: list[dict], speakers: list[int]) -> list[dict]:
    """Turns: consecutive words of one speaker, split at long pauses and, past 30 s, at sentence ends."""
    out: list[dict] = []
    for w, s in zip(words, speakers, strict=True):
        last = out[-1] if out else None
        if (
            last is None
            or last["speaker_index"] != s
            or w["start"] - last["words"][-1]["end"] > TURN_GAP_S
            or (w["start"] - last["start"] > TURN_MAX_S and last["words"][-1]["word"].endswith(SENTENCE_END))
        ):
            out.append({"speaker_index": s, "start": w["start"], "words": []})
        out[-1]["words"].append(w)
    for seg in out:
        seg["end"] = seg["words"][-1]["end"]
        seg["text"] = asr_mod.join(seg["words"])
    return out


def transcribe(audio: np.ndarray, models: Models, say: Callable[[str], None] = print) -> dict:
    """Everything about one recording's speech; see ``write`` for where it goes."""
    timing: dict[str, float] = {}
    clock = time.monotonic()

    def lap(name: str) -> None:
        nonlocal clock
        now = time.monotonic()
        timing[name] = round(now - clock, 2)
        clock = now

    duration = len(audio) / RATE
    found = vad.stamps(audio)
    spans = vad.merge(found)
    lap("vad")
    # Each piece in one language: the recogniser keeps one language of a piece and drops the rest.
    told = models.languages([clip(audio, a, b) for a, b in found]) if found else []
    cut = vad.by_language(found, told)
    pieces = [(a, b) for a, b, _ in cut]
    lap("language")
    runs = sum(1 for k in range(len(told)) if k == 0 or told[k] != told[k - 1])
    say(f"{sum(b - a for a, b in spans):.0f} s of speech in {duration:.0f} s, {len(pieces)} pieces"
        f" ({', '.join(sorted(set(told) - {'nospeech'})) or 'no language told'}, {runs} runs)")  # fmt: skip
    wavs = [clip(audio, a, b) for a, b in pieces]
    texts = models.asr(wavs) if wavs else []
    lap("asr")
    units = models.align(wavs, [t for _, t in texts], [lang for lang, _ in texts]) if wavs else []
    lap("align")
    words: list[dict] = []
    languages: dict[str, float] = {}
    for (a, _), (lang, text), u in zip(pieces, texts, units, strict=True):
        for w in asr_mod.punctuate(text, u):
            words.append({"word": w["word"], "start": round(a + w["start"], 3), "end": round(a + w["end"], 3),
                          "language": asr_mod.word_language(w["word"], lang)})  # fmt: skip
    for w in words:
        languages[w["language"]] = round(languages.get(w["language"], 0.0) + (w["end"] - w["start"]), 1)
    per_word, voices = diarize(audio, spans, words, models.embed) if words else ([], {})
    lap("speakers")
    turns = segments(words, per_word) if words else []
    say(f"{len(words)} words, {len(voices)} speakers, {len(turns)} turns")

    long_enough = [k for k, t in enumerate(turns) if t["end"] - t["start"] >= EMOTION_MIN_S]
    clips = [
        clip(audio, turns[k]["start"] - 0.1, min(turns[k]["end"] + 0.1, turns[k]["start"] + 30)) for k in long_enough
    ]
    feelings = models.emotion(clips) if clips else []
    lap("emotion")
    events = models.events(audio) if len(audio) else []
    lap("events")

    feeling_of = dict(zip(long_enough, feelings, strict=True))
    order = sorted(voices, key=lambda s: -voices[s]["talk_s"])
    label = {s: f"S{k + 1}" for k, s in enumerate(order)}
    transcript = {
        "segments": [
            {"start": t["start"], "end": t["end"], "text": t["text"], "speaker": label[t["speaker_index"]],
             "words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in t["words"]]}
            for t in turns
        ],
        "speaker_map": {},
    }  # fmt: skip
    speech = {
        "version": VERSION,
        "made": datetime.now(UTC).isoformat(timespec="seconds"),
        "device": models.device,
        "models": models.names(),
        "duration_s": round(duration, 2),
        "speech_s": round(sum(b - a for a, b in spans), 1),
        "languages": languages,  # seconds of words in each
        "speakers": [
            {"label": label[s], "talk_s": voices[s]["talk_s"], "turns": sum(1 for t in turns if t["speaker_index"] == s)}
            for s in order
        ],
        "turns": [
            {"segment": k, "speaker": label[t["speaker_index"]], "start": t["start"], "end": t["end"],
             "language": _turn_language(t["words"]),
             "emotion": {"label": feeling_of[k]["label"], "scores": feeling_of[k]["scores"]} if k in feeling_of else None,
             "fillers": fillers(t["words"])}
            for k, t in enumerate(turns)
        ],
        "events": events,
        # What the recogniser was given: each piece, the language SenseVoice told, the one it heard.
        "pieces": [{"start": a, "end": b, "told": lang, "heard": heard}
                   for (a, b, lang), (heard, _) in zip(cut, texts, strict=True)],
        "timing_s": timing,
    }  # fmt: skip
    embeddings = {
        "speaker_labels": np.array([label[s] for s in order]),
        "speakers": np.stack([voices[s]["embedding"] for s in order]) if order else np.zeros((0, 192), np.float32),
        "emotion_turns": np.array(long_enough, dtype=np.int32),
        "emotion": np.stack([f["embedding"] for f in feelings]) if feelings else np.zeros((0, 1024), np.float32),
    }
    return {"transcript": transcript, "speech": speech, "embeddings": embeddings}


def _turn_language(words: list[dict]) -> str:
    """The language most of a turn's speaking time is in."""
    time_in: dict[str, float] = {}
    for w in words:
        time_in[w["language"]] = time_in.get(w["language"], 0.0) + (w["end"] - w["start"])
    return max(time_in, key=time_in.get) if time_in else "unknown"


def moment_audio(moment: Path) -> Path:
    scene = json.loads((moment / "scene.json").read_text(encoding="utf-8"))
    name = scene.get("audio") or scene.get("video")
    if not name or not (moment / name).is_file():
        raise FileNotFoundError(f"{moment.name}: no audio file in the moment")
    return moment / name


def write(vault_root: Path, date: str, moment_id: str, result: dict) -> dict:
    moment = vault_root / date / "moments" / moment_id
    old = moment / "transcript.json"
    keep = moment / "transcript.whisper.json"
    if old.is_file() and not keep.exists() and not (moment / "speech.json").exists():
        shutil.copy2(old, keep)
    _atomic(old, json.dumps(result["transcript"], ensure_ascii=False, indent=1))
    _atomic(moment / "speech.json", json.dumps(result["speech"], ensure_ascii=False, indent=1))
    derived = vault_root / "derived" / "speech" / date / moment_id
    derived.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(derived / "embeddings.npz", **result["embeddings"])
    speech = result["speech"]
    return {"turns": len(speech["turns"]), "speakers": len(speech["speakers"]), "speech_s": speech["speech_s"],
            "languages": speech["languages"], "events": len(speech["events"])}  # fmt: skip


def run_moment(vault_root: Path, date: str, moment_id: str, models: Models, say: Callable[[str], None] = print) -> dict:
    path = moment_audio(vault_root / date / "moments" / moment_id)
    say(f"Speech of {date} {moment_id} ({path.name}) on {models.device}")
    return write(vault_root, date, moment_id, transcribe(load(path), models, say))


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)
