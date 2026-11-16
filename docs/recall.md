# Recall

A recorder keeps everything at the same strength forever. Memory does not: what held attention
or mattered stays longer, everything fades, and recalling something makes it last. This part
gives each stretch of a day a strength that behaves that way, and uses it to choose what to ask
back: three questions each morning and up to three cue cards a day.

Code: `services/ingest/ingest/recall/` (`strength.py` the curve, `episodes.py` the day cut into
episodes, `history.py` the log of recalls and the fitted weights, `today.py` the questions, cards
and the nightly round), `services/ingest/ingest/api/recall.py` (routes),
`services/capture-win/capture/librarian.py` (`step_recall`: when),
`apps/viewer/src/components/recall/RecallPage.tsx` (the page, `?view=recall`). Tests:
`services/ingest/tests/test_recall_strength.py`, `test_recall.py`.

## Episodes

An episode is a stretch of the [day timeline](timeline.md) that stayed with one thing: the same
project at the desk (or, with no project, the same category), one place, or one conversation.
`episodes.cut` groups the timeline's segments:

- the same context again within 10 minutes continues the episode (a few minutes away do not end it);
- A, then B for under 10 minutes, then A again is one episode of A;
- time away, browsing the extension does not record, and a stretch under 5 minutes are not
  episodes.

The 10 and 5 minutes were chosen on ten recorded days of one person (2 to 19 hours at the desk):
5 and 3 minutes cut those days into 12 to 75 pieces with a median length of 5 to 10 minutes;
10 and 5 give 6 to 35 episodes of 9 to 21 minutes, and keep what 10 and 10 lose on days of many
short stretches (one day's 1.3 hours of episodes became 0.4).

They are written to `vault/<date>/episodes.json`, once per review day (04:00 to 04:00) by the
nightly round, and only when they changed. Each has an id (`ep_<YYYYMMDD_HHMM>` of its start), its
times, `kind` (`desk`, `talk`, `place`), a title in the timeline's own words, category and
project, its recordings (`talk`), the apps, sites and places in it (`keys`), the first 40 records
it rests on (`evidence`), and four signals between 0 and 1:

| Signal | From | Value |
| --- | --- | --- |
| attention | lines read `n`, read again `r`, long looks `d` (the eye tracker, in the browser); seconds with typing or clicking `a` of the episode's `T`; window switches per minute `w` | `max(1 − e^(−(n + 2r + d)/20), a/T) × (1 − min(0.5, w/4))` |
| feeling | [feeling reports](feeling.md) within 45 minutes of it | the furthest of energy or mood from 3, over 2 |
| novelty | its apps, sites and places | the share not met in the 30 days before; 0 until 7 of those days have episodes |
| importance | copies `c`, searches `s`, photos, scans and recordings made `m` | `1 − e^(−(c + s + 3m)/3)` |

Each also keeps up to five `highlights`, in the records' own words: its first two searches, the
two passages read most closely, and a text copied. They are shown with the answer to a question.

The file holds nothing that is not in the timeline and the day's records, so it can be made
again at any time: `daycube recall --date 2026-11-14`.

## Strength

`strength.py`. The share of an episode still there `t` days after it ended or was last recalled:

```text
R(t) = (1 + (19/81) · t / S)^(−p)          p = 0.5
```

With `p = 0.5`, `S` (the stability) is the number of days until 90% is left, and R falls to a
half after 12.8 × S days. The curve is a power law, which fits measured forgetting better than
an exponential and is the form FSRS fits to review logs; an exponential through the same 90%
point forgets far faster in the long run.

An episode starts with

```text
S0 = s · exp(a·A + b·A·E + c·N + d·I)
```

for attention `A`, feeling `E`, novelty `N` and importance `I`. Feeling counts through attention:
a feeling about something not attended leaves little. Each recall then changes `S`:

| Recall | Effect on S |
| --- | --- |
| A question answered `remembered` | `× (1 + g · (1 − R))`: the more had faded, the more it gives |
| `partly` | `× (1 + g · (1 − R) · 0.5)` |
| The episode `opened` from a card (nothing asked) | `× (1 + g · (1 − R) · 0.3)` |
| `forgot` | `× lapse`; seeing the answer starts the clock again |

| Weight | Default | Meaning |
| --- | ---: | --- |
| `s` | 1.0 | days to 90% for an episode with no signal |
| `a` | 1.0 | attention |
| `b` | 0.5 | feeling, through attention |
| `c` | 0.5 | novelty |
| `d` | 1.0 | importance |
| `g` | 2.0 | what a recall adds |
| `p` | 0.5 | the curve's power |
| `lapse` | 0.5 | what a forgotten question leaves of S |

The defaults are a starting point, not a measurement of anyone. Each graded answer is a
measurement: the curve predicted `R`, and the person remembered or did not. From 60 graded
answers on, the nightly round fits `s`, `a`, `c`, `d` and `g` to the oldest 75% of the answers
(coordinate search on the cross-entropy between predicted `R` and the answer) and keeps the fit
(`vault/recall/weights.json`) only if it also predicts the newest 25% better than the weights in
use. A fit to noise therefore changes nothing. `b` and `p` stay fixed in this version: they need
more answers than a few months give.

Nothing but the log is stored. An episode's strength at any moment is its first stability
carried through its recalls in time order (`strength.replay`), so changing the weights changes
every strength consistently, and the log can be replayed under any other model.

## What is asked back

`today.py`. Each review day, at the first request:

- **Three questions.** Episodes are drawn first from 1, 3, 7, 21 and 60 days ago, then from any
  other of the last 90 days. In each day the episode with the most to lose is asked:
  `(min(1, minutes/60) + importance + attention + novelty + feeling) × (1 − R)`. Episodes under
  10 minutes are never asked. The question gives the time and the kind of thing ("Monday 9
  November, 09:00: about 30 minutes at your desk. What were you doing?"); the answer (the
  episode's title, times and highlights) is shown only on request, and the person's own verdict
  is logged.
  The first verdict counts. The questions are kept for the day
  (`vault/recall/questions/<date>.json`), so they do not change while it lasts. The desktop
  app's morning notification says, under its headline, how many wait.
- **Up to three cue cards.** Episodes not asked today, with under 70% left, whose importance or
  feeling reached 0.3, the most to lose first. Opening one is logged as a recall that asked
  nothing.

**Going back.** A question, once shown, and every card lead to the episode itself: for time at
the desk, the day's What I saw page with the page or app in front at that minute in view
(`?date=…&view=seen&at=HH:MM`); for a place or a conversation, the day in 3D at that hour
(`?date=…&at=HH:MM`).

`vault/recall/log.jsonl` gets one line per recall: `{t, episode, day, outcome}`. It names
episodes and quotes nothing.

## When

The capture receiver's librarian runs the nightly round (`today.night`) in its first round after
04:00: the episodes of each of the last 7 finished review days that has a timeline, oldest
first, then the fit. Earlier days of the last 90 that have a timeline and no episodes yet are
written too, so the first night covers the days recorded before this existed. `daycube recall`
runs the same round by hand and prints today's questions and cards.

| Method | Route | |
| --- | --- | --- |
| GET | `/day/{date}/episodes` | The day's episodes with their strength now (`stability_days`, `left`, `recalls`); made for the reply when not yet written |
| GET | `/recall/today` | Today's questions, the answers given, the cards |
| POST | `/recall/answer` | `{question, outcome}` with `remembered`, `partly` or `forgot` |
| POST | `/recall/opened` | `{date, episode}` |
| GET | `/recall` | Recalls by outcome, the share of graded questions that came back, the weights in use |

## Deleting a span

[Deleting a span](capture.md#deleting-a-span) writes the timelines of the review days it falls
in again, then their episodes, and removes every kept question about those days (a question
quotes its episode's title). The log stays: it holds ids, times and verdicts.

## What this version does not do

- Strength does not yet change search ranking or what is kept at full detail. Neither should go
  in before the weights are fitted to real answers.
- Attention outside the browser is typing and clicking, which says nothing about reading a PDF
  or watching a talk.
- A question is graded by the person. Nothing checks the verdict.
- The gate set for this part is 70% of 30 written questions about real days answered with the
  right evidence. It has not been run: the questions have to be written by the person whose
  days they are. `eval/score_retrieval.py --vault <vault> --questions <file>` scores them, one
  `{"q": "...", "has": "a phrase the right record holds"}` a line, and says whether the right
  record was among the first three for 70%.
