# Capture receiver

The receiver on `127.0.0.1:7700` takes records from the DayCube browser extension and fixations
from the eye tracker (`services/gaze`), writes them to `vault/capture/<local date>/`, works out
which text block the eyes are on, and detects dwells. It can start the eye tracker and itself at every
Windows sign-in. Setup, what is recorded, and limits: [docs/capture.md](../../docs/capture.md).

```powershell
uv sync --project services/capture-win
uv run --project services/capture-win python -m capture install-autostart   # now and at every sign-in
uv run --project services/capture-win python -m capture run --receive --gaze # or run it in this console
uv run --project services/capture-win python -m capture import-history      # Opera's history into the vault
uv run --project services/capture-win python -m capture doctor              # is it working? from what it recorded
```

| Module | Does |
| --- | --- |
| `capture/api.py` | `GET /status`, `POST /pause`, `/resume`, `/events` (browser records), `/gaze` (fixations and tracker state) |
| `capture/records.py` | Record types the receiver accepts |
| `capture/attention.py` | Which text block the eyes are on (a filter that allows for webcam error); dwell detection |
| `capture/anchors.py` | Clicks as calibration points: the tracker's live error and an offset correction |
| `capture/doctor.py` | `python -m capture doctor`: checks receiver, extension, tracker, accuracy, logs and autostart |
| `capture/supervise.py` | Running the eye tracker from its own environment, restarting it when it stops |
| `capture/autostart.py` | The per-user sign-in entry (registry `Run` key, windowless `pythonw.exe`) |
| `capture/history.py` | Importing a Chromium `History` file |

Without `--receive` the receiver starts paused, as before. A pause is remembered in
`vault/capture/paused` until resumed. The `windows` and `ocr` extras are reserved for the native
window sampler and screenshot OCR, which are not implemented yet.
