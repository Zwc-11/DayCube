# DayCube for Windows (the desktop app)

`apps/desktop` is one Windows app with everything the website has (the days in 3D, the notes graph,
search, Settings, eye tracking), plus capture: it starts the vault API, the capture receiver and its
eye tracker, shows the viewer in its own window and sits in the tray. It needs no repository, git,
Node or Python on the PC. Built with Tauri 2 (Rust, Edge WebView2).

## Install and first run

1. Run `DayCube_<version>_x64-setup.exe` (per user, no administrator rights). CI builds it on every
   push that touches the app (workflow **DayCube for Windows**, artifact `daycube-windows`).
2. The first start asks for the vault. An existing vault (`E:\DAYCUBE\vault`, ...) is found and used
   as it is; otherwise one is created in `Documents\DayCube\vault`. The `.env` beside the vault
   (`E:\DAYCUBE\.env`) is read as before.
3. The app replaces the `DayCube capture` sign-in entry that `capture.cmd install-autostart` wrote,
   and stops a receiver started from the checkout, so one receiver runs on 127.0.0.1:7700. To go back
   to the checkout: tools window, untick **Run capture** and **Start at sign-in**, then
   `.\capture install-autostart` in the checkout.
4. Opera extension: tools window › **Opera extension** shows the folder to load unpacked. Its token is
   the vault's, as before.

Closing the window keeps DayCube and recording running in the tray. **Quit DayCube** in the tray
stops everything until the next sign-in.

## Tray and tools

| Tray item | What it does |
|---|---|
| Status line | Recording, paused, or why the eye tracker is off (from the receiver's `/status`) |
| Open DayCube | The viewer window |
| Pause / Resume recording | `POST /pause` or `/resume` on the receiver, with the vault token |
| Delete the last hour… | After a confirmation, `DELETE /api/recent?minutes=60`: the last hour leaves the vault and everything made from it ([capture](capture.md#deleting-a-span)) |
| Calibrate the eye tracker | `python -m gaze calibrate` with the bundled eye-tracker runtime |
| Doctor and GPU packs… | The tools window |
| Start at sign-in | The shared `DayCube capture` Run value, pointing at `DayCube.exe --background` |

The tools window (`DayCube.exe --tools` opens it directly) runs `capture doctor`, installs the GPU
packs, and opens the vault, logs and extension folders.

## GPU packs

Speech (transcripts, speakers, emotion) and 3D memories (SHARP, recon) are CUDA PyTorch environments
of 3–4 GB each. NSIS installers and GitHub release files are limited to 2 GB, so they are not in the
installer: **Install** in the tools window runs the bundled `uv sync --locked` on each service's own
`pyproject.toml` and `uv.lock` into `%LOCALAPPDATA%\app.daycube.desktop\packs\<name>`, then restarts
the receiver, which finds them. The 3D pack pulls SHARP from GitHub, so it needs git on the PATH.
Models download on first use to `%LOCALAPPDATA%\app.daycube.desktop\cache`, not the install folder.

## How it fits together

```
DayCube.exe (Tauri) ── tray, settings.json, job objects (children die with the app)
 ├─ viewer window ── http://127.0.0.1:<port>/  (token injected before the page's scripts, this origin only)
 ├─ runtime\core\python -m uvicorn ingest.desktop:create_desktop_app --factory
 │    /      the built viewer (index.html for its routes, hashed assets cached for a year)
 │    /api   the vault API, unchanged (its lifespan is run by the parent app)
 └─ runtime\core\python -m capture run --receive --gaze   (127.0.0.1:7700)
      ├─ runtime\gaze\pythonw -m gaze track
      └─ packs\speech, packs\recon (when installed)
```

- One origin: the viewer already calls `/api/...` (the Vite dev server proxies that prefix), so
  serving both from one loopback port needs no path, proxy or CORS change. The API port is 7701, or
  another free port when 7701 is taken (a running `viewer.cmd`).
- Token: the app reads `vault\.token` and runs a script in the viewer window, before the page, that
  sets `window.__DAYCUBE_DESKTOP__` only when the page's origin is the app's. `backendBridge.ts`
  starts with it, so Ask, notes, rename and reindex work without **Vault connection**. Links to other
  sites open in the default browser; the viewer window never navigates away from its origin and has
  no IPC. Only the app's local pages (startup, tools) can call its commands.
- Loopback only: the server answers requests addressed to `127.0.0.1` or `localhost` and refuses
  any other `Host`, so a web page that rebinds its own name to 127.0.0.1 (DNS rebinding) cannot
  read the vault's GET endpoints, which need no token.
- Python: uv's managed CPython 3.11 builds (python-build-standalone), copied as folders; packages
  installed by hash from `uv export --locked` of `services/capture-win` (with the `desktop` extra,
  which adds meaning search) and of `services/gaze`. The sources stay plain files found through a
  relative `daycube.pth`, so `PROJECT_ROOT` is the install's `src` folder.
- What the bundle leaves out (`Environment` in `bundle-runtimes.py`, each checked by importing and
  running what the app uses once the environment is assembled): COLMAP, which only the 3D pack runs;
  Open3D's notebook and web viewers (Plotly, Dash, Flask, Jupyter widgets) and renderer assets; the
  second copy of OpenCV; test suites, type stubs, headers, pip and setuptools. The standard library
  and third-party packages are shipped as bytecode alone, as Python's embeddable distribution ships
  its standard library; tracebacks inside them show the file and line without its text. DayCube's
  own sources stay sources.
- Variables the app sets for every Python child: `DAYCUBE_VAULT_ROOT`, `DAYCUBE_ENV_FILE`,
  `DAYCUBE_AUTO_RESTART=false` (code changes only with an app update), `DAYCUBE_GAZE_PYTHON`,
  `DAYCUBE_SPEECH_PYTHON`, `DAYCUBE_RECON_PYTHON`, `HF_HOME`, `FASTEMBED_CACHE_PATH`,
  `PYTHONNOUSERSITE=1`; `PYTHONHOME` and `PYTHONPATH` are removed.
- Logs, in `vault\logs`: `desktop.log` (what the app did), `desktop-api.log`,
  `desktop-receiver.log`, plus the receiver's own `capture.log` and `gaze.log` as before.

## Build

On Windows, from the repository:

```powershell
npm ci
npm run build                                                   # the viewer
uv run --no-project --python 3.11 python apps/desktop/scripts/bundle-runtimes.py
npm run build --workspace @daycube/desktop                      # NSIS installer in src-tauri\target\release\bundle\nsis
```

Development against a checkout's own environments: `DAYCUBE_DEV_ROOT=E:\DAYCUBE` then
`npm run tauri dev --workspace @daycube/desktop`. `DAYCUBE_RESOURCES=<folder>` runs a bundle made by
`bundle-runtimes.py` without installing it.

## Verification

- `cargo test` in `apps/desktop/src-tauri`: process trees end with their parent (job objects on
  Windows), restart and back-off, loopback HTTP, ports, the install layout and the variables passed to
  Python, settings and vault choice, the sign-in entry (Windows), the token script and navigation
  guard, packs, the self-test's judgement, the tray's status text.
- `services/ingest/tests/test_desktop.py`: one origin, route fallback, cache headers and 304s, loopback
  `Host` only, token rules under `/api`, the API's lifespan when mounted, the token probe, the request record.
- `apps/viewer/e2e/desktop.spec.ts`: an injected token is bound at load and authorizes Ask.
- `DayCube.exe --self-test --vault <dir> --report <file>`: starts as usual, then checks that the API
  answers, the window loaded the viewer and read its data, the injected token is on the page and
  accepted (a probe the app runs inside the window), and the receiver answers. A watchdog ends a
  stalled run with a report naming the last step. Exit code 0 only when all hold.
- CI (`.github/workflows/desktop.yml`): builds the installer on `windows-latest`, installs it silently,
  seeds a vault with the installed Python, starts a receiver the way the checkout does, runs the
  self-test, checks the app took over that receiver, screenshots the window, and checks that no
  process from the install is left running after it exits.

## Updates

Install a newer build over the old one: settings, the vault and installed packs are kept. The installer
closes a running DayCube first; its Python processes end with it (job objects). An in-app updater needs signed
releases (a signing key stored as a repository secret and a release workflow); it is not built in
until those exist, so the app carries no code that cannot run.
