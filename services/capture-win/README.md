# Local browser metadata receiver

The receiver is a starter for port 7700. It accepts authenticated URL/title/scroll/viewport
records from the Chrome extension and writes to `vault/capture/<local-date>/browser.jsonl`.
It starts paused and does not sample native windows, take screenshots, open the webcam or record audio.

Install/run independently from the repository root:

```powershell
uv sync --project services/capture-win --python 3.11
uv run --project services/capture-win python -m capture
```

Explicitly allow browser delivery with `python -m capture --receive` in that command, or call
authenticated `/resume`. Install optional libraries as work on each collector begins:

```powershell
uv sync --project services/capture-win --extra windows --extra ocr --extra gaze
```

Load `extensions/chrome` as an unpacked extension. Open its options from the toolbar action,
paste the vault token, add domain exclusions, and opt in. Incognito tabs and pages with visible
password inputs are suppressed. The token stays in extension-local storage and is not sent to
content scripts. The toolbar badge shows OFF, ON or ERR. Add the extension ID to `.env` as
`DAYCUBE_EXTENSION_ID` if the receiver needs its CORS origin, then restart it.

The exclusion YAML is reserved for the future native sampler. Browser exclusions currently live
in extension options. There is no tray indicator, hotkey, screenshot/gaze pipeline, deletion,
automatic raw-to-vault synthesis or full privacy workflow yet. Use fictional/test pages while
developing this starter. Native capture and real personal recording are future milestones.
