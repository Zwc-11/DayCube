"""The speech pipeline's pieces, without models; the whole pipeline on real models is the canary
(``DAYCUBE_SPEECH_CANARY=1``, minutes on a CPU, model downloads on first run)."""

import json
import os
from pathlib import Path

import httpx
import numpy as np
import pytest
from speech import asr, canary, pipeline, speakers, vad, worker
from speech.sound import fillers


def test_aligned_words_get_the_transcripts_punctuation_back() -> None:
    text = "Well, what's what about it? 甚至出现交易。Oh."
    units = [("Well", 0.0, 0.3), ("whats", 0.4, 0.7), ("what", 0.7, 0.9), ("about", 0.9, 1.1), ("it", 1.1, 1.2),
             ("甚", 2.0, 2.1), ("至", 2.1, 2.2), ("出", 2.2, 2.3), ("现", 2.3, 2.4), ("交", 2.4, 2.5), ("易", 2.5, 2.6),
             ("Oh", 3.0, 3.2)]  # fmt: skip
    words = asr.punctuate(text, units)
    assert [w["word"] for w in words] == [
        "Well,",
        "what's",
        "what",
        "about",
        "it?",
        "甚",
        "至",
        "出",
        "现",
        "交",
        "易。",
        "Oh.",
    ]
    assert words[1]["start"] == 0.4 and words[-1]["end"] == 3.2
    assert asr.join(words) == "Well, what's what about it? 甚至出现交易。 Oh."
    assert [asr.word_language(w["word"], "English,Chinese") for w in words[3:7]] == [
        "English",
        "English",
        "Chinese",
        "Chinese",
    ]
    assert asr.word_language("我们", "Cantonese") == "Cantonese"


def test_speech_regions_are_grouped_into_pieces_the_aligner_takes() -> None:
    spans = [(0.0, 50.0), (55.0, 120.0), (125.0, 400.0)]
    pieces = vad.chunks(spans, max_s=170)
    assert pieces[0] == (0.0, 120.0)
    assert all(b - a <= 170 for a, b in pieces) and pieces[-1][1] == 400.0
    assert speakers.windows([(0.0, 1.0)]) == [(0.0, 1.0)]
    w = speakers.windows([(10.0, 14.0)])
    assert w[0] == (10.0, 11.5) and w[1][0] == 10.75 and 14.0 - w[-1][1] <= 0.25  # at most 0.25 s left over


def test_windows_of_two_voices_become_two_speakers_and_a_stray_window_joins_the_nearer() -> None:
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=192), rng.normal(size=192)
    e = np.stack([a + rng.normal(scale=0.6, size=192) for _ in range(30)]
                 + [b + rng.normal(scale=0.6, size=192) for _ in range(12)]
                 + [a * 0.6 + b * 0.4 + rng.normal(scale=2.0, size=192)])  # fmt: skip
    e /= np.linalg.norm(e, axis=1, keepdims=True)
    labels = speakers.cluster(e)
    assert set(labels) == {0, 1}
    assert (labels[:30] == 0).all() and (labels[30:42] == 1).all()  # 0: the speaker with most windows


def test_turns_split_at_a_new_speaker_and_a_long_pause_and_a_blip_is_smoothed() -> None:
    words = [{"word": w, "start": s, "end": s + 0.3} for w, s in
             [("hi", 0.0), ("there.", 0.4), ("yes", 1.0), ("ok", 1.4), ("so", 4.0), ("then", 4.4)]]  # fmt: skip
    turns = pipeline.segments(words, [0, 0, 1, 1, 1, 1])
    assert [(t["speaker_index"], t["text"]) for t in turns] == [(0, "hi there."), (1, "yes ok"), (1, "so then")]
    assert speakers.smooth([0, 0, 1, 0, 0], [{"start": k, "end": k + 0.2} for k in range(5)]) == [0, 0, 0, 0, 0]
    assert speakers.smooth([0, 1, 1, 1, 0], [{"start": k, "end": k + 0.9} for k in range(5)]) == [0, 1, 1, 1, 0]


def test_fillers_are_counted_in_both_languages() -> None:
    words = [{"word": w} for w in ["Um,", "I", "mean,", "it's", "like", "fine.", "嗯", "那", "个", "就", "是", "吧"]]
    assert fillers(words) == {"um": 1, "i mean": 1, "like": 1, "嗯": 1, "吧": 1, "那个": 1, "就是": 1}
    assert fillers([{"word": "umbrella"}, {"word": "likely"}]) == {}


def test_canary_scores() -> None:
    assert canary.wer("The tribal chief then called", "the tribal chieftain called") == pytest.approx(2 / 4)
    assert canary.cer("甚至出现交易几乎停滞的情况。", "甚至出现交易几乎停滞的情况") == 0
    audio, placed = canary.build({"en": np.ones(16000, np.float32), "zh": np.ones(8000, np.float32)})
    assert [k for k, _ in placed] == ["en", "zh", "en", "zh"] and placed[2][1] == pytest.approx(3.5)
    assert len(audio) == 16000 * 2 + 8000 * 2 + 16000 * 4


def test_the_worker_leases_runs_and_reports_to_the_receiver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".token").write_text("secret")
    calls: list[tuple[str, dict]] = []
    jobs = [{"id": 7, "stage": "speech", "version": 1, "inputs": {"date": "2026-10-24", "moment": "mo_voice_x"}, "attempts": 0},
            {"id": 8, "stage": "speech", "version": 1, "inputs": {"date": "2026-10-24", "moment": "mo_gone"}, "attempts": 0}]  # fmt: skip

    def receiver(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        body = json.loads(request.content or b"{}")
        calls.append((request.url.path, body))
        if request.url.path == "/jobs/lease":
            assert body["stages"] == ["speech"] and body["needs"] == "gpu"
            return httpx.Response(200, json={"job": jobs.pop(0) if jobs else None})
        return httpx.Response(200, json={"ok": True, "status": "failed"})

    real = httpx.Client
    monkeypatch.setattr(worker.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(receiver), **kw))

    def fake_run(vault_root: Path, date: str, moment: str, models: object, say: object) -> dict:
        if moment == "mo_gone":
            raise FileNotFoundError("mo_gone: no audio file in the moment")
        return {"turns": 3, "speakers": 2}

    monkeypatch.setattr(worker, "run_moment", fake_run)
    assert worker.serve(tmp_path, "http://receiver", once=True, models=object()) == 1
    paths = [p for p, _ in calls]
    assert paths == ["/jobs/lease", "/jobs/7/done", "/jobs/lease", "/jobs/8/fail", "/jobs/lease"]
    assert calls[1][1]["outputs"] == {"turns": 3, "speakers": 2}
    assert calls[3][1]["retry"] is False and "no audio" in calls[3][1]["error"]


@pytest.mark.skipif(os.environ.get("DAYCUBE_SPEECH_CANARY") != "1", reason="runs the models (DAYCUBE_SPEECH_CANARY=1)")
def test_canary() -> None:
    report = canary.run(device=os.environ.get("DAYCUBE_SPEECH_DEVICE") or "cpu", say=lambda _: None)
    assert report["ok"], report
