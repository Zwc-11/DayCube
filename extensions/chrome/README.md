# Chrome MV3 starter

Load this folder at `chrome://extensions` → Developer mode → Load unpacked. It is disabled
by default. Follow `services/capture-win/README.md` before enabling browser metadata capture.

Implemented: focused active-tab URL/title, throttled scroll and viewport metadata; local receiver
authentication; domain exclusions; password-page suppression; options; toolbar status badge.
Not implemented: DOM text blocks, screenshots, gaze mapping, reliable queued delivery or replay.
Each accepted signal is sent immediately (at most once every 5 seconds per tab), not batched.
Failed requests are dropped with ERR shown. This is a development starter, not a complete recorder.
