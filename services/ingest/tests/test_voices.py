"""The same voices across recordings (ingest.voices): you are the voice in the most recordings,
a name given once follows the voice, a name given by hand stays, ids survive rebuilds, and two
speakers of one recording are never one voice."""

import json

import numpy as np
from fastapi.testclient import TestClient
from ingest import voices
from ingest.vault import Vault, read_json

RNG = np.random.default_rng(7)
PEOPLE = {name: RNG.normal(size=192) for name in ("me", "ana", "ben")}


def recording(vault: Vault, date: str, moment: str, who: list[str], noise: float = 0.35) -> None:
    """A recording whose speakers S1, S2, ... are ``who``, with their voiceprints a little off."""
    folder = vault.root / date / "moments" / moment
    folder.mkdir(parents=True)
    day = vault.root / date / "day.json"
    if not day.is_file():
        day.write_text(json.dumps({"date": date, "places": [], "transits": []}))
    labels = [f"S{k + 1}" for k in range(len(who))]
    segments = [{"start": k, "end": k + 1, "text": "hi", "speaker": label, "words": []} for k, label in enumerate(labels)]
    (folder / "transcript.json").write_text(json.dumps({"segments": segments, "speaker_map": {}}))
    (folder / "meta.json").write_text(json.dumps({"id": moment, "t0": f"{date}T12:00:00-04:00", "duration_s": len(who)}))
    (folder / "scene.json").write_text(json.dumps({"kind": "audio", "audio": "audio.m4a"}))
    (folder / "speech.json").write_text(json.dumps({"version": 1, "speakers": [{"label": label} for label in labels]}))
    prints = np.stack([PEOPLE[w] + RNG.normal(scale=noise * np.linalg.norm(PEOPLE[w]) / np.sqrt(192), size=192) for w in who])
    derived = vault.root / "derived" / "speech" / date / moment
    derived.mkdir(parents=True)
    np.savez(derived / "embeddings.npz", speaker_labels=np.array(labels), speakers=prints.astype(np.float32))


def names(vault: Vault, date: str, moment: str) -> dict:
    return read_json(vault.root / date / "moments" / moment / "transcript.json")["speaker_map"]


def test_you_are_the_voice_in_most_recordings_and_a_name_follows_the_voice(vault: Vault) -> None:
    recording(vault, "2026-10-20", "mo_voice_1", ["me", "ana"])
    recording(vault, "2026-10-21", "mo_voice_2", ["ana", "me"])
    recording(vault, "2026-10-22", "mo_talk_3", ["me", "ben"])
    recording(vault, "2026-10-23", "mo_talk_4", ["me"])
    recording(vault, "2026-10-24", "mo_talk_5", ["ben", "ana", "me"])
    gallery = voices.update(vault)
    assert len(gallery["voices"]) == 3
    # Each voice's speakers are closer to it than the voices are to each other.
    assert min(v["least_alike"] for v in gallery["voices"]) > max(v["closest"]["similarity"] for v in gallery["voices"])
    you = next(v for v in gallery["voices"] if v["is_you"])
    assert len(you["recordings"]) == 5 and you["name"] == "person:me"
    assert names(vault, "2026-10-20", "mo_voice_1") == {"S1": "person:me"}
    assert names(vault, "2026-10-24", "mo_talk_5") == {"S3": "person:me"}

    # Naming Ana once names her wherever she speaks.
    path = vault.root / "2026-10-21" / "moments" / "mo_voice_2" / "transcript.json"
    transcript = read_json(path)
    transcript["speaker_map"]["S1"] = "person:ana"
    path.write_text(json.dumps(transcript))
    voices.update(vault)
    assert names(vault, "2026-10-20", "mo_voice_1") == {"S1": "person:me", "S2": "person:ana"}
    assert names(vault, "2026-10-24", "mo_talk_5")["S2"] == "person:ana"
    speech = read_json(vault.root / "2026-10-20" / "moments" / "mo_voice_1" / "speech.json")
    assert speech["voices"]["S2"]["how"] == "same voice" and speech["voices"]["S1"]["how"] == "you"
    assert read_json(vault.root / "2026-10-21" / "moments" / "mo_voice_2" / "speech.json")["voices"]["S1"]["how"] == "confirmed"

    # A name given by hand in one recording stays there, even against the voice's.
    path = vault.root / "2026-10-24" / "moments" / "mo_talk_5" / "transcript.json"
    transcript = read_json(path)
    transcript["speaker_map"]["S2"] = "person:anna-maria"
    path.write_text(json.dumps(transcript))
    voices.update(vault)
    assert names(vault, "2026-10-24", "mo_talk_5")["S2"] == "person:anna-maria"
    assert names(vault, "2026-10-20", "mo_voice_1")["S2"] == "person:ana"  # 1 vote each: the first given


def test_ids_survive_a_rebuild_and_one_recordings_speakers_stay_apart(vault: Vault) -> None:
    recording(vault, "2026-10-20", "mo_a", ["me", "ana"])
    recording(vault, "2026-10-21", "mo_b", ["me", "ana"])
    first = {v["id"]: set(v["recordings"]) for v in voices.update(vault)["voices"]}
    recording(vault, "2026-10-22", "mo_c", ["ana", "me"])
    second = {v["id"]: set(v["recordings"]) for v in voices.update(vault)["voices"]}
    assert set(first) == set(second)
    assert all(first[i] <= second[i] for i in first)
    # The same person split in two by a recording's diarization: two speakers there, never merged.
    recording(vault, "2026-10-23", "mo_d", ["ben", "ben"], noise=0.05)
    gallery = voices.update(vault)
    ben = [v for v in gallery["voices"] if "2026-10-23/mo_d" in v["recordings"]]
    assert len(ben) == 2


def test_naming_a_speaker_in_the_viewer_names_their_voice_elsewhere(vault: Vault, client: TestClient, auth: dict) -> None:
    recording(vault, "2026-10-20", "mo_voice_1", ["me", "ana"])
    recording(vault, "2026-10-21", "mo_voice_2", ["me", "ana"])
    recording(vault, "2026-10-22", "mo_voice_3", ["ana"])
    response = client.post("/speakers/confirm", json={"date": "2026-10-20", "moment_id": "mo_voice_1", "diar_label": "S2",
                                                       "person_id": "person:ana"}, headers=auth)  # fmt: skip
    assert response.status_code == 200, response.text
    assert names(vault, "2026-10-22", "mo_voice_3") == {"S1": "person:ana"}
    got = client.get("/moment/mo_voice_2", params={"date": "2026-10-21"})
    assert got.status_code == 200, got.text
    moment = got.json()
    assert moment["speech"]["voices"]["S2"] == {"voice": moment["speech"]["voices"]["S2"]["voice"], "person": "person:ana",
                                                "how": "same voice"}  # fmt: skip
    # Confirming the name it already had makes it yours: renaming the voice elsewhere leaves it.
    confirm = {"date": "2026-10-22", "moment_id": "mo_voice_3", "diar_label": "S1", "person_id": "person:ana"}
    assert client.post("/speakers/confirm", json=confirm, headers=auth).status_code == 200
    assert read_json(vault.root / "2026-10-22" / "moments" / "mo_voice_3" / "speech.json")["voices"]["S1"]["how"] == "confirmed"
    confirm = {"date": "2026-10-21", "moment_id": "mo_voice_2", "diar_label": "S2", "person_id": "person:安娜"}
    assert client.post("/speakers/confirm", json=confirm, headers=auth).status_code == 200
    assert names(vault, "2026-10-22", "mo_voice_3") == {"S1": "person:ana"}
    assert names(vault, "2026-10-21", "mo_voice_2")["S2"] == "person:安娜"
    bad = {**confirm, "person_id": "person:../x"}
    assert client.post("/speakers/confirm", json=bad, headers=auth).status_code == 422


def test_a_year_of_speakers_is_clustered_in_seconds() -> None:
    import time

    rng = np.random.default_rng(0)
    people = rng.normal(size=(60, 192))
    who = rng.integers(0, 60, size=1500)
    prints = people[who] + rng.normal(scale=0.35, size=(1500, 192)) * np.linalg.norm(people[who], axis=1, keepdims=True) / np.sqrt(192)
    prints /= np.linalg.norm(prints, axis=1, keepdims=True)
    recordings = [f"r{k // 3}" for k in range(1500)]  # 3 speakers per recording
    t = time.monotonic()
    labels = voices.cluster(prints.astype(np.float32), recordings)
    assert time.monotonic() - t < 60
    # Each found voice is one person.
    for g in set(labels):
        assert len({int(who[k]) for k in range(1500) if labels[k] == g}) == 1
