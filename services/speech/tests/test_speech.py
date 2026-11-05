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


def test_pieces_never_change_language() -> None:
    """English, then Mandarin, then English: three pieces, though they would fit in one. A short
    stamp (a 0.4 s "嗯", an untold one) goes with the stamps before it; untold first stamps take the
    first language told."""
    found = [(0.0, 3.0), (3.4, 7.0), (8.0, 12.0), (12.5, 12.9), (13.3, 16.0), (17.0, 20.0)]
    told = ["en", "en", "zh", "zh", "zh", "en"]
    assert vad.by_language(found, told) == [(0.0, 7.0, "en"), (8.0, 16.0, "zh"), (17.0, 20.0, "en")]
    assert vad.by_language(found, ["nospeech", "en", "zh", "en", "zh", "en"]) == [
        (0.0, 7.0, "en"), (8.0, 16.0, "zh"), (17.0, 20.0, "en")]  # fmt: skip
    # One language: the pieces of before, cut at 170 s.
    long = [(k * 10.0, k * 10.0 + 8.0) for k in range(40)]
    assert [(a, b) for a, b, _ in vad.by_language(long, ["zh"] * 40)] == vad.chunks(long, max_s=170)
    assert vad.by_language([], []) == []


@pytest.mark.skipif(os.environ.get("DAYCUBE_SPEECH_CANARY") != "1", reason="runs the models (DAYCUBE_SPEECH_CANARY=1)")
def test_sensevoice_tells_the_canarys_languages_apart() -> None:
    from speech.sound import Events

    clips = canary.fetch()
    assert Events("cpu").languages([clips["en"], clips["zh"], clips["en"][: 16000 * 2], clips["zh"][: 16000 * 2]]) == [
        "en", "zh", "en", "zh"]  # fmt: skip


def test_windows_of_two_voices_become_two_speakers_and_a_stray_window_joins_the_nearer() -> None:
    pytest.importorskip("sklearn", reason="speaker clustering requires the speech environment's scikit-learn")
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


def test_the_canarys_report_is_printed_and_kept_in_utf8_on_a_cp1252_console(tmp_path: Path) -> None:
    """Windows writes redirected output in cp1252; the PC's first canary report, with its Mandarin,
    failed to print there. The models are left out: the report is given."""
    import subprocess
    import sys

    vault = tmp_path / "vault"
    vault.mkdir()
    script = (
        "import sys, speech.canary as c\n"
        "c.run = lambda device=None, say=print: {'device': 'cuda:0', 'asr': 'Qwen/Qwen3-ASR-1.7B', 'ok': True,"
        " 'turns': [['S2', 8.6, '甚至出现交易几乎停滞的情况。']], 'zh_cer': 0.0, 'en_wer': 0.067}\n"
        f"sys.argv = ['speech', 'canary', '--vault', {str(vault)!r}]\n"
        "from speech.__main__ import main\nmain()\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          env={**os.environ, "PYTHONIOENCODING": "cp1252"}, timeout=120)  # fmt: skip
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert "甚至出现交易几乎停滞的情况" in done.stdout.decode("utf-8")
    kept = json.loads((vault / "derived" / "speech" / "canary.json").read_text(encoding="utf-8"))
    assert kept["ok"] and kept["turns"][0][2].startswith("甚至") and kept["made"][:2] == "20"


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
            assert (body["stages"], body["needs"]) in ((["conversations"], "cpu"), (["speech"], "gpu"))
            if body["stages"] == ["conversations"]:
                return httpx.Response(200, json={"job": None})
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
    reports = [(p, b) for p, b in calls if p != "/jobs/lease"]
    assert [p for p, _ in reports] == ["/jobs/7/done", "/jobs/8/fail"]
    assert sum(p == "/jobs/lease" for p, _ in calls) == 6  # conversations, then speech, each round
    assert reports[0][1]["outputs"] == {"turns": 3, "speakers": 2}
    assert reports[1][1]["retry"] is False and "no audio" in reports[1][1]["error"]


@pytest.mark.skipif(os.environ.get("DAYCUBE_SPEECH_CANARY") != "1", reason="runs VAD and downloads the canary clips")
def test_conversations_are_cut_out_of_an_hour_at_long_silences(tmp_path: Path) -> None:
    """Two talks in ten minutes of sound, the second of two bursts 60 s apart; a lone short word
    is not a conversation. The speech is the canary's English clip, where Silero VAD finds speech."""
    from speech.audio import RATE
    from speech.conversations import find

    clip = canary.fetch()["en"]  # 7 s of speech
    hour = np.zeros(int(600 * RATE), np.float32)

    def put(at_s: float, wav: np.ndarray) -> None:
        hour[int(at_s * RATE) : int(at_s * RATE) + len(wav)] = wav

    put(30, clip)
    put(40, clip)  # one talk: 30-47 s
    put(300, clip)
    put(367, clip)  # a 60 s pause: the same talk, 300-374 s
    put(520, clip[: int(1.0 * RATE)])  # one word: too little to be a talk
    found = find(hour)
    assert len(found) == 2
    assert found[0]["start_s"] == pytest.approx(29.0, abs=1.0) and found[0]["end_s"] == pytest.approx(48.2, abs=1.5)
    assert found[1]["start_s"] == pytest.approx(299.0, abs=1.0) and found[1]["end_s"] == pytest.approx(375.2, abs=1.5)
    assert found[1]["speech_s"] >= 10


@pytest.mark.skipif(os.environ.get("DAYCUBE_SPEECH_CANARY") != "1", reason="runs the models (DAYCUBE_SPEECH_CANARY=1)")
def test_canary() -> None:
    report = canary.run(device=os.environ.get("DAYCUBE_SPEECH_DEVICE") or "cpu", say=lambda _: None)
    assert report["ok"], report
