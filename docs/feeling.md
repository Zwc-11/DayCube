# How the day felt

No sensor measures a mood. Reviews of the evidence on faces (Barrett et al., 2019) and on the
body's physiology (Siegel et al., 2018) found no reliable signature of an emotion: the same
expression goes with different feelings, and the same feeling with different expressions. So the
fact here is what the person says: energy and mood from 1 to 5, with an optional note, a few
times a day. Everything else is evidence shown beside the report, never a verdict in its place.

Code: `services/ingest/ingest/feeling.py`, routes in `services/ingest/ingest/api/recall.py`, the
form on the viewer's Recall page (`?view=recall`). Tests: `services/ingest/tests/test_feeling.py`.

## Reports

`POST /feeling` with `{energy, mood, note?, t?}` (both from 1 to 5; `t` defaults to now) appends
to `vault/capture/<date>/feeling.jsonl`:

```json
{"id": "fe-20261027095000", "t": "2026-10-27T09:50:00-04:00", "kind": "feeling", "energy": 4, "mood": 2, "note": "stuck on the timeline bug"}
```

It is a capture log like the others: compaction, [deleting a span](capture.md#deleting-a-span)
and the nightly round treat it as they treat the rest. A report within 45 minutes of an episode
sets that episode's feeling signal ([recall](recall.md#episodes)).

## Evidence

Each report is returned and listed (`GET /day/{date}/feeling`) with what was recorded in the ten
minutes before it:

| Field | From |
| --- | --- |
| `keys_per_min`, `clicks_per_min`, `move_px_per_min` | the typing and mouse counts (`inputs.jsonl`: counts per 10 s, never which keys) |
| `switches_per_min` | window switches in the [timeline](timeline.md)'s segments |
| `doing` | the category in front at the time, or `away`, or `nothing recorded` |
| `part` | morning (from 05:00), afternoon (12:00), evening (18:00), night (23:00) |

## The personal model, and when it is dropped

Where nothing was reported, a model could estimate energy and mood from the same evidence. It is
only worth showing if it knows something the calendar does not. So it is judged against the
person's own average for that part of the day and that activity:

1. Below 100 reports it is not judged: `verdict: "collecting"`.
2. From 100 on, each day is left out in turn. The baseline predicts each of its reports as the
   average of the other days' reports in the same part of day and activity (falling back to the
   part of day, then to everything). The model is that baseline plus a ridge regression (penalty
   3.0) from the evidence to what the baseline leaves over.
3. For energy and for mood separately, the model is kept only if its mean absolute error on the
   left-out days is under 0.95 of the baseline's. Otherwise it is dropped and only reports are
   shown.

`GET /feeling/model` returns the count, both errors per target and the verdict (`collecting`,
`kept`, `dropped`).

On made-up reports the rule behaves as intended (`test_feeling.py`): where mood follows typing
speed the model's error is under 0.6 of the baseline's and it is kept; where energy follows
nothing it is dropped; on pure noise both are dropped. Whether typing and mouse counts say
anything about a real person's reports is not known until there are 100 of them.

## Not in this version

- **Voice.** Loudness, pitch and speech rate need the desk microphone recording speech, which is
  off until it is chosen.
- **Face.** Blink rate and posture would come from the eye tracker's camera. The tracker keeps
  only a ten-second reading every ten minutes (`health.jsonl`), and its eyes-closed share reads
  high when the eyes look down; a count per minute is not built.
- **Pulse.** A chest strap would add heart rate. None is connected.
- **A prompt.** Nothing asks for a report; the form on the Recall page waits to be used. A tray
  prompt three times a day belongs to the Windows app.
- **The estimate itself.** Only the rule that gates it is built. Drawing an estimate where
  nothing was reported comes after a `kept` verdict, which needs 100 reports first.
