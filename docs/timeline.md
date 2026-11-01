# Day timeline

One sequence per day of where the person was and what they were doing: at the desk (and on what),
away, at a place the phone recorded, and the conversations recorded meanwhile. It is phase 1 of
the daily log and end-of-day summary plan (2026-10-30); the [end-of-day review](review.md)
computes its numbers from it.

`vault/<date>/timeline.jsonl`, one segment per line, contract `packages/schemas/timeline.schema.json`:

```json
{"t": "2026-10-27T09:00:00-04:00", "until": "2026-10-27T09:20:00-04:00", "where": "desk",
 "doing": "timeline.py - DAYCUBE - Visual Studio Code", "category": "coding", "project": "DayCube",
 "talk": [], "switches": 3, "evidence": ["capture:app:3f2a...", "capture:visit:f1-0000001"],
 "source": "pc", "fidelity": "known", "labeled": "rule",
 "key": "app:code.exe|timeline.py - DAYCUBE - Visual Studio Code", "broad": "app:code.exe"}
```

The file is written only when its content changes, so what reads it (the review's facts, the
viewer) sees a new stamp only for a new timeline. `GET /day/{date}/review` returns it with the day's
review, writing it first if the day has none ([API](api.md)).

- **Review day**: 04:00 local on the date to 04:00 the next morning, so a night past midnight
  stays with its day (23 or 25 hours across a clock change). Capture folders are calendar days,
  so the records of the day before and after are read too.
- **where**: `desk`, `away`, `place:<id>` (a phone visit in `day.json`) or `unknown` (a
  conversation nothing else covers). Time nothing recorded has no segment.
- **source** `pc`, `phone` or `pc+phone`; **fidelity** `estimated` when any part was inferred (idle
  away, phone places), `illustrative` when any part comes from sample or simulated records
  (capture records with `"synthetic": true`, a phone day whose events are illustrative or
  synthetic); **labeled**: how the category was decided (`null` away from the desk).
- **key** and **broad**: at the desk, the window or page in front longest and its app or site, the
  keys a correction of just it or of all of it uses (`null` elsewhere).
- **evidence**: the records the segment was made from. `capture:app:<id>` and `capture:visit:<id>`
  name the window or browser record that started a span, the same ids as the search units in most
  cases (a unit that merged a return within 60 s keeps its first record's id).
  `capture:away:<id>` and `capture:media:<id>` name `windows.jsonl` and media records; they have
  no search unit. `place:<id>` and event ids are the phone's units.

Code: `services/ingest/ingest/review/` (`timeline.py`, `labels.py`, `categories.yaml`,
`simulate.py`). Tests: `services/ingest/tests/test_day_timeline.py`, and the screen reader's new
records in `test_screen.py`.

## How it is made

1. **In front.** The screen reader's `windows.jsonl` says which app was in front from one record
   to the next ([capture](capture.md)); while a browser is in front, the extension's
   `browser.jsonl` says which page. Presence is the screen reader's: away from the last input once
   5 minutes pass with neither input nor gaze, at the lock screen, or when the computer slept.
   The extension's own one-minute idle is not used where `windows.jsonl` exists, so a page read
   without touching anything counts. A calendar day without `windows.jsonl` (before the screen
   reader) falls back to the extension's visits and its idle.
2. **Watching.** A video that plays on in the page in front after input stops is watching until it
   pauses or ends, not away. Locked is away whatever plays.
3. **Not recorded.** Paused recording, the reader stopped or switched off, and the bare desktop
   end what was in front. The reader writes `still` every 5 minutes; when its records stop for 6
   minutes (the computer switched off), what was in front ends at the last one, and while they
   go on a window counts for as long as it stays in front. For days recorded before `still`
   existed, a window with no record after it counts at most 2 hours.
4. **Phone.** Places and walks take over away time they overlap and fill time the computer did not
   record. Time at the desk stays at the desk.
5. **Talk.** A moment with a transcript is listed on the segment it overlaps most.
6. **Segments.** Pieces with the same where, category and project merge across gaps up to 60 s.
   Something else in front for under 20 s beside desk time is a switch: counted on the segment it
   interrupted. `doing` is what was in front longest.

## Categories and projects

The first match decides a category:

1. `vault/label-corrections.json`, for the exact window or page, then its app or site:
   `{"site:youtube.com": {"category": "studying"}, "app:code.exe|a3.py - cs136 - Visual Studio Code": {"project": "CS 136"}}`.
   Keys are `page:<url>`, `site:<domain>`, `app:<program>|<title>` and `app:<program>`. The
   viewer's relabel (phase 2) will write this file.
2. `vault/categories.yaml`, then the built-in `services/ingest/ingest/review/categories.yaml`
   (its header explains the form: `apps`, `sites` with `*` and paths, `titles`, `courses: true`).
3. `vault/labels-cache.json`: earlier answers of the language model.
4. One call to a language model for the apps and sites still unknown, only with
   `DAYCUBE_CATEGORY_LLM=anthropic` (and `ANTHROPIC_API_KEY`, `DAYCUBE_ANTHROPIC_MODEL`). Off by
   default. When on, each unknown app's or site's name and up to three of its titles (80
   characters each) are sent once; answers outside the categories are dropped; a failed call is
   reported and not retried for an hour.

Otherwise `other`. A browser in front with a page the extension does not record (an excluded
site, a private window) is `private`, so what cannot be known is not counted as a gap in the rules.

Projects: a correction; `vault/projects.yaml`

```yaml
projects:
  DayCube:
    folders: ['E:\DAYCUBE']   # its name in an editor's title, its path in a terminal's
    keywords: [daycube]       # in a title or address
    sites: [github.com/caesar/daycube]
    courses: []               # course codes that belong to this project
```

then a course code in the title (`CS 136`), then the folder an editor has open (VS Code, Cursor,
Kiro, JetBrains, Visual Studio titles) or the repository of a GitHub, GitLab or Bitbucket page. A
folder named like a course (`cs136`) is the course. A terminal whose path contains a project's
folder, or a folder an editor showed that day, gets that project.

A labels file that does not parse is reported and left out; the built-in rules still apply.

## Running

- The capture receiver's librarian writes the timeline of the review day under way every 10
  minutes (what is in front now is still in front), and the day before once more as recorded,
  then only when one of its files changes.
- `uv run daycube timeline --date 2026-10-27` (or `--all`) writes it and prints a report: desk,
  away and place seconds, the share in `other` and what it was, how categories were decided, and
  any files left out.
- `python -m capture doctor` reports yesterday's and today's desk time and the share no category
  knew, against a target of 10%, with the three largest unknowns.

## Measured

**Synthetic days** (`uv run python eval/day_sim.py --days 20 --seed 1`, and `--seed 101`;
`simulate.py` documents the script and the noise). Each second of the review day is compared with
the script's truth. Half the days have phone places, walks and a voice memo; 2026-10-31 (25 hours)
is among them. AMD Ryzen 7 9800X3D, Python 3.11.9, 2026-10-30:

| | seed 1 | seed 101 |
| --- | --- | --- |
| clean: seconds right, every day | 100% | 100% |
| clean: switches found | 992 of 992 | 981 of 981 |
| noisy: seconds right (worst day) | 99.963% (99.907%) | 99.967% (99.900%) |
| noisy: median boundary error (worst day's 90th percentile) | 0.21 s (10.4 s) | 0.24 s (7.7 s) |
| conversations on the right segment, both modes | 8 of 8 | 8 of 8 |
| build, median per day | 30 ms | 29 ms |

The plan's targets (100% clean; at least 98% noisy with the median boundary within 2 s) are met.
The 90th percentiles come from phone boundaries, jittered by up to 15 s. The pytest gate runs three
of these days (`test_simulated_days_are_right_second_by_second`).

**Real days.** Built read-only from the vault on this PC for 2026-10-24 to 2026-10-29 with the
built-in rules only (no `categories.yaml`, no language model): 1.2%, 5.1%, 9.0%, 9.4%, 6.0% and
3.5% of desk time in `other`, all within the 10% target, the largest unknowns being personal and
hackathon sites. 34 to 256 minutes a day on 2026-10-24 to 26 were `private`. Building one day took
56 to 170 ms (median of 3), reading three days of `browser.jsonl` and `windows.jsonl` (up to 3.1 MB
and 0.55 MB a day).

Two things found in that data shaped the rules above. On 2026-10-29 nothing at all was recorded
from 07:33 to 20:10 (the computer asleep or off) after a YouTube tab had been in front: the
extension's fallback counted those 12.6 hours as watching, so the fallback is now only for days
without the screen reader. On 2026-10-24 and 25, input came back on its own five
minutes after the last (222 of 276 returns from idle on the 24th), so the reader logged away and
back a second later every five minutes; the timeline keeps such an away whole.

## Limits and gaps

- The synthetic score does not measure accuracy on real days, which have no ground truth. The
  share in `other` is the only real-day measure so far.
- Absences under 5 minutes are not seen (the screen reader's threshold), nor is time at the desk
  with the computer recording nothing.
- Days recorded before 2026-10-30 have no `still`, `asleep` or `stopped` records: a computer that
  slept or was switched off with a window in front counts that window for up to 2 hours.
- The search units and the daily note still split a window in front at midnight; only the
  timeline joins the calendar days.
- Phone place ids are per visit, so two visits to one place are two `where` values.
- The viewer shows the timeline as the [review](review.md) card's strip; the 3D day page's own
  timeline at the bottom does not show categories.
