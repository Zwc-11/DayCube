# Capture: browser and gaze on your PC

From sign-in to shutdown, three pieces record what you do in the browser and where you look:

```mermaid
flowchart LR
  ext[DayCube extension in Opera] -- "records every 1 s, queued until acknowledged" --> rx
  gaze[Eye tracker, services/gaze] -- "fixations every 1 s" --> rx
  rx[Receiver 127.0.0.1:7700, services/capture-win] --> logs[(vault/capture/date/*.jsonl)]
  rx -- "dwell signal with the next heartbeat" --> ext
  rx -. "--on-dwell command" .-> hook[Your program]
```

- **The extension** reports pages, focus, searches, clicks, selections, media, and snapshots of
  the text on screen with where the viewport sits on the monitor.
- **The eye tracker** turns webcam frames into fixations with EyeTrax (MIT). Frames stay in
  memory; only fixations leave the process. It runs in its own environment because EyeTrax
  needs numpy<2 and the rest of DayCube needs numpy>=2.
- **The receiver** writes the logs, decides which paragraph each fixation belongs to and which
  line is being read, and detects dwells. It learns the tracker's live error from clicks (people look at what they click) and
  corrects for it. It starts the eye tracker and restarts it if it stops.

Nothing goes over the network: the receiver listens on 127.0.0.1 only, and the extension can
reach no other host.

## Set up on Windows

From the repository folder, in PowerShell:

```powershell
uv sync --project services/capture-win
uv sync --project services/gaze

# 1. The webcam: frame rate and how often a face is found. Aim for 15+ frames/s and 90%+.
uv run --project services/gaze python -m gaze check

# 2. Calibrate: 13 dots, about 30 s. Sit as you normally do, about an arm's length away.
uv run --project services/gaze python -m gaze calibrate

# 3. Start everything now and at every sign-in (no administrator rights needed).
uv run --project services/capture-win python -m capture install-autostart
```

Then in Opera:

1. Open `opera://extensions`, turn on **Developer mode**, choose **Load unpacked** and pick the
   `extensions/chrome` folder.
2. On the extension's card, turn on **Allow access to search page results**. Opera keeps
   extensions off search result pages unless this is on.
3. Open the extension's options (right-click its toolbar button, then **Options**), paste the
   contents of `vault\.token`, tick **Record my browsing**, and save. The **Right now** panel
   should show the receiver and the eye tracker running.

Optional: copy the history Opera already kept (about 90 days) into the vault:

```powershell
uv run --project services/capture-win python -m capture import-history
```

After pulling a new version, reload the extension at `opera://extensions` (its card shows the
version: 0.6.0 now; 0.5.0 or later puts its page script into tabs already open, so they need no refresh;
before 0.5.0, refresh them), and restart the receiver
(sign out and in, or end its `pythonw.exe` and `python.exe` processes in Task Manager and run
`python -m capture install-autostart` again), so the extension, receiver and tracker run the same
code; `python -m capture doctor` reports a receiver still running older code. A newer tracker or
extension still works with an older receiver: fields it does not know are ignored.

Recalibrate when your seat, lighting or monitor changes (`python -m gaze calibrate`). It pauses
recording for the half minute it takes, which frees the webcam, and the receiver restarts the
tracker with the new calibration.

### Processing rate and live checks

The tracker now targets **30 processed frames/s** by default, including when started by the
receiver at sign-in. The camera keeps its existing mode: this setting does not request 1080p,
change calibration geometry or create a faster hardware capture mode. A manual override is:

```powershell
uv run --project services/gaze python -m gaze track --fps 15
```

`/status` reports `gaze.target_fps` separately from the rolling measured `gaze.fps` and face
detection fraction (`gaze.face`, from 0 to 1). Refresh the extension's options page after updating:
**Right now** shows measured/target FPS, face percentage and calibration error. For example,
`29.8/30 fps` means a measured 29.8 with a target of 30; it does not round the measurement up.
Pausing hides old active telemetry, and resuming waits for a fresh tracker report.

Collect a rate report without opening the camera, reading recordings or changing pause state:

```powershell
uv run python eval/profile_gaze.py --seconds 15 --output test-results/gaze-rate.json
```

The report samples status only. Its rate values share the tracker's rolling ten-second window,
so consecutive samples are correlated. Allow ten seconds after startup for that window to settle.
Processing rate and face detection establish that the tracker is working on frames. Whether gaze
lands on the right paragraphs is measured from ordinary use, below.

### Checking it without a test

```powershell
uv run --project services/capture-win python -m capture doctor
```

prints one line per check, from what the services recorded while you used the computer: receiver,
extension, frame rate, face detection, **fixations** (the share of gaze that became fixations, and
the gaze noise), calibration error, **live accuracy**, today's logs, dwells and the sign-in entry.
It exits with code 1 when a check fails.

Live accuracy comes from clicks. The eyes are on a link or button when it is clicked, so each mouse
click with a fixation around it measures how far off the tracker is at that moment and place
(keyboard clicks have no position and are not used). After five clicks the receiver reports the
median error before and after its correction (each click predicted from the clicks before it),
uses it as the gaze error it allows for while the newest click is under 10 minutes old, and shows
it in the extension's **Right now** panel (`58 px now (7 clicks)`). A recalibration starts the
clicks over. A live error above the calibration limit means: recalibrate
from your usual seat. Every 10 minutes while gaze arrives, the same figures go to
`vault/capture/<date>/health.jsonl`, so a session can be judged afterwards.

## Everyday use

| To | Do |
| --- | --- |
| Pause everything, including the webcam | Click the toolbar button or press `Alt+Shift+D` (works once the token is saved, even with browser recording off). The badge reads `II`. The webcam is released within about a second |
| Resume | The same again. The badge reads `REC` |
| See whether it works | The extension's options page, **Right now**; or `http://127.0.0.1:7700/status` |
| See where it has you looking, and which line | Extension options, **Show where I am looking and the line I am reading**: a red ring at the last fixation placed on the page and a blue marker beside the decoded line (fainter when less sure), updated twice a second, a second or two behind the eyes. **Right now** shows the line, or why there is none: where the last fixations went (on a page, on a line, outside the browser page, no page on screen, page not reporting, another monitor) and how many of the last page's text blocks sent their lines |
| Stop starting at sign-in | `python -m capture uninstall-autostart`, or turn it off in Task Manager > Startup apps |
| Read the logs | `vault\logs\capture.log` and `vault\logs\gaze.log` |

A pause is remembered across restarts: the receiver keeps it in `vault/capture/paused` until you
resume, and logs every pause and resume in `vault/capture/pauses.jsonl`. A pause made while the
receiver was unreachable is retried every 3 seconds for a minute, then every 30 seconds while
Opera is open. If Opera closes before the receiver hears of it, the pause applies at the next
start of Opera with the receiver running; until then the eye tracker keeps recording.

The webcam turns on only after the receiver has confirmed that it is running and not paused,
and turns off within about 5 seconds of the receiver stopping or a pause. It is also released,
and nothing is recorded, while the session is locked, while another app uses the camera (a
video call), and when you are away: no keyboard or mouse input for 5 minutes and no face for
the last minute. Reading without touching anything keeps tracking. When the primary monitor is
not the calibrated one (a laptop undocked, a monitor off), tracking stops and is retried every
minute.

## Dwells: when your eyes stay too long

A *dwell* is a run of time on one block of text (a paragraph, heading, list item or cell).
Looking elsewhere for up to 2.5 seconds does not end it; a gap in gaze of 2 seconds, a tab switch
or a navigation to another page does (an address change within the page, such as a section link,
does not). Blinks and saccades between fixations count as looking. A run becomes a dwell when its
time reaches both:

- 8 seconds (`--dwell-seconds`), and
- twice the time the block takes to read at 238 words a minute (`--dwell-factor`), the average
  adult silent reading rate for English non-fiction (Brysbaert 2019, a meta-analysis of 190
  studies).

Reading a long paragraph at normal speed is therefore not a dwell. Reading it at under half
speed, re-reading it, or staring at a short line is. A dwell also needs a block the tracker can
single out at its current error: with a coarse calibration, a heading 20 px above a paragraph is a
guess between the two, and no dwell is reported on it.

Each dwell writes a `start` record when it is detected and an `end` record when it ends, with
the page, the block's text, fixation time and reading time. What happens at `start` is up to you:

| Action | Where to set it |
| --- | --- |
| Only record it (default) | Extension options |
| A notification with the passage | Extension options |
| A thin bar beside the paragraph, fading after 4 s. It is an element added to the page, so the site's scripts can see it | Extension options |
| Any program: it receives the dwell record as JSON on stdin | `python -m capture install-autostart --on-dwell "C:\path\to\program.exe --flag"` |

The bar and the notification arrive with the page's next heartbeat, so up to 5 seconds after
the dwell is detected. The program runs at once; one runs at a time.

## What is recorded

`vault/capture/<local date>/`, one JSON object per line:

| File | Records |
| --- | --- |
| `browser.jsonl` | `page` (address, how it was reached: link, typed, reload, form submit, back/forward; address changes that only move to a section are left out), `title`, `focus` (tab in front; none when the browser loses focus; no address for an excluded page), `idle` (active, idle after 60 s, locked), `search` (engine and query from result-page addresses), `click` (label, where it landed on the page, and the link address unless it points to an excluded domain), `selection` (selected or copied text), `media` (play, pause, end, with position) |
| `blocks.jsonl` | The text blocks on screen (up to 150, including text inside web components' shadow roots, as on D2L Brightspace) with their rectangles, and where the viewport is on the monitor. A block's text and its visual lines (rectangle and text of each, up to 60) are sent once per page load, again if queued text was dropped, and the lines again when the block's wrapping changes or it moves on the page; later snapshots carry only positions |
| `gaze.jsonl` | Fixations as fractions of the monitor, with the page point (corrected by clicks), the block the eyes were most likely on and its probability `p`, and the line being read: `"line": {"block": "b4", "line": 2, "p": 0.93}` (0 = the block's first line) |
| `reading.jsonl` | One record per block read: each line read with its text, seconds on it and passes over it, the share of the block's lines read (`coverage`), when the eyes moved to each line (`path`), the tracker offset the decoder found and its mean confidence (see [Which line you are reading](#which-line-you-are-reading)) |
| `attention.jsonl` | Dwell `start` and `end` records |
| `health.jsonl` | Every 10 minutes while tracking: the tracker's state (frame rate, face, fixation share, noise) and the live accuracy from clicks |
| `history.jsonl` | Visits imported from Opera's history |

Not recorded:

- private windows, and domains excluded in the extension options (subdomains included);
- the text, clicks, selections and media of a page while it, a frame in it (an embedded sign-in
  or payment form) or an open shadow root shows a password field. The page's address and title
  are still recorded;
- anything typed: fields, editable areas, and code editors that draw typed text in plain
  elements (Monaco, CodeMirror, Ace);
- anything while paused.

`import-history` leaves out the same things the extension would: the extension's excluded
domains (mirrored to `vault/capture/exclusions.json`), the domains, URL patterns and title
patterns in `services/capture-win/config/exclude.yaml`, and visits made while recording was
paused. It merges Opera Stable and Opera GX, and each run mirrors the browser's current history:
a day whose visits are all gone loses its file.

Every record has an id. The extension keeps records in its storage until the receiver
acknowledges them (up to 5,000; the oldest text snapshots are dropped first). The receiver
ignores an id it has already stored, and stores an id only once its record is written, so a
resend after a lost reply or a failed write is stored once per receiver run. Records older than
24 hours are refused. After a restart the receiver reloads block texts from today's and
yesterday's snapshots, so dwells on pages already open still know their text.

## How gaze is placed on a page

1. The tracker predicts a screen point per frame (EyeTrax ridge regression on MediaPipe eye
   landmarks), smooths it with a One Euro filter, and groups samples into fixations (I-DT, at
   least 0.1 s, split at 1 s so a long stare is reported while it lasts). The dispersion
   threshold starts at 6% of the screen width and rises with the gaze noise measured over the
   last 8 seconds, so a noisy session is not broken into pieces too short to keep. The tracker
   reports the share of gaze that became fixations (`coverage`) and the noise (`noise_px`).
2. The extension reports where the viewport is on the monitor: measured from two mouse events
   (screen point minus page point, and the page zoom from their spacing), otherwise estimated
   from the window's outer and inner sizes. Every browser window reports the page it shows,
   focused or not: a page read beside a terminal or another window is on screen too. A page that
   leaves the screen (another tab, minimized, covered) says so at once.
3. The receiver places each fixation on the page whose window it falls in (the focused window's
   when windows overlap).
4. It subtracts the offset learned from recent clicks, converts the fixation to page
   coordinates, and corrects for scrolling since the last snapshot.
5. It does not pick the block under the point: webcam error is larger than the space between
   paragraphs. A filter over "on block b" and "on nothing" scores each block by how likely the
   tracker was to report this point if the eyes were anywhere in it (the block blurred by the
   current gaze error), and assumes the eyes tend to stay where they were. After each fixation it
   knows how likely each block is; the block that leads is the one in `gaze.jsonl`, and the time
   it leads is what a dwell counts. Details and parameters: `services/capture-win/capture/attention.py`.

Returning to a tab or window sends a fresh block-position snapshot even when the page has not
changed. Block ids remain stable;
already acknowledged text is not duplicated. Reload the unpacked extension and refresh open
pages after updating the reporter script.

## Which line you are reading

Placing a fixation on a block cannot name the line: at the recalibrated error (69 px median on a
2560 px monitor) one fixation's spread covers about three lines of body text. The receiver
decodes the line from the sequence of fixations instead (`services/capture-win/capture/reading.py`):

- The extension measures each block's visual lines (`Range.getClientRects`; a text node that
  wraps is split where its characters move to the next line) and sends their rectangles in page
  coordinates with the text on each.
- The decoder is a hidden Markov model over every line in the viewport, "off the text", and a
  grid of vertical offsets. Webcam error is mostly an offset that changes slowly, and the decoder
  learns it from the reading itself: a return sweep (a jump back to the start of the line below)
  moves the eyes down a line and small steps keep them on it; horizontal steps carry no offset.
  The page's own shape gives the offset away: a reader placed a line too low soon reads past the
  end of a short last line, or into the gap between paragraphs.
- It follows reading across paragraphs and headings by itself, so near a paragraph's edge the
  line's block can differ from the block in `gaze.jsonl`'s `block` field (the dwell filter's).

| Where | What |
| --- | --- |
| `gaze.jsonl` | Each fixation's line, `"line": {"block", "line", "p"}` |
| `/status` | `"reading": {"block", "line", "lines", "p", "text", "url", "t"}`: the line now; `null` after 10 s without one. `"line_decoding"`: of the last 300 fixations, how many landed on a page and on a line, and where the rest went (`outside` every page shown, `no_page` on screen, `stale` page, `other_monitor`), with the last page's text blocks and how many sent lines; `doctor` reads it ("line reading"). `"gaze_point"`: the last fixation placed on a page (viewport px and that page's scroll) |
| `reading.jsonl` | A record per block once the eyes have left it (two fixations decoded on another block), the page changes, the tab or window loses focus, the block's lines are re-measured, or 10 s pass without gaze on the page. Blocks looked at for under 1.5 s or 3 fixations get none. A pass is a stay of 0.4 s or more on a line |

Example `reading.jsonl` record (shortened):

```json
{"kind": "read", "t": "2026-10-23T21:40:02.103000+00:00", "t_end": "2026-10-23T21:40:24.812000+00:00",
 "url": "https://example.com/notes", "block": "b4", "tag": "p", "line_count": 6,
 "lines": [{"line": 0, "text": "The method of loci is old, but the reason it works is still", "seconds": 3.4, "passes": 1}, ...],
 "coverage": 1.0, "path": [[0.0, 0], [3.6, 1], [7.1, 2], ...], "offset_px": 41.5, "p": 0.91}
```

In simulation the decoder puts 91-98% of reading time on the right line at the recalibrated error
and 94-99% with a good webcam session, scrolling included; at the first calibration's error (183 px) it is mostly
wrong, and `doctor` says to recalibrate ([Simulation](#simulation)).

## Limits

- **Accuracy.** Webcam gaze is good to roughly 2-4 degrees: paragraphs, not words. Calibration
  reports the median error on four held-out targets and refuses to track above 120 px at 1920 px
  wide (scaled to your screen width; 160 px on a 2560 px monitor). Glasses, backlight and head
  movement make it worse; the live accuracy from clicks shows when, and `doctor` says to
  recalibrate.
- **One monitor.** Gaze is calibrated on the primary monitor. Pages in windows on other monitors
  are recorded, but gaze is not placed on them.
- **Windows acceptance.** The webcam, calibration, live receiver, Opera settings and browsing
  records were checked on the Windows PC ([record](capture-windows-check.md)). The paragraph test
  and the reading tests are now simulated end to end (below) instead of being run by hand; on the
  PC, `python -m capture doctor` judges a session from ordinary use. Lock, video-call camera
  handoff and sign-in are covered by the tracker's tests with a scripted computer; the Windows
  calls themselves (`OpenInputDesktop`, the camera privacy records, the `Run` key) are not.
- **Lines.** Line decoding needs reading: on a block glanced at or stared at, the line is a guess
  (its `p` says so). It assumes text read top to bottom in columns, the way the page lays it out;
  lines are measured for the first 60 of a block. At an error above the calibration limit it is
  unreliable.
- **Browser only.** Other apps, screenshots and OCR are not captured yet; nor is there a tray
  icon. The toolbar badge and Windows' own camera indicator show that recording is on.
- **No synthesis yet.** The logs are not yet turned into day events in the viewer.

## Size

Fixations are about 260 bytes each with the block and its probability, and about 50 more with
the line; an 8-hour simulated day (`eval/capture_soak.py`, 3 fixations a second) wrote 24 MB of
`gaze.jsonl`, 4.9 MB of text snapshots and 0.6 MB of browser records before compression. Line
rectangles and their text roughly double the text snapshots. Text snapshots depend on how much you
scroll. `daycube compact` compresses the capture logs of finished days with zstd, for days that
also have a vault day folder; capture-only days are not compacted yet.

## Tests

```powershell
uv run pytest services/ingest/tests/test_capture.py services/ingest/tests/test_attention.py services/ingest/tests/test_reading.py services/ingest/tests/test_anchors.py services/ingest/tests/test_capture_tools.py
cd services/gaze; uv run pytest
npm run test:extension   # Node unit tests, then the extension in Chromium against a scratch vault
# Reporter lifecycle only: synthetic transport, safe while the live receiver is running.
npx playwright test --config playwright.extension.config.ts reporter.e2e.spec.ts
```

`capture.e2e.spec.ts` also checks that a page in a window that has lost focus keeps reporting
(it fails with the old focused-window rule), that text inside web components (an open shadow root filled
from a template, as D2L's `d2l-html-block` does, and a closed one) is recorded with its lines, and
that a link clicked inside one carries its address. `update.e2e.spec.ts` updates the extension in
place (over the DevTools protocol) with a page open and checks that the page reports again without
a reload; with the injection turned off it does not.

`gaze.e2e.spec.ts` is the paragraph test without a person: example.com at 200% zoom (set with
`chrome.tabs.setZoom`) in Chromium with the extension, the real receiver, and the real tracker
loop fed by simulated eyes staring at the paragraph through the recalibrated session's error
model, in real time for 20 s. It checks that the reporter measured the zoom, that the paragraph is
where the browser says it is, that the dwell fires on it and the bar appears, that a 5 s glance
does not fire, and that clicks carry their position. A third test reads a six-line paragraph of
`reading.html` line by line in real time (the lines the extension measured, 238 wpm, the same
error model): it checks that `gaze.jsonl` names the right line for most of the reading time
(100% in the runs so far), that `/status` and the page marker follow every line, and that leaving
the page writes the paragraph to `reading.jsonl` with all its lines. Headless Chromium needs the
full browser (`DAYCUBE_CHROMIUM=<path to chrome>`), not the headless shell, for extensions.

A day of capture through the receiver, at the live rates, takes about four minutes:

```powershell
uv run python eval/capture_soak.py --hours 8 --output eval/results/capture-soak.json
```

On 8 simulated hours (28,800 browser and 28,800 gaze batches, 91,076 fixations) every record was
logged once, none malformed, the receiver's id, text and click caches stayed under their caps,
and the median request took 3.2 ms (browser) and 4.4 ms (gaze), the same in the last tenth of the
day as in the first. The tracker's smoother and fixation detector take 12 µs a frame.

## Simulation

`eval/gaze_sim.py` runs the tracker loop and the receiver's attention code against simulated
eyes and webcam error, 100 sessions per row, on a 2560 x 1440 monitor at 100% and 125% Windows
scaling. The error model is fitted to the held-out error the tracker reports, per frame: a
calibration bias that varies over the screen (an offset, a linear term and broad bumps), head
drift since calibration, correlated frame jitter, landmark failures and blinks. Clicks in the
simulation land within about 15 CSS px of where the eyes are, and one in seven is made without
looking. The attention parameters were chosen on seeds 0-39; the table uses seeds 1000-1099. Page layouts are measured by the extension's reporter in
Chromium (`eval/measure_pages.mjs`).

```powershell
uv run python eval/gaze_sim.py --compare 80e2d6b --output eval/results/gaze-sim.json
```

Pass rate per case, revision `80e2d6b` (before) → this version, by calibration error (median
held-out, per frame: the first calibration on the Windows PC, the recalibration, a good webcam
session) and Windows scaling. A dwell case passes when a dwell fires on the intended block and on
no other; a no-dwell case passes when none fires.

| Case | Expect | 183 px, 100% | 183 px, 125% | 69 px, 100% | 69 px, 125% | 40 px, 100% | 40 px, 125% |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| stare 20 s, 200% zoom | dwell on target | 0% → **84%** | 0% → **90%** | 75% → **99%** | 89% → **100%** | 98% → **100%** | 100% → **100%** |
| stare 20 s, 200% zoom, after 6 clicks | dwell on target | 0% → **89%** | 0% → **94%** | 81% → **100%** | 92% → **98%** | 96% → **100%** | 99% → **99%** |
| stare 20 s, 100% zoom | dwell on target | 0% → **62%** | 0% → **70%** | 39% → **91%** | 55% → **95%** | 70% → **98%** | 79% → **99%** |
| look away 1.5 s mid-stare | dwell on target | 0% → **75%** | 0% → **81%** | 69% → **98%** | 83% → **100%** | 97% → **99%** | 99% → **99%** |
| re-read a paragraph 3 times | dwell on target | 0% → **0%** | 0% → **8%** | 29% → **55%** | 39% → **67%** | 51% → **93%** | 64% → **99%** |
| glance 5 s | no dwell | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** |
| tab switch mid-stare | no dwell | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** |
| read an article | no dwell | 100% → **100%** | 100% → **99%** | 100% → **97%** | 100% → **100%** | 100% → **99%** | 100% → **100%** |
| read an article, 200% zoom | no dwell | 100% → **99%** | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** |
| slow read under a heading | no dwell | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** | 100% → **100%** |

| Share of tracked time in fixations | | 34% → **66%** | 33% → **66%** | 83% → **88%** | 82% → **88%** | 88% → **88%** | 87% → **87%** |

- **Why the Windows stares failed.** With the first calibration's error, the fixation detector
  kept a third of tracked time (its threshold was fixed at 6% of the screen width; noisier gaze
  broke a steady look into pieces too short to keep), and the receiver counted only fixations
  inside the paragraph's rectangle, ending the run after 1 s outside it. None of 100 simulated
  stares became a dwell, as on the PC. The old code passed 75-89% with the recalibrated error.
- **Clicks** matter most when the calibration is coarse: six clicks before the stare take the
  183 px calibration from 84-90% to 89-94%, even with clicks that miss by about 15 px and one in
  seven made without looking.
- The reading rows (one new: the article at 200% zoom, which needs scrolling) were run for both revisions once reading followed each block's
  measured lines (one fixation per 1.2 words along each line, one regression in ten;
  `eval/results/gaze-sim-lines.json`).
- **Re-reading** a paragraph three times is the weakest dwell case (55-67% at 69 px): its first and last lines sit within
  the gaze error of the neighbouring blocks, and the filter is deliberately quicker to move on
  once a paragraph's reading time has passed, which is what keeps normal reading from being
  flagged.
- **Lines**, held-out seeds 1000-1099 (decoder constants chosen on seeds 0-23,
  `eval/results/gaze-sim-lines.json`, `GAZE_SIM_ONLY=read uv run python eval/gaze_sim.py --output ...`):
  share of reading time decoded on the right line (within one line), and blocks read for 1.5 s or
  more that got a `reading.jsonl` record:

  | Case | 183 px, 100% | 183 px, 125% | 69 px, 100% | 69 px, 125% | 40 px, 100% | 40 px, 125% |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: |
  | read an article | 21% (41%) | 42% (60%) | **94%** (95%) | **94%** (95%) | **97%** (97%) | **97%** (98%) |
  | re-read a paragraph 3 times | 28% (55%) | 58% (73%) | **95%** (96%) | **96%** (96%) | **99%** (100%) | **98%** (98%) |
  | slow read under a heading | 32% (74%) | 57% (82%) | **97%** (97%) | **91%** (92%) | **99%** (99%) | **94%** (94%) |
  | read an article, 200% zoom (two screens: scrolls on the way) | 81% (87%) | 88% (91%) | **96%** (98%) | **98%** (98%) | **98%** (99%) | **99%** (99%) |
  | blocks recorded | 86-97% | 97-98% | 100% | 100% | 100% | 100% |

  Before the page-level decoder (a separate decoder per block, fed by the dwell filter, offset
  learned afresh in each block) the same cases were 31-39% on the right line at 69 px, 125% (ten
  tuning seeds): a
  tracker reading 1-3 lines low was taken for a reader 1-3 lines further on.
- The simulation measures the code, not the person: the error model, the stare (fixations of
  about 0.45 s anywhere in the middle of the paragraph) and reading (one fixation per 1.2 words
  at 200-280 wpm) are models. `python -m capture doctor` and `health.jsonl` measure real sessions.
