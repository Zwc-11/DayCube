# Capture receiver

The receiver on `127.0.0.1:7700` takes records from the DayCube browser extension and fixations
from the eye tracker (`services/gaze`), writes them to `vault/capture/<local date>/`, works out
which text block the eyes are on, and detects dwells. On Windows it also reads the window under the
gaze in any other app (OCR) and treats it as a page. It can start the eye tracker and itself at every
Windows sign-in. Setup, what is recorded, and limits: [docs/capture.md](../../docs/capture.md).

```powershell
uv sync --project services/capture-win
uv run --project services/capture-win python -m capture install-autostart   # now and at every sign-in
uv run --project services/capture-win python -m capture run --receive --gaze # or run it in this console
uv run --project services/capture-win python -m capture import-history      # Opera's history into the vault
uv run --project services/capture-win python -m capture doctor              # is it working? from what it recorded
uv run --project services/capture-win python -m capture restart             # after git pull: run the new code now
uv run --project services/capture-win python -m capture screen check        # what OCR reads in the window in front
uv run --project services/capture-win python -m capture screen off          # stop reading other apps (on: again)
```

From the repository folder, `.\capture doctor` (or any of these commands) is the same: `capture.cmd`
runs it in this environment. A bare `python -m capture` uses the system Python, which has no
`capture` module.

| Module | Does |
| --- | --- |
| `capture/api.py` | `GET /status`, `POST /pause`, `/resume`, `/events` (browser records), `/gaze` (fixations and tracker state) |
| `capture/records.py` | Record types the receiver accepts |
| `capture/attention.py` | Which text block the eyes are on (a filter that allows for webcam error); dwell detection; block visits for `reading.jsonl` |
| `capture/reading.py` | Which line is being read: a decoder over the page's lines and the tracker's offset, using return sweeps and the page's shape |
| `capture/anchors.py` | Clicks as calibration points: the tracker's live error and an offset correction |
| `capture/screen.py` | The window under the gaze in any app: found by z-order, read by Windows OCR when it changes, sent as a page with paragraphs, lines and the scroll; the app in front (`windows.jsonl`) |
| `capture/restart.py` | `python -m capture restart`: stops the running receiver and its eye tracker (found by command line) and starts the sign-in command again; the code stamp `/status` reports |
| `capture/doctor.py` | `python -m capture doctor`: checks receiver, extension, tracker, screen reading, accuracy, logs and autostart |
| `capture/supervise.py` | Running the eye tracker from its own environment, restarting it when it stops |
| `capture/autostart.py` | The per-user sign-in entry (registry `Run` key, windowless `pythonw.exe`) |
| `capture/history.py` | Importing a Chromium `History` file |

Without `--receive` the receiver starts paused, as before. A pause is remembered in
`vault/capture/paused` until resumed. Screen reading needs `mss` and the `winrt-*` packages, installed
on Windows by `uv sync`; `uv sync --extra ocr` adds RapidOCR for a Windows without an OCR language.
