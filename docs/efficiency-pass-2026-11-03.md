# Startup, input counting and the desktop app

Continues the [2026-10-27 pass](efficiency-pass-2026-10-27.md) from `capture-browser-gaze`
(`e535c29`). The review ledger (`docs/review-ledger.json`, baseline `2ffd223`) was used to find what
no pass had covered: about 22,000 lines new or changed since, mostly the end-of-day review
(`ingest/review`), input counting, the screen reader's changes and the new desktop app. The
DeepInfer principle applied here is the one earlier passes cite from its CUDA graph runner:
prepare once, outside the repeated path, then replay.

Measurements ran on this Linux workspace (Python 3.11, Node 22, Chromium headless), not on the
Windows PC; absolute times on a Ryzen 7 9800X3D will be lower. Each comparison holds the output
fixed: the same records, the same verdicts, the same error text.

## Measured changes

| Path | Before | After | How it was held equal |
| --- | ---: | ---: | --- |
| Viewer: navigation to rendered sidebar, CPU throttled 4x, cold cache, median of 15 interleaved runs | 1,190 ms (1,101-1,358) | 649 ms (597-755) | Validators precompiled by the same ajv with the same options; a test compares verdict and error text with run-time compilation for all 11 contracts and invalid samples |
| Contract validators ready (Node, import to first use) | about 200 ms (37 ms loading ajv, 145-170 ms compiling) | 11 ms | as above |
| Input counter, per raw input event (200,000 synthetic events, median of 11) | 3.11 us | 1.92 us | Identical bucket records (SHA-256 of all records) |
| Desktop app, first start of the API after install | 7,538 ms | 843 ms | Bytecode written at build time; warm starts unchanged (about 0.5 s) |

**Viewer startup.** `@daycube/schemas` compiled all eleven JSON Schemas with ajv when the page loaded:
about 200 ms of loading and code generation before the first paint, on every page. The generator
(`scripts/generate-contracts.mjs`) now emits `validators.mjs` with ajv's standalone mode, and
`npm run schemas:check` fails when it drifts from the schemas, as for the other generated files.
The two helpers ajv reaches through CommonJS `require` become a named import and a 15-line copy
(`ucs2length.mjs`, tested against ajv's). The entry chunk is 356 → 423 KB raw but 106 → 80 KB
gzipped, and the browser no longer generates code at run time. Reproduce with
`eval/benchmark_first_render.mjs`.

**Input counter.** Every key and mouse report reaches `capture/inputs.py` (a gaming mouse reports
1,000-8,000 times a second). The window procedure copied the whole 1 KB buffer, sliced it, built
tuples between layers and ran a generator over button flags even for a bare move. `dispatch` now
reads the RAWINPUT in place with precompiled `struct.Struct`s and calls `Buckets.key`/`Buckets.mouse`
directly; a bare move is one `math.hypot`. `parse` and `Buckets.event` remain for tests. Reports:
[before](../eval/results/inputs-before-2026-11-03.json), [after](../eval/results/inputs-after-2026-11-03.json);
`eval/benchmark_inputs.py --compare` fails if the records differ. This measures the Python work
only, not the ctypes callback.

**Desktop app first start.** `apps/desktop/scripts/bundle-runtimes.py` compiles every module at build
time: third-party packages as `unchecked-hash` (an install never changes them), DayCube's own
sources as `checked-hash` (an edited file is still recompiled). Neither depends on file times.

## Looked at, left as they are

| Path | Finding |
| --- | --- |
| End-of-day timeline (`ingest/review`) | Rebuilt every 10 minutes: 49-100 ms per simulated day (`eval/day_sim.py`). Not worth changing |
| Screen reader paragraph matching | Trigram matching is limited to the last three reads and the known set is capped; OCR (100+ ms a read) dominates |
| Receiver start | About 550 ms warm, mostly FastAPI and httpx imports |
| Viewer entry chunk, demo data | The 41 KB sample day and the offline ask engine load on every page; making them lazy changes the bridge's synchronous API for about 10 ms of parsing |

## Validation

Python tests, Ruff, schema drift checks, contract tests, TypeScript, the viewer's 47 browser tests
(46 pass; `review.spec.ts` fails on `e535c29` without these changes as well), the desktop app's Rust
tests on Linux and Windows CI, and `DayCube.exe --self-test` against a relocated bundle.
