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

The frontend loads the vault day through `/api` on startup. To enable authenticated actions:

1. Open the viewer and click **API Contract** in the bottom bar.
2. Copy your token in PowerShell: `Get-Content -Raw .\vault\.token | Set-Clipboard`.
3. Paste it into the vault-token field and click **Bind Token**.
4. Click **GET /api/health & /api/day/2026-10-20** to reload the vault day.
5. Use **POST /api/ingest/2026-10-20** to rebuild the search index, then Ask Memory.

Try “Where was I when we decided the demo order?”. Live answers quote source evidence and
select its place/time. The token is held only in page memory, so bind it again after a browser
reload. Without a bound token, Ask runs the frontend's standalone demo engine. Decorative scenes,
the moment/OCR inspectors, graph and evaluation panels still contain prototype data; they do not
mean that capture, reconstruction or the volunteer study has run.

## Structure

```text
apps/viewer/                 React/Vite frontend with a vault/API bridge and illustrative dioramas
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

The frontend's rendering design is preserved under `apps/viewer`. Its bridge uses the typed API
client and converts the API's seconds-since-midnight into the renderer's decimal-hour format.

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
npm run test:e2e:viewer        # Token binding, reindex and live Ask through the frontend
uv run python eval/score_retrieval.py
```

Make targets mirror the main commands when Make is available. On Windows the npm/uv commands
work without Make or shell activation. `npm run demo` starts both servers; Ctrl+C
stops them. Seed first with `npm run demo:seed`. Demo does not start capture, call a model or deploy a GPU job.

Optional libraries, account configuration and the remaining system dependencies are documented
in [dependencies](docs/dependencies.md) and [the runbook](docs/runbook.md).
