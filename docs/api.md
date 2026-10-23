# Local API

Run `npm run api`, then open <http://127.0.0.1:7701/docs>. FastAPI publishes the live OpenAPI
contract at <http://127.0.0.1:7701/openapi.json>. Use its Authorize button with `vault/.token`
for actions. Do not paste external provider keys there.

| Method | Route | Current behavior |
| --- | --- | --- |
| GET | `/health` | Health and honest capability flags |
| GET | `/days` | Day summaries, newest first: `date`, `stats`, place `kinds` in visit order |
| GET | `/day/{date}` | Validated-file data with derived `layers[]`; `ETag`, `304` on `If-None-Match` |
| GET | `/day/{date}/events?t0=...&t1=...&type=...` | Event slice; both offset timestamps or neither |
| GET | `/day/{date}/skeleton` | GeoJSON fixes |
| GET | `/day/{date}/graph` | Evidence graph |
| GET | `/moment/{id}?date=YYYY-MM-DD` | Metadata, scene availability and transcript |
| GET | `/vault/{date}/{asset}` | Allowlisted media only, with path confinement |
| POST | `/ask` | `{date?, question}` → extractive answer, citations, location; no `date` searches every day |
| POST | `/ask?stream=1` | SSE `location`, `answer`, `done` events |
| POST | `/events` | `{date, events:[canonical Event]}` → accepted count |
| POST | `/ingest/{date}` | `{steps:["index"]}` → process-local job id |
| GET | `/jobs/{id}` | Job snapshot |
| GET | `/jobs/{id}?stream=1` | SSE `progress` events |
| POST | `/places/rename` | `{place_id,name}` → global name override |
| POST | `/speakers/confirm` | `{date,moment_id,diar_label,person_id}` → persisted speaker map |
| POST | `/pause`, `/resume` | Forward to receiver; 503 if absent |
| DELETE | `/day/{date}/hours` | 501 until complete purge/reindex semantics exist |

All POST/DELETE routes require `Authorization: Bearer <vault-token>`. Event ingestion is separate
from port 7700's raw browser metadata receiver. The two payloads are intentionally different.
Moment lookup requires a date so two days can safely both contain `mo_001`.

`/day/{date}` is serialized once per change of its input files. It carries an `ETag` and
`Cache-Control: no-cache`, so a client sending `If-None-Match` with the current tag gets an empty
`304`. In `layers[].path`, each visit's fixes are reduced to the fields the viewer reads and
simplified so the interpolated position at every fix time is within 10 cm of the fix.
`/day/{date}/skeleton` still returns every fix with every field.

`/ask` searches the vault database (`vault/daycube.sqlite`, see [search.md](search.md)) with
keywords and, when the vector extra is installed, meaning, merged by rank. It answers with the
best record verbatim; query rewriting, generated answers and verification are still to come.
Each evidence record carries its `day` and `matched` (`keyword`, `meaning` or both), and the
answer carries the `day` of its best record. `/health` reports `vector_search`. An unknown
question returns empty `sentences` and `evidence`, null location fields and an explanatory
message. Unknown x/y/floor values remain null.
`t` in ask results and `tod` on event pins are seconds since local midnight; `iso_time` is the
unambiguous source instant. API metadata uses snake_case; viewer layer shapes use camelCase.

Speaker confirmation currently updates the transcript map only. It does not regenerate quote
events or the graph. Unsupported ingest stages return 501 instead of reporting success.

`packages/api-client` provides `DaycubeClient` to the frontend bridge. Its base URL defaults
to `/api`, which Vite proxies to loopback port 7701. It validates day/event/moment/search payloads
using shared contracts. A token callback keeps token acquisition in the caller's UI.

The frontend's Settings › Vault connection page binds the token in memory, reloads the day and runs index jobs.
`apps/viewer/src/lib/backendBridge.ts` converts seconds to renderer hours and polls job completion.
Live empty search results stay empty; they do not substitute prototype evidence.
