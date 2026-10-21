# Implementation status

This is a foundation milestone, not a claim that the complete technical spec has shipped.

| Component | Implemented | Remaining |
| --- | --- | --- |
| Repository | npm workspaces, uv project, folder boundaries, environment template, commands, lockfiles | Next.js/pnpm migration if wanted; existing Vite/npm stack retained |
| Contracts | 8 JSON Schemas, generated Pydantic/TypeScript, full runtime validation, fixture checks | Production schemas for scenes, capture logs, derived API payloads |
| Vault | Initialize/seed/read/validate, atomic file writes, reference checks, stats, name overrides | Transactional multi-file writes, cross-process locking, full deletion/export UI |
| Fixture | 5 visits, 60 fixes, 14 events, 2 OCR records/6 blocks, 1 synthetic transcript moment | Licensed real media and a reconstructed scene |
| Skeleton | Timestamp/coordinate model and Arc-compatible GPX parser | Health/Timeline/recorder parsers, DBSCAN visits, floors, indoor PDR/map matching |
| Mind | Tested interval-clipped attention calculation | Native sampler, OCR execution, fixation detection, gaze calibration, event synthesis |
| Capture | Authenticated opt-in raw browser receiver, pause/resume | Tray/hotkey, native collectors, reliable queues, exclusions end-to-end, deletion |
| Chrome | Disabled-by-default active/focused URL/title/scroll, exclusions, options, badge | DOM blocks, batching, persistent queue, live Chrome execution verification |
| Moments | FFmpeg metadata/audio helpers, transcript and moment reads | Consent/blur, WhisperX diarization, scenes, speaker event regeneration, synchronized playback |
| Search | Repeatable FTS5 rebuilds, quoted-token queries, source-stamp invalidation, tested RRF helper | sqlite-vec index and hybrid fusion in the live query route |
| Ask | Source excerpts/citations, place/time/moment offset, SSE, explicit no-match results | Query rewrite, soft hints, generated phrasing with semantic verification |
| Integrations | Lazy Anthropic/FastEmbed/Modal adapters, cached Overpass adapter | Authenticated account validation and live integration tests |
| GPU jobs | Optional transcription and relative-depth function definitions | Deployment, GPU validation, splats, consent-gated ingestion |
| API | Local read/action endpoints and OpenAPI docs, mutation auth, media confinement, index job SSE | Durable job queue, complete ingestion stages, privacy purge |
| Viewer | Preserved separate frontend prototype under `apps/viewer` | Connect it through the typed API client in a later milestone |
| Evaluation | 4 synthetic questions, test runner and saved smoke score | 50 real questions, grounding audit, calibration metrics, volunteer experiment |

The frontend draft is preserved separately under `apps/viewer` after concurrent frontend work
moved it there. It is not imported by the new backend or shared-contract packages.
All generated sample facts and stage geometry are labeled illustrative. Unknown coordinates
stay null. Floor labels from the fixture are fictional; actual floor anchoring is not claimed.
