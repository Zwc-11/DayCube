# DayCube Capture (Chromium MV3: Opera, Chrome, Edge)

Load this folder unpacked (Opera: `opera://extensions`, Developer mode, Load unpacked), then
paste the vault token in its options and turn recording on. Full setup and what it records:
[docs/capture.md](../../docs/capture.md).

| File | Does |
| --- | --- |
| `background.js` | Navigation (with transition type), focus, titles, idle, searches; the delivery queue in extension storage; pause and the `Alt+Shift+D` shortcut; dwell actions; after an install or update, the page scripts injected into tabs already open |
| `content.js` | Text on screen (including inside web components' open and closed shadow roots) with positions and each block's visual lines, viewport geometry, heartbeats, clicks, selections, media; the dwell mark and the line-being-read marker |
| `lib.js` | Search parsing, exclusions, queue bounds (unit-tested in `extensions/tests`) |
| `options.*` | Token, exclusions, dwell action, the line marker, live status (with the line being read) |

Permissions: `scripting` and access to `http://*/*` and `https://*/*` let the service worker put
the page scripts into tabs that were open when the extension was installed or updated (an update
cuts the old scripts off; without this those tabs record nothing until reloaded). The page scripts
already run on every http and https page, so this adds no site access.

The toolbar button pauses and resumes (badge `REC`, `II`, `ERR`, `OFF`). The token stays in
extension storage and is never given to pages. Private windows are not allowed. Pages showing a
password field report nothing, and typed text is never read.

Tests: `npm run test:extension` (unit tests, then this folder loaded into Chromium against a
receiver in a scratch vault; `update.e2e.spec.ts` updates it in place with a tab open).
