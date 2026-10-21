# Local runbook

From the repository root on Windows:

```powershell
uv sync --locked --python 3.11
npm ci
npm run schemas:check
uv run daycube init
npm run demo
```

To customize configuration, copy `.env.example` to `.env` if you have not already created a
local `.env`. Defaults work without it. Files in `vault/`, including `.token`, are private local
runtime data; do not commit them. Setup generates a token and retains it on later runs.
The demo never overwrites an existing day. To regenerate fixture source files for development,
run `uv run python scripts/build-fixture.py`; this changes only checked-in fictional fixture files.

Run API and viewer separately with `npm run api` and `npm run dev`. Ports are 7701 and 3000;
both bind to `127.0.0.1`. A port conflict is an error, not a silent move to another port.
The raw browser receiver is a separate explicit command, paused by default; see its README.

## Verification

```powershell
npm run test:api
npm run lint:api
npx playwright install chromium
npm run test:e2e:api
uv run daycube doctor
uv run python eval/score_retrieval.py
```

Browser tests create `.daycube-e2e-vault`, use only its generated token and do not record traces
or screenshots. They run the API on port 17701 without starting or changing the frontend draft.
The four-question evaluation tests fictional evidence retrieval only, not general recall quality.

## Recovery

- API is offline: start `npm run api`; check `/health` and `/docs`.
- No days listed: run `npm run demo:seed`. Existing days are preserved.
- Invalid vault: run `uv run daycube validate --date YYYY-MM-DD` for the exact contract failure.
- Invalid/stale index: run `uv run daycube index --date YYYY-MM-DD`; it is rebuilt from files.
- Ask is unauthorized: use the current local `vault/.token`; provider API keys are unrelated.
- Capture forwarding returns 503: the optional receiver is not running.
- An ingest step returns 501: that stage is planned, not a failed background process.
- A media helper cannot find ffmpeg: install FFmpeg/ffprobe and put its bin directory on PATH.

No deletion command is provided until all raw records, media, events, summaries, graph edges
and index rows can be purged consistently. Keep development on synthetic/test data for now.
