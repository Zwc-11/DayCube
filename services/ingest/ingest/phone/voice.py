"""A voice memo as an audio moment: when it was recorded, and what was said in it.

**When.** A phone's recording app stamps the file (``creation_time`` in the MP4/M4A header). On an
iPhone, Voice Memos stamps the moment the recording was saved, its end: a memo recorded alongside
a Sensor Logger recording (2026-10-24, 18 min 51 s) put its start, so read, on the drive back a few
hundred metres from home, and what was said in its first minutes matched the arrival; read as its
start, it would have begun after the sensor recording had ended, though the memo itself mentions
both recording. ``ends_at_creation`` says which reading to take;
a recording app that stamps the start sets it False.

**What was said.** Transcribed with faster-whisper (the ``transcribe`` extra) on the CPU: the
``small`` model, int8, words with times, voice activity filtering. Without it, the moment keeps
its audio and an empty transcript, and ``scene.json`` says why. Speakers are not separated
(``speaker: null``): diarization and the consent of others recorded are not implemented
(docs/consent.md), so the moment lists no consented people.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..moments.media import executable, probe

AUDIO_SUFFIXES = frozenset({".m4a", ".wav", ".mp3", ".aac", ".caf"})
MODEL = "small"


@dataclass
class Audio:
    path: Path
    t0: datetime  # UTC
    duration_s: float
    stamped: str  # what the time came from


def read(path: Path, *, ends_at_creation: bool = True, start: datetime | None = None) -> Audio:
    """When the recording began and how long it is."""
    info = probe(path)
    duration = float(info["format"]["duration"])
    if start is not None:
        return Audio(path, start.astimezone(UTC), duration, "given")
    created = (info["format"].get("tags") or {}).get("creation_time")
    if not created:
        raise ValueError(f"{path.name}: no creation time in the file; give the start time")
    stamp = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(UTC)
    if ends_at_creation:
        return Audio(path, stamp - timedelta(seconds=duration), duration, "creation time, taken as the end")
    return Audio(path, stamp, duration, "creation time, taken as the start")


def transcribe(path: Path, say: Any = print) -> dict | None:
    """Segments with word times, as transcript.schema.json; None without faster-whisper."""
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError:
        return None
    import numpy as np

    say(f"transcribing {path.name} with faster-whisper {MODEL} (about a third of its length on a laptop CPU)")
    # Decoded by FFmpeg to 16 kHz mono: faster-whisper's own decoder chokes on some phone files.
    raw = subprocess.run(
        [executable("ffmpeg"), "-nostdin", "-v", "quiet", "-i", str(path), "-ac", "1", "-ar", "16000",
         "-f", "s16le", "-"],
        capture_output=True, check=True, timeout=1800,
    ).stdout  # fmt: skip
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio, word_timestamps=True, vad_filter=True)
    return from_whisper([
        {"start": s.start, "end": s.end, "text": s.text,
         "words": [{"start": w.start, "end": w.end, "word": w.word, "p": w.probability} for w in s.words or []]}
        for s in segments
    ])  # fmt: skip


def from_whisper(segments: list[dict], duration_s: float | None = None) -> dict:
    """faster-whisper segments (``start``, ``end``, ``text``, ``words`` with ``p`` or
    ``probability`` or ``score``) as a transcript: times held inside the recording, empty
    segments dropped."""
    out = []
    limit = duration_s if duration_s is not None else float("inf")
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        start, end = max(0.0, float(segment["start"])), min(float(segment["end"]), limit)
        if not text or end <= start:
            continue
        words = []
        for word in segment.get("words") or []:
            score = word.get("p", word.get("probability", word.get("score")))
            w0, w1 = max(0.0, float(word["start"])), min(float(word["end"]), limit)
            if w1 < w0:
                continue
            item = {"word": str(word["word"]).strip(), "start": round(w0, 2), "end": round(w1, 2)}
            if score is not None:
                item["score"] = round(min(max(float(score), 0.0), 1.0), 3)
            words.append(item)
        out.append({"start": round(start, 2), "end": round(end, 2), "text": text, "speaker": None, "words": words})
    return {"segments": out, "speaker_map": {}}


def load_transcript(path: Path, duration_s: float) -> dict:
    """A transcript file: transcript.schema.json already, or faster-whisper's segments."""
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = data.get("segments", data) if isinstance(data, dict) else data
    return from_whisper(segments, duration_s)


def store(audio: Audio, folder: Path) -> str:
    """The audio copied into the moment's folder (kept as recorded); returns its file name."""
    name = "audio" + audio.path.suffix.lower()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if not target.exists() or target.stat().st_size != audio.path.stat().st_size:
        shutil.copyfile(audio.path, target)
    return name
