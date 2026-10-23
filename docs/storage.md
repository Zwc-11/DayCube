# Storage and caching

How much a day of capture costs on disk, where the bytes go, and how the vault keeps reads cheap
as days accumulate. Every number here comes from `eval/storage_budget.py` (one synthetic day at the
capture rates in the technical spec) or from the benchmarks named next to it. Media sizes cannot be
synthesized honestly, so they are stated assumptions, labelled as such.

## One day at spec rates

The day is 16 hours awake with 8 hours at a screen:

- fixes at 1 Hz
- the foreground window every 5 s
- a screenshot with OCR every 30 s
- gaze fixations at 3.5/s while reading
- 1,500 events

Sizes are in MB. "As written" is the format the vault uses today. GeoJSON is written with
indentation by `Vault.write_json`; the other streams are JSONL. The zstd columns compress compact
JSON; compressing the indented skeleton as written gives 1.03 MB, and the totals below use that
figure.

| Stream | Records | As written | Compact JSON | zstd 10 | zstd 19 | Parquet + zstd |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixes (`skeleton.geojson`) | 57,600 | 25.94 | 12.76 | 0.95 | 0.81 | 1.12 |
| Gaze fixations | 100,800 | 10.11 | 10.11 | 1.35 | 1.03 | 0.82 |
| OCR (`screens/ocr.jsonl`) | 963 shots | 5.71 | 5.71 | 0.57 | 0.50 | — |
| Events (`events.jsonl`) | 1,500 | 0.70 | 0.70 | 0.14 | 0.13 | — |
| Foreground windows | 5,846 | 0.67 | 0.67 | 0.01 | 0.01 | 0.01 |

The search index and embeddings:

| Derived data | Before | After collapsing repeated OCR |
| --- | ---: | ---: |
| Index units | 25,575 | 5,290 |
| FTS5 index | 6.74 MB | 1.56 MB |
| Embeddings, float32 (384-d) | 37.46 MB | 7.75 MB |
| Embeddings, int8 | 9.37 MB | 1.94 MB |
| Embeddings, binary | 1.17 MB | 0.24 MB |

Assumed media sizes:
- **Thumbnails:** 28 KB each, 26.3 MB/day. Skipping the 60% of shots whose screen did not change gives 10.5 MB/day.
- **One minute of a moment:** source video 60 MB at 1080p HEVC, keyframes 10.5 MB.
  - Depth for every keyframe: 52.7 MB.
  - Depth for the scene keyframe only: 0.44 MB, the only one the 2.5D mesh uses.

Excluding moments:

| | Per day | Per year |
| --- | ---: | ---: |
| As written | 113.7 MB | 41.5 GB |
| With this change: finished days compacted, repeated OCR collapsed | 38.7 MB | 14.1 GB |
| Plus int8 embeddings and unchanged-screen thumbnails skipped | 17.1 MB | 6.3 GB |

The last row's int8 embeddings now exist (the vault database, [search.md](search.md)); unchanged-
screen thumbnails still need screenshot capture. Moments dominate whenever they exist. Keep the source video and treat everything derived
from it as cache.

## What changed in the vault

| Mechanism | What it does | Measured effect |
| --- | --- | --- |
| Appending logs | `Vault.append_lines` writes only the new lines in one `write`, then fsyncs. A crash can leave at most one unacknowledged partial line. The next append cuts it, and readers skip it. `append_events` previously rewrote the whole log; the capture receiver did too, so a day of capture cost O(n²) | 10 events into a 5,000-event day, then the next read: 88.7 → 5.2 ms (median of 50) |
| Write-through snapshot | After its own append, the vault merges the new events into the cached series (a stable merge, O(n + k log k)). It never re-parses and re-validates the log. It does this only when no other file changed | Same row |
| Bounded caches | Parsed days are in a 64 MB segmented LRU, serialized days in a 32 MB one, charged by content size. A day touched twice is protected, so a pass over every day cannot evict it | Memory stays flat with the number of days opened |
| Conditional `/day` | The payload carries a blake2b entity tag. A matching `If-None-Match` gets an empty `304`, and the viewer keeps its parsed copy | 1.88 MB → 0 bytes on revalidation |
| Viewer path | Each visit's fixes are simplified with the synchronized Euclidean distance, at 10 cm, and reduced to the six fields the viewer reads | Map-matched day: 57,600 → 234 points, payload 12.0 → 1.9 MB. Positions with 0.3 m noise: 55,603 points, 10.7 → 6.6 MB |
| OCR runs | A block that stays on screen across consecutive shots of one window is indexed once. That entry keeps the shot where attention peaked and when the block was last seen | 25,575 → 5,290 units; large benchmark build 79 → 52 ms |
| Compaction | `daycube compact` compresses the large logs of days before today. Each frame is verified by decompressing it before the plain file is removed. Every reader decompresses transparently, and an append expands the log first | Synthetic logs 17 MB → 0.14 MB in 91 ms. Synthetic data is unusually repetitive; use the zstd 10 column above for real ratios |
| Day listing | `/days` reads each `day.json` once per change and stays out of the snapshot cache | Listing a year of days does not evict the open day |

Why the snapshot cache is a segmented LRU:
- **The access pattern:** a few hot days, strongly recency-biased, with occasional scans. Reindexing, exporting and the calendar listing are scans.
- **Plain LRU:** a scan flushes it.
- **Frequency sketches such as TinyLFU:** they need many accesses per key to learn, and a day is opened a handful of times.
- **Segmented LRU:** gets scan resistance in O(1) per operation and needs no tuning.

Every cache key covers every input file. Each file contributes `(mtime_ns, size, inode)` plus an
in-process write counter. The counter catches two writes inside one coarse clock tick.
`tests/test_cache_regressions.py` pins each way a warm cache previously disagreed with a cold read.

## Why the canonical files stay JSON for now

Parquet would shrink the fixes about as much as zstd does (1.12 vs 0.95 MB). Reading the columns
takes 4 ms against 300 ms to parse the GeoJSON. But converting those columns into the Python
records the pipeline uses takes 530 ms, slower than JSON. So a columnar format only pays off when
the consumer stays columnar. A training loader moving Arrow columns into tensors does; today's
dict-based pipeline does not.

Plain JSON also keeps the spec's contract: every component reads files a person can open. So:
- the canonical vault stays JSON;
- finished days are compressed;
- columnar data belongs in an export built for the consumer that wants it.

## Making the vault trainable

For a world model, the vault already has the hard parts:
- one clock: ISO 8601 with offsets, and seconds since local midnight;
- place-local metric frames;
- an evidence id on every derived record;
- a fidelity label on every record.

To make it a training source:

1. **Train only on `known`, and on `estimated` with its confidence as weight.** `illustrative`
   geometry is stage set, not observation; a model trained on it learns the diorama.
2. **Keep source data lossless, and treat derived data as a cache.**
   - Source: fixes, capture logs, moment video, transcripts' speaker confirmations, place names.
   - Rebuildable: keyframes, depth, splats, OCR, the index.
   - A retention policy can then drop caches without losing anything a model needs.
3. **Export, don't convert.** `daycube export` (next step) should write one Parquet file per
   stream per day, in a shared timebase, with fidelity and consent columns. A loader reads columns
   straight into tensors; that is the 4 ms path, not the 530 ms one.
4. **Consent travels with the data.** Faces and voices of other people are blurred or excluded
   unless they consented (spec 5.6). The export must drop non-consented moments by default rather
   than leave it to the trainer.

## Reproduce

```powershell
uv run --with zstandard --with pyarrow python eval/storage_budget.py --output test-results/storage.json
uv run pytest services/ingest/tests/test_storage.py services/ingest/tests/test_cache_regressions.py
uv run daycube compact --before 2026-10-20
```

The last report is in [eval/results/storage-budget.json](../eval/results/storage-budget.json).
