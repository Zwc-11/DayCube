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
| Speech regions | Silero VAD | MIT | where people speak, in stamps split at 300 ms pauses; for speakers, pauses under 0.6 s bridged |
| Language | SenseVoice-Small | model card: not stated | the language of each stamp (zh, en, yue, ja, ko), so each piece the recogniser is given is in one language |
| Transcript | Qwen3-ASR-1.7B on the GPU, 0.6B on a CPU | Apache-2.0 | text and its language, per piece: a run of stamps of one language, at most 170 s |
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

### Pieces of one language

Qwen3-ASR-1.7B, given a piece that changes language, transcribes one language and drops the other.

- **Found** on the PC's first GPU canary (2026-10-25): of English, Mandarin, English, Mandarin in one
  27 s piece, it returned the English sentence twice and no Mandarin. The aligner then spread the
  second English sentence over the first Mandarin clip.
- **Reproduced** in bfloat16 on a CPU: English then Mandarin, and Mandarin then English, each gave
  only the English; each clip alone gave both right. The 0.6B model did not do this on the canary.
- **The fix.** SenseVoice tells each speech stamp's language from its first 20 s. A stamp shorter
  than 1 s, or with none told, takes the language of the stamps before it. Pieces are runs of one
  language, still at most 170 s, and the recogniser detects the language of each.
- **Result:** the canary on Qwen3-ASR-1.7B (bfloat16, CPU) gives 4 pieces (en, zh, en, zh),
  Mandarin CER 0.0, English WER 0.133, times to 0.02 s.
- **Still possible.** A switch inside a stamp, with no pause of 300 ms, is in one piece. Mixing
  English words into Mandarin sentences is left to the recogniser.
- `speech.json` `pieces` lists each piece with the language told and the one the recogniser heard.
  Speech from before (`version` 1) was cut by length only.

### Turns

- A turn is a run of one speaker's words.
- A pause of 1.5 s starts a new turn, as does a sentence end once a turn passes 30 s.
- A word's language comes from its script, among the languages detected in its piece. A turn's
  language is the one most of its words' time is in.
- Fillers are counted as words, not told apart from their ordinary uses. "like" as a verb and 吧 at
  the end of a sentence are counted too.

## The same voices across recordings (`ingest/voices.py`)

The speech worker keeps each speaker's voiceprint: the mean of their CAM++ windows, 192 numbers. When
a speech job finishes, when you name a speaker, and when the receiver finds voiceprints newer than
the voices (speech run with `python -m speech moment`), it rebuilds the voices from all of them:

- **Voices.** Voiceprints are clustered by cosine similarity, merging groups while their average
  similarity is above 0.5 (`SAME`). Two speakers of one recording are never merged: diarization
  told them apart there.
- **Ids.** A voice keeps its id (`voice:3`) across rebuilds when its voiceprint is within 0.6 of the
  same id's before.
- **You** are the voice in the most recordings, once it is in at least two and in more than any
  other voice. It is named `person:me` unless you name it. No enrolment.
- **Names.** Naming a speaker (`/speakers/confirm`, the chips above a transcript) names their voice.
  - Every other recording where it speaks gets the name in its transcript's `speaker_map`.
  - A name given by hand in a recording stays there, even against the voice's.
  - When one voice was given two names in different recordings, the one given most is spread; on a
    tie, the one from the earliest recording.
- **Checking 0.5.** It is a starting value, set before there were recordings of the same people on
  different days. `daycube voices` rebuilds the voices and prints, per voice, its least alike
  speaker (`least_alike`) and the voice most like it (`closest`). The threshold is right when every
  `least_alike` is above it and every `closest` below.

The viewer shows a recording's speakers as chips above the transcript. A chip says how its name is
known: given here, `same voice` (given to this voice elsewhere), or `by voice` (found as you).
Clicking it opens a field to name them. Each line shows its speaker and, as tags, its emotion when
emotion2vec+ gives it at least 0.5 (not neutral), and laughter, applause or crying heard while it was
said.

## Conversations in all-day sound (`speech/conversations.py`)

The app records all day in one-hour files. The `conversations` job runs Silero VAD over an hour,
on the CPU and without waiting for the GPU, then:

- joins speech with less than 2 minutes of silence between it
- keeps stretches with at least 8 s of speech, each padded by a second
- returns their spans

The receiver, the vault's one writer of days, makes each span a talk moment (`ingest/phone/importer.py`
`add_talks`) and queues its `speech` job.

## What is written

| Path | What |
| --- | --- |
| `moments/<id>/transcript.json` | transcript.schema.json: a segment per turn, `speaker` S1…, words with times. The viewer, search and `/speakers/confirm` read it as before |
| `moments/<id>/transcript.whisper.json` | the faster-whisper transcript it replaced (the first time only) |
| `moments/<id>/speech.json` | seconds of speech per language; speakers with talking time and turns; per turn its language, emotion scores and fillers; sound events; the models; each stage's seconds |
| `vault/derived/speech/<date>/<id>/embeddings.npz` | each speaker's voice embedding (192) for recognising them across days, and each turn's emotion embedding (1024) for training |
| `moments/<id>/speech.json` `voices` | per speaker: the voice it is, its name, and how the name is known (`confirmed`, `same voice`, `you`); written by the receiver |
| `vault/derived/speech/voices.json` | the voices: id, name, whether it is you, its recordings, `least_alike`, `closest`, the voiceprint |

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
- **The GPU's model on a CPU:** `DAYCUBE_ASR_MODEL=Qwen/Qwen3-ASR-1.7B DAYCUBE_SPEECH_DTYPE=bfloat16`
  runs the 1.7B in 3.4 GB, with the GPU's numbers (about 2 min for the canary on 2 cores).

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
- pieces and windows, and pieces cut where the language changes
- clustering two voices, with a stray window
- turns and smoothing
- fillers
- the canary's scores
- the worker leasing, finishing and failing jobs against a stand-in receiver

`DAYCUBE_SPEECH_CANARY=1` adds the canary on the real models, and SenseVoice telling the canary's
English and Mandarin apart (whole clips and their first 2 s).

`services/ingest/tests/test_voices.py` covers the voices: three people over five days found as three
voices, you found and named, a name given once spread to the other recordings and a name given by
hand kept, ids kept across a rebuild, one recording's two speakers never merged, naming through
`/speakers/confirm` (also in Mandarin), and 1,500 voiceprints clustered (1.5 s here). The
viewer's chips and tags are covered by `apps/viewer/e2e/phone.spec.ts`.
