"""The same voices across recordings: who S1 of one memo is in the next.

The speech worker keeps each speaker's voiceprint (CAM++, 192 numbers, the mean of their windows)
in ``derived/speech/<date>/<moment>/embeddings.npz``. Here:

- **Clustering.** All voiceprints are clustered by cosine similarity, merging groups whose average
  similarity is above ``SAME``. Two speakers of one recording are never merged: they were told
  apart there.
- **Ids.** A voice keeps its id across rebuilds when its voiceprint matches the voice of that id
  before.
- **You.** You are the voice in the most recordings, made on your own phone, once it is in at
  least two. No enrolment is needed.
- **Names.** A name you give a speaker (``/speakers/confirm``: ``person:<name>`` in the
  transcript's ``speaker_map``) names their voice. Every other recording where that voice speaks
  gets it in its ``speaker_map``, unless a name was set there by hand. You are ``person:me``
  unless you named yourself.

Written: ``derived/speech/voices.json`` (the voices: id, recordings, name, whether it is you, how
alike its least alike speaker is and the voice closest to it, the voiceprint), and per recording ``speech.json`` ``voices``: label to voice, name, and how the name is
known (``confirmed``: given here; ``same voice``: given to this voice elsewhere; ``you``: the voice
in the most recordings).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .vault import Vault, atomic_text, read_json

SAME = 0.5  # average cosine similarity above which two groups of voiceprints are one voice
KEEP_ID = 0.6  # similarity to a voice of the last build for it to keep that voice's id
ME = "person:me"


def _prints(vault_root: Path) -> list[dict]:
    out = []
    for npz in sorted((vault_root / "derived" / "speech").glob("*/*/embeddings.npz")):
        date, moment = npz.parent.parent.name, npz.parent.name
        with np.load(npz) as data:
            labels = [str(x) for x in data["speaker_labels"]]
            prints = data["speakers"].astype(np.float32)
        for label, vector in zip(labels, prints, strict=True):
            n = float(np.linalg.norm(vector))
            if n > 0:
                out.append({"date": date, "moment": moment, "label": label, "print": vector / n})
    return out


def cluster(prints: np.ndarray, recordings: list[str], *, same: float = SAME) -> list[int]:
    """Average-linkage clustering under cannot-link (prints of one recording stay apart), with the
    group similarities updated in place (Lance-Williams), so a merge costs one pass over them."""
    n = len(prints)
    if n == 0:
        return []
    sim = (prints @ prints.T).astype(np.float64)
    _, where = np.unique(np.array(recordings), return_inverse=True)
    clash = where[:, None] == where[None, :]  # one recording's speakers are different people
    size = np.ones(n)
    member = np.arange(n)
    sim[clash] = -np.inf
    while True:
        a, b = np.unravel_index(int(np.argmax(sim)), sim.shape)
        if sim[a, b] <= same:
            break
        row = (size[a] * sim[a] + size[b] * sim[b]) / (size[a] + size[b])
        clash[a] |= clash[b]
        clash[:, a] = clash[a]
        row[clash[a]] = -np.inf
        sim[a], sim[:, a] = row, row
        size[a] += size[b]
        sim[b], sim[:, b] = -np.inf, -np.inf
        member[member == b] = a
    order = {g: k for k, g in enumerate(sorted(set(member.tolist())))}
    return [order[m] for m in member.tolist()]


def stale(vault_root: Path) -> bool:
    """Whether a recording's voiceprints are newer than the voices: speech run outside a job (the
    speech worker's ``moment`` command) is matched to the others at the receiver's next look."""
    gallery = vault_root / "derived" / "speech" / "voices.json"
    built = gallery.stat().st_mtime if gallery.is_file() else -1.0
    return any(p.stat().st_mtime > built for p in (vault_root / "derived" / "speech").glob("*/*/embeddings.npz"))


def update(vault: Vault) -> dict:
    """Rebuild the voices and spread the names; the gallery written."""
    root = vault.root
    prints = _prints(root)
    gallery_path = root / "derived" / "speech" / "voices.json"
    before = read_json(gallery_path) if gallery_path.is_file() else {"voices": []}
    if not prints:
        return {"same": SAME, "voices": []}
    keys = [f"{p['date']}/{p['moment']}" for p in prints]
    labels = cluster(np.stack([p["print"] for p in prints]), keys)

    # Names given by hand, per (recording, label).
    transcripts: dict[str, dict] = {}
    for key in set(keys):
        path = root / key.split("/")[0] / "moments" / key.split("/")[1] / "transcript.json"
        transcripts[key] = read_json(path) if path.is_file() else {"segments": [], "speaker_map": {}}
    speech_files = {key: root / key.split("/")[0] / "moments" / key.split("/")[1] / "speech.json" for key in set(keys)}
    inferred_before = {}
    for key, path in speech_files.items():
        voices = (read_json(path) or {}).get("voices") if path.is_file() else None
        for label, info in (voices or {}).items():
            if info.get("how") != "confirmed":
                inferred_before[(key, label)] = info.get("person")

    def confirmed(key: str, label: str) -> str | None:
        person = transcripts[key].get("speaker_map", {}).get(label)
        # A name written by an earlier spreading is not a name given by hand.
        return person if person and inferred_before.get((key, label)) != person else None

    voices = []
    for g in sorted(set(labels)):
        members = [k for k, lab in enumerate(labels) if lab == g]
        centre = np.mean([prints[k]["print"] for k in members], axis=0)
        centre /= np.linalg.norm(centre)
        names: dict[str, int] = {}
        for k in members:
            person = confirmed(keys[k], prints[k]["label"])
            if person:
                names[person] = names.get(person, 0) + 1
        voices.append({"members": members, "print": centre, "recordings": sorted({keys[k] for k in members}),
                       "name": max(names, key=names.get) if names else None, "found_you": False})  # fmt: skip

    # Ids: kept from the last build where the voiceprint matches.
    old = [(v["id"], np.array(v["print"], np.float32)) for v in before.get("voices", []) if v.get("print")]
    used: set[str] = set()
    taken = [int(v["id"].split(":")[1]) for v in before.get("voices", [])] or [0]
    next_id = max(taken) + 1
    for voice in sorted(voices, key=lambda v: -len(v["recordings"])):
        match = max(((float(voice["print"] @ p), i) for i, p in old if i not in used), default=(0.0, None))
        if match[1] is not None and match[0] >= KEEP_ID:
            voice["id"] = match[1]
        else:
            voice["id"] = f"voice:{next_id}"
            next_id += 1
        used.add(voice["id"])
    ranked = sorted(voices, key=lambda v: -len(v["recordings"]))
    you = ranked[0] if len(ranked[0]["recordings"]) >= 2 and (len(ranked) == 1 or len(ranked[0]["recordings"]) > len(ranked[1]["recordings"])) else None
    for voice in voices:
        voice["is_you"] = voice is you
        if voice["is_you"] and not voice["name"]:
            voice["name"], voice["found_you"] = ME, True

    # Spread: each recording's labels get their voice, and its name where none was given by hand.
    per_key: dict[str, dict] = {key: {} for key in set(keys)}
    for voice in voices:
        for k in voice["members"]:
            key, label = keys[k], prints[k]["label"]
            by_hand = confirmed(key, label)
            person = by_hand or voice["name"]
            how = "confirmed" if by_hand else "you" if voice["found_you"] else "same voice" if person else None
            per_key[key][label] = {"voice": voice["id"], "person": person, "how": how}
    for key, mapping in per_key.items():
        transcript = transcripts[key]
        speaker_map = dict(transcript.get("speaker_map") or {})
        for label, info in mapping.items():
            if info["how"] in ("same voice", "you"):
                speaker_map[label] = info["person"]
            elif info["how"] is None and inferred_before.get((key, label)) == speaker_map.get(label):
                speaker_map.pop(label, None)  # spread before, its voice has no name now
        date, moment = key.split("/")
        tpath = root / date / "moments" / moment / "transcript.json"
        if tpath.is_file() and speaker_map != transcript.get("speaker_map"):
            transcript["speaker_map"] = speaker_map
            with vault.lock:
                vault.write_json(tpath, transcript)
            vault.invalidate_index(date)
        spath = speech_files[key]
        if spath.is_file():
            speech = read_json(spath) or {}
            speech["voices"] = mapping
            atomic_text(spath, json.dumps(speech, ensure_ascii=False, indent=1) + "\n")

    # How well apart they are, to check SAME against real recordings: each voice's least alike
    # speaker, and the voice most like it.
    centres = np.stack([v["print"] for v in voices])
    between = centres @ centres.T
    np.fill_diagonal(between, -np.inf)
    for k, voice in enumerate(voices):
        voice["least_alike"] = round(float(min(prints[m]["print"] @ voice["print"] for m in voice["members"])), 3)
        j = int(np.argmax(between[k])) if len(voices) > 1 else None
        voice["closest"] = {"voice": voices[j]["id"], "similarity": round(float(between[k, j]), 3)} if j is not None else None
    gallery = {"same": SAME, "voices": [
        {"id": v["id"], "name": v["name"], "is_you": v["is_you"], "recordings": v["recordings"],
         "least_alike": v["least_alike"], "closest": v["closest"], "print": [round(float(x), 5) for x in v["print"]]}
        for v in sorted(voices, key=lambda v: int(v["id"].split(":")[1]))
    ]}  # fmt: skip
    gallery_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(gallery_path, json.dumps(gallery, indent=1) + "\n")
    return gallery
