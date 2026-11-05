# End-of-day review

What the day was, said in a few sentences at its end: phases 2 to 4 of the daily log and
end-of-day summary plan (2026-10-30). It is written from the [day timeline](timeline.md) and the day's search
units, into `vault/<date>/review.json` (contract `packages/schemas/review.schema.json`), and shown
at the top of the daily note and in the viewer.

Code: `services/ingest/ingest/review/metrics.py` (the numbers), `summary.py` (the words, the
validator, writing), `signals.py` (typing and mouse counts, lines noted, phone apps), `git.py`
(commits), `sites.py` (things finished on websites), `plan.py` (planned blocks, best hours),
`weekly.py` (the week), `push.py` (the morning push), `services/capture-win/capture/librarian.py`
(when), `inputs.py` and `doingnow.py` (recording). Tests: `services/ingest/tests/test_day_review.py`,
`test_day_signals.py`, `test_day_plan.py`, `apps/viewer/e2e/review.spec.ts`.

## What it holds

The review day runs from 04:00 to 04:00 local, like the timeline.

- **Numbers**, all computed by code: time at the desk, away and at places; first and last desk
  time; the longest stretch on one thing; stretches of 25 min or more; glances elsewhere under
  20 s, in all and per hour at the desk; changes of what you were doing; desk time after
  midnight; the share in no category; pages not recorded; passages read line by line and on how
  many pages; searches; copies; conversations recorded. Time per category and per project. From
  the signals below, when they were recorded: desk time with typing or the mouse, keys and clicks;
  lines you noted; phone time in the apps you log, pickups, phone time during desk time and time
  per app; commits; things finished on websites; planned blocks that went as planned.
- **Headline and 5 to 8 sentences** (fewer on a day with little in it), each citing the records it
  rests on, as Ask's answers do (`sentences: [{text, evidence}]`).
- **Planned**: each block of the daily note's Plan section or the calendar files, and what became
  of it (below).
- **Done**: time per project of 10 min or more, commits per project, things finished on websites,
  and pages with 3 or more passages read.
- **To change**: up to three changes, each tied to a number by its key: two or more planned blocks
  that went elsewhere or were missed, 20 min or more of phone during desk time (or 40 pickups),
  glances above 30 an hour,
  no stretch of 25 min, a longest stretch under 45 min on a day of 2 h or more at the desk, 30 min
  or more after midnight, 10% or more in no category, 20% or more of social, watching, games,
  shopping or news. The most pressing first.
- **Tomorrow**: deadlines seen on pages that fall in the 7 days after the day, where the desk
  work stopped, and the two hours your long stretches of the last 14 days ran in most, kept for the
  nearest deadline or the day's main project.
- **Evidence**: every cited record with its time and text. A stretch of the timeline is
  `timeline:<n>`, with its first records (`capture:app:…`, `place:…`); a unit keeps its own id.
- **Gaps**: what it could not see (no phone records; typing and mouse use or phone apps when they
  were not recorded; files left out).
- `illustrative`: some records behind it are sample or simulated data. The viewer shows
  "Sample data"; the note says so.

## The words, and the checks

Without a language model, a template writes the words from the numbers. With
`DAYCUBE_SUMMARY_LLM=anthropic` (and `ANTHROPIC_API_KEY`, `DAYCUBE_ANTHROPIC_MODEL`) or
`DAYCUBE_SUMMARY_LLM=local` (an OpenAI-compatible server on this PC, such as `llama-server`, at
`DAYCUBE_LOCAL_LLM_URL`, `http://127.0.0.1:8080/v1` by default; only addresses on this computer are
accepted), a model writes them. It is sent the day's numbers, category and project times, what got
done and what is due, and up to about 150 records: the 40 longest stretches of the timeline and up
to 12 of each kind of unit (lines read, searches, copies, transcript lines, places), each cut to 220
characters. With the Anthropic API those leave this PC; nothing is sent when the setting is empty,
which is the default.

Its answer is checked before it is used:

- every cited id is one of the day's records it was given (a record of another day is refused);
- every number in the words is one of the numbers (as displayed, rounded, or in another unit:
  `2 h 30 min`, `150 min`, `2.5 hours`), part of the date, or in a name the words may use;
- every capitalized word inside a sentence, domain and quotation appears in the records the
  sentence cites or among the day's names (categories, projects, apps, sites, places, pages);
- a headline of at most 120 characters, 5 to 8 sentences, no sentence twice, improvements tied to a
  number or category key, and no Markdown, links, `<`, `>` or line breaks.

A rejected answer is sent back once with what was wrong. A second rejection, a failed call or no
model gives the template's words, which pass the same checks (a test holds them to it). Rejections
are kept in the review (`writer.rejected`) and reported by `doctor`.

The checks bound what the words can claim. They cannot tell whether a true number is described
fairly, and they do not read number words ("three"): the model is told to write digits. A record's
own text, such as a page's words, can still steer what a model writes about that record.

## When it is written

While the capture receiver runs, its librarian (every 10 minutes):

- a **draft** in the first round after 23:00, once;
- the **final** in the first round after 04:00, when the review day has ended;
- the final again when that day's facts change (a phone import, a relabel);
- in its first round, the final of each of the last 7 days that has records and no final (the
  computer was off at 04:00).

Not at shutdown: Windows gives a program a few seconds. The facts are hashed (`facts`): the same
facts are never written twice, so a model is asked once per day and change, not once per round. A
draft whose facts did not change becomes the final without being written again.

On request: **Summarize now** in the viewer, `POST /day/{date}/review`, or
`uv run daycube review --date 2026-10-29` (`today` and `yesterday` work, as review days;
`--check` holds the stored words to freshly collected facts and times each step).
`check-review.cmd` does that for yesterday on this PC and keeps the result in
`vault\logs\review-check.json`.

## Where it shows

- **Daily note**: a `## Summary` section at the top (headline, sentences, numbers, done, to change,
  tomorrow, who wrote it and when), and a `## Conversations` section with each recording of the day:
  its time, title, length, lines, the people named in it and its first two lines.
- **Viewer**: a card beside the daily note (the note's own Summary section is not shown twice), and
  at the top of the itinerary on a day page, where its footnotes and strip move the playhead. The
  strip shows the review day, trimmed to what was recorded, one bar per stretch colored by
  category (away grey, places green, pages not recorded hatched). Picking a bar shows what it was
  and relabels it: a category and a project, for the whole app or site or only that window or page.
  Writing needs the vault token (Settings › Connection).
- `python -m capture doctor`: yesterday's review, when and by what it was written.

## Beside the timeline

Recorded by the capture receiver and read for the review day's window (phase 3):

- **Typing and mouse** (`capture/inputs.py` → `capture/<date>/inputs.jsonl`). Windows Raw Input
  (`RIDEV_INPUTSINK`, no keyboard hook) counts keys pressed, mouse buttons, wheel notches and how far
  the mouse moved, in 10-second buckets, written only for buckets with input. Which keys were pressed
  is never kept: a held key's code stays in memory until it is released, so its auto-repeat counts
  once, and no record has a field for it. Nothing is counted while a password manager, the lock or
  sign-in screen or an app excluded from screen reading is in front, nor while recording is paused.
  `python -m capture inputs` prints the buckets for 30 s; `--no-inputs` turns it off.
  Input that software sends (`SendInput` from an agent, a remote-control tool or a keep-awake
  program; Raw Input gives it no device) is counted apart, as `injected` in the bucket, and is
  neither typing nor you at the desk. The same counter decides **away**: the screen reader asks it
  how long since a real keyboard or mouse was used, not Windows' own idle time, which software
  input resets (a keep-awake tool made every hour look like desk time). Input Windows saw that
  reached the counter neither from a device nor from a program (a window run as administrator in
  front, touch, pen) is not taken for absence: Windows' time decides then. An away record says
  which decided, `"input": "devices" | "software" | "unseen"`. The doctor's `software input` line
  shows the count and the last time.
- **Lines you note** (`capture/doingnow.py` → `doing.jsonl`). `Alt+Shift+N` (`RegisterHotKey`)
  opens a box, "What are you doing now?"; Enter saves, Escape or a minute closes it.
  `python -m capture doing "..."` and `POST /doing` (the vault token) write the same line. The
  review quotes the latest two with their times; a model sees them all.
- **Phone apps** (`POST /phone/events` → `phone-events.jsonl`), from iOS Shortcuts personal
  automations (recipe in [phone](phone.md)). An app's use runs from its opening to its closing; an
  opening with no closing ends at the next event or after 30 min (marked estimated). A pickup is a
  use that starts a minute or more after the last ended.
- **Commits** (`review/git.py`): every commit authored in the window, all branches, no merges, in
  the folders of `vault/projects.yaml` that are repositories, and in the folder of a vault named
  `vault` (the DayCube checkout). The repository's `user.email` (or the global one) tells yours
  from the rest: the **Commits** number counts yours, **Commits by others** counts the rest by
  author name (an agent committing for you shows as "Claude made 20"), and the Done line lists both
  ("DayCube: 11 commits by you (…); 20 by Claude"). With no email set, all are yours. Read-only,
  with `GIT_OPTIONAL_LOCKS=0`. `DAYCUBE_GIT=off` in the environment turns it off.
- **Finished on websites** (`review/sites.py`): pages the extension already sent the text of
  (`blocks.jsonl`) whose address and words match a rule. LeetCode's accepted submissions are built
  in; `vault/sites.yaml` adds more (its form is in the module's docstring), for example LEARN's
  dropbox confirmation once you have seen its wording.

## Planned against actual

Blocks come from the daily note's own text (outside the generated region), under a heading named
Plan or Today: `- 09:30–11:00 DayCube splats` (`9:30-11`, `14:00 - 15:30`, a checkbox in front all
work; hours before 04:00 are after midnight). And from calendar files in `vault/calendar/*.ics`:
each timed event of the day, weekly and daily repeats followed (`BYDAY`, `INTERVAL`, `UNTIL`,
`COUNT`, `EXDATE`), which covers a term's class schedule; all-day and cancelled events are not
blocks.

A block **went as planned** when 60% of it was desk time on something its words name (a word of 3
letters or more, or a course code, found in the stretch's project, category or window title),
**partly** from 20%, **went elsewhere** when 30% or more of it was desk time on something else
(named, with its time), and was **missed** otherwise.

## The week

`vault/weeks/<YYYY-Www>.json` (contract `packages/schemas/week.schema.json`) and
`notes/Weekly/<YYYY-Www>.md`: a row per day (desk time, stretches of 25 min or more, the longest,
the main category and project, phone time, pickups, commits), the totals, time per category and
project, the week before, the best hours and the deadlines of the week after. Numbers only, no
model. The librarian writes the last finished week once Sunday's review day has ended (Monday
04:00), and again when one of its days' timeline or review changes; `uv run daycube week --date last`
(or any date of a week) writes it now.

## The morning push

Off unless `DAYCUBE_SUMMARY_PUSH` is `ntfy` (`DAYCUBE_NTFY_URL`: an https topic address; a topic on
ntfy.sh can be read by anyone who knows its name, so make it long and random, or run your own) or
`telegram` (`DAYCUBE_TELEGRAM_TOKEN` from @BotFather, `DAYCUBE_TELEGRAM_CHAT`). In its first round
after `DAYCUBE_PUSH_AT` (08:00) the librarian sends the day before's final review once: the
headline, three sentences, the first change and two items for tomorrow, which is what leaves the
PC. A failed send is tried in the next rounds, five times at most; `vault/logs/push.json` keeps what
was sent.

## Relabeling

A correction goes to `vault/label-corrections.json` (keys in [the timeline's](timeline.md)
categories section). The day it was made on gets its timeline and review written again at once;
other days follow when they are next written (`daycube timeline --all` rewrites every timeline).
With a model set, a relabel that changes the facts asks it again.

## Measured

On this PC (AMD Ryzen 7 9800X3D, Python 3.11.9, 2026-10-30), template words, a temporary copy of
the vault's capture logs for 2026-10-24 to 2026-10-30 (no phone days):

| | 2026-10-25 | 2026-10-28 | 2026-10-29 |
| --- | --- | --- | --- |
| first write, indexing the day's two calendar days | 3.6 s | 1.9 s | 2.1 s |
| written again with the same facts (`unchanged`) | | 70 ms | |
| `review.json` | 47 KB | 38 KB | 45 KB |
| the template's words against fresh facts | no problems | no problems | no problems |

The second write reads the timeline and the units from the database and compares the facts; the
first also brings the two calendar days into the database (reading their capture logs, up to 39 MB
of text blocks a day).

## Limits and gaps

- The words come from the template until a model is set; the model's quality on these days is not
  measured yet. A model's answer that fails the checks twice falls back to the template.
- Typing and mouse counts, the hotkey and phone events run on Windows and iOS: their recording is
  tested with fakes here (parsing real `RAWINPUT` layouts, counting, buckets, the hotkey's one-box
  rule, the endpoint), not on the PC itself; `doctor` reports whether they run.
- Matching a planned block by its words is crude: "rust reading" does not match a page titled in
  other words. A relabel or the project's keywords help.
- A website counts as finished only when its page text was sent by the extension, and only by the
  rules given.
- The daily note is a calendar day; its Summary is the review day of the same date, which also
  holds the hours after midnight.
- Corrections apply to the day they are made on at once and to other days when those are written
  again.
