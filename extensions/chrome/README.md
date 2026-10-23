# DayCube Capture (Chromium MV3: Opera, Chrome, Edge)

Load this folder unpacked (Opera: `opera://extensions`, Developer mode, Load unpacked), then
paste the vault token in its options and turn recording on. Full setup and what it records:
[docs/capture.md](../../docs/capture.md).

| File | Does |
| --- | --- |
| `background.js` | Navigation (with transition type), focus, titles, idle, searches; the delivery queue in extension storage; pause and the `Alt+Shift+D` shortcut; dwell actions |
| `content.js` | Text on screen with positions, viewport geometry, heartbeats, clicks, selections, media; the dwell mark |
| `lib.js` | Search parsing, exclusions, queue bounds (unit-tested in `extensions/tests`) |
| `options.*` | Token, exclusions, dwell action, live status |

The toolbar button pauses and resumes (badge `REC`, `II`, `ERR`, `OFF`). The token stays in
extension storage and is never given to pages. Private windows are not allowed. Pages showing a
password field report nothing, and typed text is never read.

Tests: `npm run test:extension` (unit tests, then this folder loaded into Chromium against a
receiver in a scratch vault).
