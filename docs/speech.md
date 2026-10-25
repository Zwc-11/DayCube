# Speech

For every recording of sound in the vault (voice memos now, the DayCube Capture app's audio
next), the speech worker produces:

- what was said, with the time of every word
- who said it
- how it sounded: emotion per turn, filler words, laughter, music and other sound events

It runs on the GPU as a [job](jobs.md) and needs no command.

## Stages (`services/speech/speech`)

| Stage | Model | License | What it gives |
| --- | --- | --- | --- |
| Speech regions | Silero VAD | MIT | where people speak; pauses under 0.6 s bridged |
| Transcript | Qwen3-ASR-1.7B on the GPU, 0.6B on a CPU | Apache-2.0 | text and its languages, in pieces of at most 170 s |
| Word times | Qwen3-ForcedAligner-0.6B | Apache-2.0 | each word's start and end (each character in Chinese), with the transcript's punctuation put back |
| Speakers | CAM++ (`funasr/campplus`) | Apache-2.0 | a voice embedding per 1.5 s window, every 0.75 s, clustered |
| Emotion | emotion2vec+ large | model card: not stated | 9 classes per turn of at least 1 s, plus its 1024-number embedding |
| Sound events | SenseVoice-Small | model card: not stated | laughter, music, applause, cough, crying, sneeze, breath, in 10 s windows |
| Fillers | — | — | 嗯 呃 啊 哦 吧 呢 嘛 那个 就是 然后 …, um uh like "you know" "I mean" …, counted per turn |

### Why these models

The research is in the project's pipeline plan.

- **Qwen3-ASR-1.7B** beats Whisper large-v3 on:
  - Mandarin: Fleurs-zh CER 2.41 vs 4.09, WenetSpeech meeting 5.88 vs 19.11
  - English: Fleurs-en 3.35 vs 4.08
- **The aligner** places words to within 33–53 ms.

### How speakers are told apart

- Windows of one speaker score about 0.35 cosine similarity on the sample clips; different
  speakers score −0.1 to 0.15.
- Clusters merge while their average similarity is above 0.2.
- A cluster with fewer than 4% of the windows joins the speaker it is most like.
- Each word takes the label of the windows it overlaps. A run of words shorter than 0.6 s between
  two runs of one other speaker is given to that speaker.
- Speakers are labelled S1, S2, … by talking time.
- pyannote's community-1 pipeline was the plan's pick. It is gated behind a Hugging Face sign-in,
  so nothing here uses it.

### Turns

- A turn is a run of one speaker's words.
- A pause of 1.5 s starts a new turn, as does a sentence end once a turn passes 30 s.
- A word's language comes from its script, among the languages detected in its piece. A turn's
  language is the one most of its words' time is in.
- Fillers are counted as words, not told apart from their ordinary uses. "like" as a verb and 吧 at
  the end of a sentence are counted too.

## What is written

| Path | What |
| --- | --- |
| `moments/<id>/transcript.json` | transcript.schema.json: a segment per turn, `speaker` S1…, words with times. The viewer, search and `/speakers/confirm` read it as before |
| `moments/<id>/transcript.whisper.json` | the faster-whisper transcript it replaced (the first time only) |
| `moments/<id>/speech.json` | seconds of speech per language; speakers with talking time and turns; per turn its language, emotion scores and fillers; sound events; the models; each stage's seconds |
| `vault/derived/speech/<date>/<id>/embeddings.npz` | each speaker's voice embedding (192) for recognising them across days, and each turn's emotion embedding (1024) for training |

## Running

    uv sync --project services/speech                 # once: PyTorch (CUDA 12.8 on Windows), the models' code
    uv run --project services/speech python -m speech canary          # the check below
    uv run --project services/speech python -m speech moment --date 2026-10-24 --moment mo_voice_20261024_214746

- **Install.** On Windows the environment takes PyTorch for CUDA 12.8, which runs on the RTX 5070
  (sm_120). The models are fetched from Hugging Face the first time, about 7 GB, and need no
  sign-in. They are kept in the checkout's `.cache/huggingface` (set `HF_HOME` to put them
  elsewhere), so they stay off the system drive.
- **The receiver.**
  - Once the environment exists, the receiver starts the worker (`python -m speech serve`) and
    restarts it if it stops. Its log is `vault/logs/speech.log`.
  - The worker leases `speech` jobs over the receiver's API. Discovery queues one for every
    recording without a `speech.json`.
  - Like every GPU job, a speech job is handed out only while the PC is free.
- **Without a GPU** each stage runs in a process of its own, so only one model is in memory at a
  time. Freeing a model inside one process gave back 600 MB of its 3.2 GB.

## The check (`speech/canary.py`)

The check uses two published sample clips, an English one (FunASR's SenseVoice example) and a
Mandarin one (Qwen3-ASR's example). They are joined as A, B, A, B with a second of silence between
them. The check passes when:

- the English word error rate is ≤ 15% and the Mandarin character error rate ≤ 10%
- there are four turns from two speakers, in the order A B A B
- each clip's second turn starts as far after its first as it was placed, to within 0.15 s

## Tests

`services/speech/tests/test_speech.py` covers, without models:

- punctuation back on aligned words, in English and Chinese
- word languages
- pieces and windows
- clustering two voices, with a stray window
- turns and smoothing
- fillers
- the canary's scores
- the worker leasing, finishing and failing jobs against a stand-in receiver

`DAYCUBE_SPEECH_CANARY=1` adds the canary on the real models.
