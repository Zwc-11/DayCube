# DAYCUBE

A local-first spatial memory system. This repository now contains a working **backend/API
foundation** alongside a separately maintained frontend prototype. It is not yet the complete capture/reconstruction
system described in [the technical specification](docs/daycube-technical-spec.md).

## Start locally

Prerequisites: Node 20.19+ and [uv](https://docs.astral.sh/uv/). Setup selects Python 3.11.
Core libraries are already installed in this workspace, and the synthetic sample is seeded.

```powershell
npm run setup
npm run demo:seed
npm run demo
```

- Viewer: <http://127.0.0.1:3000>
- API and interactive endpoint documentation: <http://127.0.0.1:7701/docs>
- Health/capabilities: <http://127.0.0.1:7701/health>

To try the backend, open the API docs, click Authorize and paste the contents of your local
`vault/.token`. Sample `/ask` questions include “Where was I when we decided the demo order?”
and “hinge inference loop”. Backend answers quote matching evidence verbatim and return the
place/time. The preserved frontend currently has its own mock data and is not wired to this API.

## Structure

```text
apps/viewer/                 Separately maintained React/Vite frontend prototype, preserved intact
packages/schemas/            Canonical JSON Schemas, generated TypeScript types, Zod validators
packages/api-client/         Typed browser client with response validation
services/ingest/             FastAPI, vault storage, FTS search, adapters and tests
services/capture-win/        Opt-in browser metadata receiver and optional native-library groups
extensions/chrome/          Disabled-by-default Chrome MV3 starter
jobs/modal/                 Optional transcription/depth cloud job definitions
fixtures/sample-day/        Fictional day with 5 visits, 60 fixes and 14 events
venues/                     Fictional venue pack
eval/                       Four-question fixture smoke evaluation; human study still planned
docs/                       API, architecture, dependencies, runbook and implementation status
scripts/                    Fixture generation and cross-language contract checks
vault/                      Local runtime data and token; ignored by Git
```

The other frontend draft was moved into `apps/viewer` during concurrent development. It remains
separate from the backend and shared contracts, as requested. Its mock decimal-hour time format
is not the API's seconds-since-midnight format. Use `packages/api-client` when connecting it later.

## Working now

- Atomic vault JSON/JSONL writes, schema validation and reference checks.
- Day, layer, event, skeleton, graph, moment and allowlisted media reads.
- Authenticated event writes, place renaming, speaker-map saves and index jobs.
- Rebuildable SQLite FTS5 search, extractive cited answers and SSE responses.
- Eight shared JSON Schema contracts with generated Pydantic and TypeScript models.
- GPX parser, attention helper, FFmpeg probe/audio helpers, optional integration adapters.
- Python tests, JavaScript contract tests, real-browser API checks and a CI workflow.

Native capture, OCR execution, gaze calibration, visit/floor inference, raw-browser synthesis,
semantic retrieval, generative answers, deletion, splats and full moment reconstruction remain
future milestones. Unimplemented ingest stages and hour deletion return HTTP 501.
The sample moment has a synthetic transcript and **no source video/audio or GLB**.
See [implementation status](docs/implementation-status.md) for exact boundaries.

## Commands

```powershell
npm run api                   # API only, loopback port 7701
npm run dev                   # Starter viewer only, loopback port 3000
npm run demo:seed             # Seed if absent, then rebuild local index
uv run daycube validate --date 2026-10-20
uv run daycube index --date 2026-10-20
uv run daycube doctor
npm run schemas               # Regenerate Pydantic + TypeScript contracts
npm run test:api               # Drift checks, JS contracts and Python tests
npm run build                 # Build the separately maintained frontend
npm run lint:api
npx playwright install chromium
npm run test:e2e:api           # Real-browser API checks on an isolated test vault/port
uv run python eval/score_retrieval.py
```

Make targets mirror the main commands when Make is available. On Windows the npm/uv commands
work without Make or shell activation. `npm run demo` starts both servers; Ctrl+C
stops them. Seed first with `npm run demo:seed`. Demo does not start capture, call a model or deploy a GPU job.

Optional libraries, account configuration and the remaining system dependencies are documented
in [dependencies](docs/dependencies.md) and [the runbook](docs/runbook.md).
