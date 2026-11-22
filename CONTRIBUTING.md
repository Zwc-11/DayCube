# Contributing

Run every command from the repository root. Setup is in the [README](README.md#start-from-source).

## Before you send a change

```powershell
npm run format     # Ruff: fix what it can, then format
npm run check      # lint, contracts, Python and unit tests, viewer build
```

`npm run check` is what CI runs on Windows and Linux. The browser suites are separate
(`npm run test:e2e:viewer`, `npm run test:extension`); run the one for the surface you changed.

## Code

The bar is [DeepInfer](https://github.com/deepseek-ai/DeepInfer): small modules with one job,
typed public functions, comments that give the reason, the unit or the invariant and nothing
else, one formatter and one lint configuration, and no code that nothing runs.

- **Less code.** Delete what a change makes unused, in the same change. A helper needs two
  callers; a benchmark or script needs a reference from the docs or `package.json`. `eval/` keeps
  only what reproduces a number the docs state.
- **One place per thing.** Routes live in `services/ingest/ingest/api/`, one module per area.
  JSON Schemas in `packages/schemas` are the contracts; both language bindings are generated from
  them (`npm run schemas`). Configuration is read in `ingest/config.py`.
- **Python.** Ruff with the rule families `E`, `F`, `UP`, `B`, `SIM`, `I`, `ANN`
  ([pyproject.toml](pyproject.toml)). Public functions are annotated. Each service has its own
  environment (`services/*/pyproject.toml`); the core never imports a GPU package at module level.
- **TypeScript.** `npm run typecheck`. Components read typed application data; storage, search and
  indexing stay in the API.
- **Files in the vault** are read through `Vault.snapshot(day)` and written through
  `Vault.write_json`, `write_lines` or `append_lines`, so caches and compacted days stay correct.
  Logs only grow: append, never read and rewrite.
- **Resources have an owner.** SQLite connections close before a file is replaced (Windows);
  timers, subscriptions, animation frames and GPU buffers are released by the scope that made them.
- **Bounded.** Every cache has a capacity; work on a day is bounded by that day, never the vault.

## Tests

- `services/*/tests/`: pytest, no network, no GPU, no camera. They run on synthetic or fixture
  data and must pass on a machine with none of the optional packs installed.
- `apps/viewer/e2e/`, `extensions/tests/`: Playwright, against the fictional sample vault.
- `eval/`: simulations and benchmarks with their recorded results (`eval/results`). They may need
  a dataset, a GPU or minutes of time, and are not collected by pytest.

A logic change comes with a test of the failure it fixes. A performance change comes with a
before and after on the same machine, the workload named, and a check that the output is the
same ([performance](docs/performance.md)).

## Privacy

DayCube records a person's day. Sample data is fictional and labelled so in the app. Never add a
real recording, screenshot, name, path or token to the repository, a test or an issue.
