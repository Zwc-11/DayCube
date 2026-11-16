# DayCube for Windows

The Windows app: the interface (`apps/viewer`) in its own window, with the vault API, capture and
the eye tracker run from the tray. A Tauri 2 shell (`src-tauri/`), its own startup and tools pages
(`ui/`), and relocatable Python runtimes bundled from the repository's lockfiles
(`scripts/bundle-runtimes.py`). How it works, what it changes on the PC and how it is verified:
[docs/desktop.md](../../docs/desktop.md).

```powershell
npm ci; npm run build                                            # the interface
uv run --no-project --python 3.11 python apps/desktop/scripts/bundle-runtimes.py
npm run build --workspace @daycube/desktop                       # src-tauri\target\release\bundle\nsis
```

| Path | Does |
| --- | --- |
| `src-tauri/src/lib.rs` | Startup, the windows, the token handed to the viewer, commands for the app's own pages |
| `src-tauri/src/services.rs`, `process.rs` | Python services kept running; Windows job objects, so nothing outlives the app |
| `src-tauri/src/tray.rs`, `autostart.rs` | The tray; start at sign-in (the `DayCube capture` Run value) |
| `src-tauri/src/packs.rs` | Speech and 3D GPU packs, installed with the bundled uv from their lockfiles |
| `src-tauri/src/selftest.rs` | `DayCube.exe --self-test`: proof the install works, for CI |
| `scripts/smoke-test.ps1` | CI: silent install, self-test, screenshot, nothing left running |
