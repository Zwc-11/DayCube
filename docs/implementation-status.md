# Implementation status

This is a foundation milestone, not a claim that the complete technical spec has shipped.

| Component | Implemented | Remaining |
| --- | --- | --- |
| Repository | npm workspaces, uv project, folder boundaries, environment template, commands, lockfiles | Next.js/pnpm migration if wanted; existing Vite/npm stack retained |
| Contracts | 8 JSON Schemas, generated Pydantic/TypeScript, full runtime validation, fixture checks | Production schemas for scenes, capture logs, derived API payloads |
| Vault | Initialize/seed/read/validate, atomic file writes, reference checks, stats, name overrides | Transactional multi-file writes, cross-process locking, full deletion/export UI |
| Fixture | 5 visits, 60 fixes, 14 events, 2 OCR records/6 blocks, 1 synthetic transcript moment | Licensed real media and a reconstructed scene |
| Skeleton | Timestamp/coordinate model, Arc-compatible GPX parser, barometric floor estimator (`skeleton/floors.py`, [floors.md](floors.md)) | Health/Timeline/recorder parsers, DBSCAN visits, wiring floors into the day build, indoor PDR/map matching |
| Mind | Tested interval-clipped attention calculation; live gaze placement on text blocks by a filter that allows for webcam error, click-based live accuracy and offset correction, and dwell detection (`capture/attention.py`, `capture/anchors.py`), checked against simulated webcam gaze (`eval/gaze_sim.py`) | Native sampler, OCR execution, event synthesis from capture logs, reading-state classifier |
| Capture | Receiver for typed browser records and fixations with idempotent ids, per-kind daily logs, remembered pause, dwell signals and command hook, eye-tracker supervision, sign-in autostart, Opera history import ([capture.md](capture.md)) | Tray icon, native window/screenshot collectors, deletion of hours; Windows autostart, lock/idle/camera checks not yet run on Windows |
| Gaze | EyeTrax calibration with held-out error, One Euro smoothing, I-DT fixations with a noise-adaptive threshold, fixation coverage and noise reported, webcam released while paused, locked, away or in a call (`services/gaze`); run on the Windows PC's webcam at 30 frames/s | Multi-monitor; reading-state classifier |
| Browser extension | Navigation with transitions, focus, titles, idle, searches, clicks with position, selections, media, on-screen text with viewport geometry, persistent queue, pause shortcut, dwell marks; tested in Chromium against a scratch vault, including 200% zoom | Running in Opera on Windows; viewport geometry checked against a real window layout |
| Moments | FFmpeg metadata/audio helpers, transcript and moment reads | Consent/blur, WhisperX diarization, scenes, speaker event regeneration, synchronized playback |
| Search | One vault database across days (`vault/daycube.sqlite`): per-day transactional replace by source stamp, FTS5, int8 bge-small vectors with a sign-bit first pass and day/time scoping, vector reuse for unchanged text, coverage-weighted rank fusion ([search.md](search.md)) | Entity, mention and link tables; evaluation on real questions |
| Ask | Source excerpts/citations, place/time/moment offset, SSE, explicit no-match results | Query rewrite, soft hints, generated phrasing with semantic verification |
| Integrations | Lazy Anthropic/FastEmbed/Modal adapters, cached Overpass adapter | Authenticated account validation and live integration tests |
| GPU jobs | Optional transcription and relative-depth function definitions | Deployment, GPU validation, splats, consent-gated ingestion |
| API | Local read/action endpoints and OpenAPI docs, mutation auth, media confinement, index job SSE | Durable job queue, complete ingestion stages, privacy purge |
| Viewer | Existing design under `apps/viewer`, API day/event hydration, token binding, live Ask/reindex and sync smoke tests; walking routes (grid A* around stage geometry, stair flights between floors, upper-floor cutaway, walk-paced playback) | Replace remaining prototype moment/OCR/graph/evaluation data with live views; route over venue packs and OSM footprints instead of stage presets |
| Evaluation | 4 synthetic questions, test runner and saved smoke score | 50 real questions, grounding audit, calibration metrics, volunteer experiment |

The frontend draft is preserved separately under `apps/viewer` after concurrent frontend work
moved it there. It is not imported by the new backend or shared-contract packages.
All generated sample facts and stage geometry are labeled illustrative. Unknown coordinates
stay null. Floor labels from the fixture are fictional; actual floor anchoring is not claimed.
When a visit has no measured indoor positions, the viewer walks the character between stage
spots (entrance, desk, table, stairs) at the times of its records. That walk is illustrative,
not a measured trajectory.
