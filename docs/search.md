# Search: the vault database

Every searchable unit of every day lives in one SQLite file, `vault/daycube.sqlite`. The day
folders stay the source of truth; the database only holds what can be rebuilt from them, so
deleting it costs nothing but the time to rebuild.

## Why one SQLite file

| Option | Choice | Why |
| --- | --- | --- |
| One SQLite file for the vault | Used | One user, local-first, nothing to run, one file to back up. A year is about 1.9M units and roughly 2 GB: 0.8 GB of vectors (measured), 0.6 GB of keyword index ([storage.md](storage.md)), the rest rows and indexes (estimated) |
| One index per day (the previous design) | Replaced | "When did I last see Alex" spans days; per-day files can only answer within one |
| Postgres + pgvector | Not now | Worth it for many users or a hosted service; the schema maps onto it |
| sqlite-vec (`vec0`) | Measured, then dropped | 1.5 s for a year-wide nearest-neighbour query (below) |
| A separate vector database | No | Another process to run and keep consistent, for a scan NumPy does in 68 ms |
| Parquet | Export only | Columnar data for training; search wants rows |

## Schema

| Table | Holds |
| --- | --- |
| `days` | One row per indexed day: time zone, the source stamp it was built from, unit count, embedding model, units still without a vector, and `generation`, a vault-wide write counter that tells in-memory search blocks to reload |
| `units` | Events, transcript segments, on-screen text runs and visits, with `ts` (epoch seconds, since ISO strings with offsets do not sort across DST) and `text_hash` (blake2b-128) |
| `units_fts` | FTS5 over `units.text` (external content) |
| `vectors` | Per embedded unit: the int8 embedding (384 bytes) and its sign bits (48 bytes) |
| `meta` | Embedding model and dimension, the write counter, and the version of what is derived. A model change replaces every vector; a change in what is derived (`DERIVED_VERSION`: units, graph, note links) reads every day and note again on the next use, keeping vectors, which are found again by text |
| `entities` | The knowledge graph's nodes: kind (site, page, query, course, deadline, place, person, topic, note, day), a canonical key, a display name. Shared by every day |
| `mentions` | Unit to entity, with a role: `on` (happened on that page or site), `about` (names it), `at` (a place), `with` (a person) |
| `links` | Entity to entity, per day and with the record that shows it: `part_of`, `led_to`, `mentions`, `due`; and `links` from the person's notes (day `''`, the note's path as evidence) |
| `notes`, `note_chunks`, `note_chunks_fts` | The notes folder as last read: each note's identity and stamp, and the person's own writing in pieces with a keyword index ([notes.md](notes.md)) |
| `speakers`, `deletions` | Carried over from the per-day index for speaker naming and purges |

Unit ids use `AUTOINCREMENT`, so they are never reused and a vector can never point at another
unit.

## Keeping it current

- **Per-day replace.** A day's rows are replaced in one transaction whenever its source stamp
  (mtime, size and inode of `day.json`, `events.jsonl`, `screens/ocr.jsonl`, moments and
  `places.json`) differs from the one stored. A reader never sees half a day, and a failed
  rebuild leaves the previous rows searchable.
- **Set statements, not triggers.** The keyword index and vectors are updated by one statement
  per day rather than a trigger per row; per-row triggers made a 5,053-unit day take 507 ms.
- **Cheap freshness.** A query re-checks a day only when its file stamps changed since the
  last check; otherwise it costs a few stats per day. Search as you type sends a request per
  pause in typing, so across every day it re-checks older days and the notes folder at most every
  2 s (`FULL_CHECK_S`) and today and yesterday, where capture is arriving, every time. A note hit
  whose file was deleted since is dropped, and a changed one brings the folder in at once.
- **Reuse before embedding.** Embedding is the slow step: bge-small manages 28–47 texts a second
  on two CPU cores. Re-indexing a day reuses the vectors of its unchanged units, and repeated
  text on other days ("Visited Home", the same document) reuses theirs. Only new text is
  embedded, outside the vault lock.
- **Inline limit.** A query embeds at most 256 new texts before answering. A bigger day is
  searched by keywords until `daycube index --date …` (or `--all`) fills its vectors.
- **Removed days.** A day deleted from the vault leaves the database at the next search across
  days.

| Synthetic day (`eval/benchmark_core.py`, 5 runs) | Per-day index file (before) | Vault database |
| --- | ---: | ---: |
| Large day (5,053 units), warm | 52 ms | 105 ms |
| Large day, cold process | 239 ms | 289 ms |
| Keyword search, warm | — | 1.3 ms |

The database rewrites a day in place, in a shared file with an embedding column, where the old
index wrote a fresh file per day. The extra 50 ms is mostly the insert into a table with a
uniqueness index (46 ms for 5,053 rows).

## What the computer capture adds

Days are searched whether they have a vault day folder, computer capture
(`vault/capture/<date>/`, [capture.md](capture.md)), or both. `ingest/capture/synthesize.py` reads
a day's capture logs once and turns them into units:

| Unit type | From | Text |
| --- | --- | --- |
| `web.visit` | navigation, focus and idle records: the page in front, idle and locked time left out, the same page back within a minute merged, spans under 3 s dropped | "Opened *title* (*site*) for 5 min" |
| `web.read` | the line decoder's reads (`reading.jsonl`) | "Read in *title* (*site*): *the lines read*"; attention = line coverage, confidence = the decoder's |
| `web.dwell` | dwells (`attention.jsonl`), one per dwell | "Stayed 16 s on this in *title*: *the block*" |
| `web.look` | fixations placed on a block, 3 s or more in all, for blocks not read line by line (headings, figures, tables) | "Looked at in *title*: *the block*" |
| `web.search`, `web.click`, `web.selection`, `web.media` | the browser records of those kinds | "Searched google: *query*", "Clicked “*link*” to *site*", "Copied in *title*: “*text*”", "Played media for 5 min in *title*" |

Block texts are sent once per page load, so the previous day's text snapshots are read too. A
line that does not parse is skipped. The day's source stamp covers its capture logs (compacted or
not), so new capture lines re-index the day at the next search. A capture-only day takes the
receiver's time zone (`DAYCUBE_TIMEZONE`, default America/Toronto) and has no places.

## Knowledge graph

`ingest/graph/extract.py` names entities only where a record names them exactly, so the graph
needs no language model and no guessing:

| Kind | Key | From |
| --- | --- | --- |
| `site` | domain without `www.` | every capture record with a URL |
| `page` | URL without fragment, tracking parameters or trailing slash | visits, reads, dwells, looks, clicks, copies, media |
| `query` | the search, lower case | searches |
| `course` | a course code (`CS 136`, `MATH 135`: 2-5 capitals, 3 digits) | page titles, text read or stared at |
| `deadline` | date (and time) plus course, named "CS 136 due Fri Oct 30, 11:59 PM" | "due October 30 at 11:59 PM" in text read or stared at; without a year, the next such date |
| `place` | the vault's place id, named as in the day or as renamed in `places.json` (a rename shows at once) | visits |
| `person`, `topic` | the event's own names | events' `people` and `topics` |

Links: a page is `part_of` its site; a search `led_to` the page opened from its results (the next
page in that tab that is not a results page, within 5 minutes); a page `led_to` the page a link on
it opened; a page `mentions` a course; a course (or the page, without one) is `due` at a deadline;
a place `led_to` the next place visited that day.
Mentions and links are written with the day's units, in the same transaction, and entities that
nothing points at any more are dropped. A place's records are found through `units.place` rather
than a mention per event.

`GET /graph?since=&until=&kinds=&limit=` returns the most mentioned entities in a window and the
links among them; `GET /entity/{id}` one entity with its records (newest first) and neighbours.
The viewer draws it under Settings → Knowledge graph, and from the calendar under Your data, with
a deterministic force layout (`apps/viewer/src/lib/graphLayout.ts`: the same graph always gives
the same picture; 132 nodes lay out in about 40 ms). Click a node for its records and neighbours.
On the synthetic large day (5,053 units, every event a visit and so a place mention) writing the
graph adds 45 ms to the day's update (107 to 152 ms); a real day names far fewer entities.

## Question filters

A question's time, kind and site words narrow the search and are taken out of it
(`ingest/ask/query.py`, patterns only, no language model):

| Words | Filter |
| --- | --- |
| today, tonight, yesterday, last night, *N* days ago, on Monday, last Friday, October 22, Oct 22nd, 2026-10-22 | that day (a date ahead of today means last year) |
| morning, afternoon, evening, night after a day; this morning | 5-12, 12-17, 17-24, 18-24 h of it |
| this week, last week, this month, last month, since Tuesday, past 3 days, past 2 hours | the range up to now |
| read / searched, googled, looked up / stared at / clicked / copied / watched, videos / visited, opened / said, talked about | reads, dwells, looks, on-screen text and pages read; searches; dwells; clicks; copies; media; visits; conversations |
| on YouTube, in D2L, on Wikipedia, on GitHub, on example.com | records on that site (or its subdomains), through the site's mentions |

"What did I read about hash tables yesterday on Wikipedia" searches for "hash tables" in reads,
dwells and looks between yesterday's midnights, on wikipedia.org. A question that is all filters
("what did I search yesterday") lists the records instead (up to 20, in time order). When the
words match nothing within the filters, the filtered records are listed and the answer says so
("Nothing matched “quantum”; showing yesterday, reading instead."). Ask reports what it read the
question as in `filters` (labels, window, kinds, site, the words searched). Meaning search embeds
the question without its filter words and drops hits outside the kinds and site afterwards. Notes
are searched only by questions without filters, since they have no time, kind or site.

## Retrieval

1. **Keywords.** FTS5 with every question word quoted, so punctuation can never become an
   operator; best BM25 first; the top 50.
2. **Meaning.** The question is embedded with bge's query instruction, then searched in two
   passes (`ingest/store/vectors.py`):
   - Hamming distance on sign bits over every unit in scope. The bits are held in memory,
     column-major, one block per day sorted by time, so a day or a time window is a contiguous
     slice. This costs 64 bytes a unit, 125 MB for a year.
   - The best 40× the wanted count are re-scored by int8 cosine, fetched by primary key.

   Only results with similarity of at least 0.55 count.
3. **Fusion.** Reciprocal-rank fusion (k = 60). Each keyword hit is weighted by the share of
   the question's words it contains, so a record that shares one common word ("day") cannot tie
   a close meaning match.

Every evidence record says which method found it (`matched`) and which `day` it is from.

### Two quantizations

- **int8 for storage and exact scoring.** bge-small's components are small (median magnitude
  0.027, 99.9th percentile 0.37), so they are scaled by 254 before rounding. On 1,218 sentences
  from this repository's documents, int8 top-10 neighbours agree with float32 97.6% of the time
  (96.6% at a scale of 127; 94% at 381, where clipping starts). int8 is a quarter of float32's
  size.
- **Sign bits for the first pass.** On 6,000 real sentences (the docs plus standard-library
  docstrings), re-scoring the bit pass's best candidates kept this share of the exact top-k:

  | Candidates re-scored | recall@10 | recall@50 |
  | --- | ---: | ---: |
  | 2× | 0.79 | 0.73 |
  | 4× | 0.88 | 0.87 |
  | 8× | 0.93 | 0.94 |
  | 16× | 0.96 | 0.97 |

  The index re-scores 40×, since a year holds 300 times more units than this test.

### Scale

`uv run python eval/vector_bench.py --sizes 100000 500000 1930000`: random vectors at 5,290 units
a day, 50 neighbours, median of 20 queries, two CPU cores.

| Units | Days | All days | One day | Last 7 days | Last 30 days | First-search load |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100,000 | 19 | 9 ms | 3.8 ms | 6.1 ms | 8.3 ms | 0.2 s |
| 500,000 | 95 | 22 ms | 3.3 ms | 6.3 ms | 10 ms | 0.8 s |
| 1,930,000 (a year) | 365 | 68 ms | 4.7 ms | 8.1 ms | 12 ms | 3.4 s |

The same year in sqlite-vec 0.1.9, as one int8 `vec0` table partitioned by day:

| Query | sqlite-vec | Two-pass index |
| --- | ---: | ---: |
| All days | 1,539 ms | 68 ms |
| One day | 3.7 ms | 4.7 ms |
| Last 7 days | 242 ms | 8.1 ms |
| Last 30 days | 329 ms | 12 ms |

Bit vectors in sqlite-vec were no faster (1.8 s), because its per-row overhead, not the distance
arithmetic, dominates. The spec budgets 30 ms for retrieval: any scoped question meets it, and a
year-wide meaning search takes about twice that.

### What the sample day shows

`uv run --extra vector python eval/score_retrieval.py`: 28 units, synthetic, 25 questions. The
threshold and query instruction were chosen on these same questions, so this is a sanity check,
not a result.

| Questions | Keyword | Meaning | Combined |
| --- | ---: | ---: | ---: |
| 5 with exact words, top-1 | 1.00 | 1.00 | 1.00 |
| 4 located by place and time, top-1 | 1.00 | 1.00 | 1.00 |
| 11 paraphrases, top-1 | 0.09 | 0.55 | 0.45 |
| 11 paraphrases, top-3 | 0.09 | 0.73 | 0.73 |
| 5 with no answer in the day, nothing returned | 1.00 | 0.80 | 0.80 |

- **Meaning search is what finds paraphrases.** Keywords alone find 1 of 11.
- **Combined trails meaning-only by one paraphrase.** "In what sequence *will* we run the
  presentation?" shares *will* with a neighbouring quote, which then ranks in both lists. On
  this day nothing favours keywords, since every exact-word question is easy for both methods.
  Keywords matter for rare names, numbers and IDs, which a small embedding model handles poorly.
  Real questions decide whether fusion earns its place.
- **No threshold separates "no answer".** Correct answers scored 0.48–0.80. The best match for
  unanswerable questions scored up to 0.60 ("Did I go swimming?" returns two records). Saying
  "nothing in your days answers this" is the answer checker's job.

## Search as you type

`GET /search?q=&date=` returns what `POST /ask` returns, without the vault token (reads only, like
the other GET routes), with each result's note and the words to mark there, and `took_ms`. The
viewer's notes page calls it 90 ms after the last keystroke and cancels the request it replaces.
The API loads the vault database, the embedding model and the vector index in the background when
it starts, so the first question is as fast as the rest.

`eval/search_latency.py` builds days of synthetic computer capture (80 pages a day, real English
lines from these docs) and times 10 questions, 3 rounds, warm, through the API (two CPU cores):

| Vault | Search | One day, p50 / p95 | Every day, p50 / p95 |
| --- | --- | ---: | ---: |
| 90 days, 25,560 records | keywords | 5.5 / 10.1 ms | 7.5 / 13.9 ms |
| 20 days, 5,680 records | keywords and meaning (bge-small) | 13.9 / 18.7 ms | 29.0 / 52.2 ms |

Server time; the request adds 2-4 ms. Embedding the question is most of the meaning-search cost.
Getting there: the result-to-note lookup walked every page entity until a `CROSS JOIN` fixed its
order (4.3 ms to 0.04 ms at 30 days), and searches across days stopped re-checking 90 days of files
per keystroke (39 to 7.5 ms). Embedding the 5,680 records the first time took 326 s; that is
`daycube index`'s job, not a search's.

Opening a day's notes at 60 days (4,862 notes): 36 ms once written, 415 ms when its notes are
written first; one note 25 ms. The notes folder is listed with `os.scandir` (on Windows the
listing carries each file's times and size).

## Commands

```powershell
uv sync --extra vector                    # meaning search; without it, keywords only
uv run daycube index --date 2026-10-20    # or --all: rebuild rows and fill every vector
uv run daycube stats                      # days, units, units without vectors, size
uv run daycube data                       # the data center: every source stream and the database
uv run daycube graph                      # the most mentioned entities and their links, as JSON
uv run --extra vector python eval/score_retrieval.py
uv run python eval/vector_bench.py --sizes 100000 500000 1930000
uv run python eval/search_latency.py --days 90 --output eval/results/search-latency.json
```

`DAYCUBE_VECTOR_SEARCH=false` turns meaning search off; `DAYCUBE_EMBEDDING_MODEL` picks another
fastembed model, and switching replaces every stored vector.
