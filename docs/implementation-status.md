# Implementation status

This is a foundation milestone, not a claim that the complete technical spec has shipped.

| Component | Implemented | Remaining |
| --- | --- | --- |
| Repository | npm workspaces, uv project, folder boundaries, environment template, commands, lockfiles | Next.js/pnpm migration if wanted; existing Vite/npm stack retained |
| Contracts | 8 JSON Schemas, generated Pydantic/TypeScript, full runtime validation, fixture checks | Production schemas for scenes, capture logs, derived API payloads |
| Vault | Initialize/seed/read/validate, atomic file writes, reference checks, stats, name overrides | Transactional multi-file writes, cross-process locking, full deletion/export UI |
| Fixture | 5 visits, 60 fixes, 14 events, 2 OCR records/6 blocks, 1 synthetic transcript moment | Licensed real media and a reconstructed scene |
| Skeleton | Timestamp/coordinate model, Arc-compatible GPX parser, barometric floor estimator (`skeleton/floors.py`, [floors.md](floors.md)) | Health/Timeline/recorder parsers, DBSCAN visits, wiring floors into the day build, indoor PDR/map matching |
| Mind | Tested interval-clipped attention calculation; live gaze placement on text blocks by a filter that allows for webcam error, click-based live accuracy and offset correction, dwell detection, and line-by-line reading decoded from return sweeps and page layout with per-block reading records (`capture/attention.py`, `capture/anchors.py`, `capture/reading.py`), checked against simulated webcam gaze (`eval/gaze_sim.py`) and in real time in Chromium | Native sampler, OCR execution, event synthesis from capture logs, reading-state classifier |
| Capture | Receiver for typed browser records and fixations with idempotent ids, per-kind daily logs, remembered pause, dwell signals and command hook, eye-tracker supervision, sign-in autostart, Opera history import ([capture.md](capture.md)) | Tray icon, native window/screenshot collectors, deletion of hours; Windows autostart, lock/idle/camera checks not yet run on Windows |
| Gaze | Head-aware gaze model (head pose from the face each frame, eye direction within the head learned at calibration, geometry to the screen; `gaze/head.py`), benchmarked on MPIIFaceGaze against EyeTrax (`eval/head_bench.py`: 43 mm against 78 mm median error on other days); calibration with held-out error and mirrored-camera detection, One Euro smoothing, I-DT fixations with a noise-adaptive threshold, fixation coverage, noise and head position since calibration reported, webcam released while paused, locked, away or in a call (`services/gaze`); EyeTrax version run on the Windows PC's webcam at 30 frames/s | Head-aware model on the Windows PC's webcam; online correction of the eye regression from clicks; multi-monitor; reading-state classifier |
| Browser extension | Navigation with transitions, focus, titles, idle, searches, clicks with position, selections, media, on-screen text with viewport geometry and visual lines, persistent queue, pause shortcut, dwell marks, line-being-read marker; tested in Chromium against a scratch vault, including 200% zoom | Running in Opera on Windows; viewport geometry checked against a real window layout |
| Moments | FFmpeg metadata/audio helpers, transcript and moment reads | Consent/blur, WhisperX diarization, scenes, speaker event regeneration, synchronized playback |
| Search | One vault database across days (`vault/daycube.sqlite`): per-day transactional replace by source stamp, FTS5, int8 bge-small vectors with a sign-bit first pass and day/time scoping, vector reuse for unchanged text, coverage-weighted rank fusion; computer capture as units (visits, reads, dwells, looks, searches, clicks, copies, media); knowledge graph of entities named exactly (sites, pages, searches, course codes, due dates, places, people) with mentions and links, `/graph` and `/entity` ([search.md](search.md)); data center (`/data`, `daycube data`, [storage.md](storage.md)) | Language-model entity and commitment extraction; evaluation on real questions |
| Notes | Obsidian-compatible `vault/notes`: daily, source and course notes with `[[links]]`, generated regions that keep the person's writing, identity in front matter; the person's writing and links read back into search and the graph; refreshed by the receiver every 10 minutes ([notes.md](notes.md)) | Weekly review, explorer and calendar export of deadlines |
| Ask | Source excerpts/citations, place/time/moment offset, SSE, explicit no-match results | Query rewrite, soft hints, generated phrasing with semantic verification |
| Integrations | Lazy Anthropic/FastEmbed/Modal adapters, cached Overpass adapter | Authenticated account validation and live integration tests |
| GPU jobs | Optional transcription and relative-depth function definitions | Deployment, GPU validation, splats, consent-gated ingestion |
| API | Local read/action endpoints and OpenAPI docs, mutation auth, media confinement, index job SSE | Durable job queue, complete ingestion stages, privacy purge |
| Viewer | Existing design under `apps/viewer`, API day/event hydration, token binding, live Ask/reindex and sync smoke tests; live knowledge graph and data center (Settings, and Your data on the calendar); walking routes (grid A* around stage geometry, stair flights between floors, upper-floor cutaway, walk-paced playback) | Replace remaining prototype moment/OCR/evaluation data with live views; open days that have only computer capture (they are searchable and in the graph, but not on the calendar); route over venue packs and OSM footprints instead of stage presets |
| Evaluation | 4 synthetic questions, test runner and saved smoke score | 50 real questions, grounding audit, calibration metrics, volunteer experiment |

The frontend draft is preserved separately under `apps/viewer` after concurrent frontend work
moved it there. It is not imported by the new backend or shared-contract packages.
All generated sample facts and stage geometry are labeled illustrative. Unknown coordinates
stay null. Floor labels from the fixture are fictional; actual floor anchoring is not claimed.
When a visit has no measured indoor positions, the viewer walks the character between stage
spots (entrance, desk, table, stairs) at the times of its records. That walk is illustrative,
not a measured trajectory.
