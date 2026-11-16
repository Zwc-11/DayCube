# Performance

Numbers measured on named workloads, and how to measure them again. Each table says where it was
measured; compare rows within a table only. None of them is a promise for other hardware.

## Rules the code keeps

- The viewer draws nothing when idle, links no shader and allocates no buffer during playback,
  and stays within 10 WebGL draw calls for the sample scene. The browser suite asserts all three
  (`npm run test:e2e:viewer`).
- A day's files are parsed and validated once per change (`Vault.snapshot`); `/day/{date}` replays
  the same bytes and answers `304` to a client that holds them.
- Time windows, layers and path samples are found by bisection, never by scanning a day.
- Every cache has a byte or entry budget. Logs are appended, never rewritten.

## Vault and search (CPU)

Synthetic day: 50 visits, 5,000 events, 10,000 path fixes, 1,000 screenshots with three OCR blocks
each (8,050 indexed units). Linux x86-64 container, one warm-up then five runs, medians.

| Operation | Time |
| --- | ---: |
| Full index rebuild, new `Vault` (parse, validate, collect, write) | 277 ms |
| Full index rebuild, warm snapshot | 119 ms |
| Assemble the viewer's day | 12 ms |
| Collect index evidence | 8.0 ms |
| Events overlapping a 15-minute window | 0.16 ms |
| `/day` payload, first request after a change | 164 ms |
| `/day` payload, unchanged day | 0.46 ms |
| Warm retrieval | 1.2 ms |

The table is the `large` case of [core-storage.json](../eval/results/core-storage.json), recorded
on 2026-11-15 on two cores.

Through FastAPI's test client (about 2.5 ms of each figure is the client itself):
`GET /day/{date}` 2.7 ms, `GET /day/{date}/events` over 30 minutes 3.7 ms, `POST /ask` 4.0 ms.

```powershell
npm run bench:core -- --output test-results/core-current.json --compare eval/results/core-storage.json
```

The benchmark builds its own temporary vault, never reads yours, and fails when an output hash
differs from the compared report. Search over many days, vectors and storage per day have their
own measurements in [search](search.md) and [storage](storage.md).

## A day's capture into the index (real days)

Ten recorded days of one person (449 to 4,239 indexed units a day; `blocks.jsonl`, the text and
geometry of every page shown, is 9 to 63 MB of each), on a two-core Linux VM, one process, each
day indexed twice. Recorded on 2026-11-16; the index rows (units, entities, mentions) of all ten
days are the same before and after.

| | Before | After |
| --- | ---: | ---: |
| Index the ten days, first time | 4.88 s | 3.12 s |
| Index them again in the same process | 3.17 s | 1.10 s |
| The heaviest day (4,239 units, 63 MB of blocks): first, again | 1.36 s, 1.33 s | 0.75 s, 0.30 s |
| The day after it (its text log is read too, for pages left open overnight) | 0.89 s, 0.72 s | 0.38 s, 0.05 s |
| Block texts kept for the heaviest day | 108,579 (34 MB: over the cache's 32 MB, so never kept) | 845 (0.9 MB) |

What changed: a day uses a block's text only when a look or a dwell names the block. The log is
now read for those blocks alone, and a record of another document is passed over without being
parsed (`capture/synthesize.py`). Before, every block's text was kept, a heavy day's did not fit
its cache and was parsed again on every index, and half of the rest of the time went to measuring
the size of what was cached.

A whole background round for a day (index, notes, timeline, review, episodes) costs 0.5 to 2 s
of one core every ten minutes on those days; the review is the largest part (0.4 to 1.7 s, a
third of it two `git` calls for the day's commits).

## A video's frames

A 90.6 s phone video (1080×1920, 30 frames a second, a keyframe every 2.8 s), two CPU cores.

| | Before | After |
| --- | ---: | ---: |
| The 92 looks of its 34 shots, decoded | 64 s (0.70 s a look: a seek each), twice with poses | 29 s, once |
| Items of the whole video, with poses ([items](items.md#a-plain-video)) | 1,172 s | 883 s |
| A scan's frames, one every 2 s of 128 s (HEVC, 1920×1440), same pixels | 62.1 s | 55.6 s |

A shot's looks now come from one run of the decoder (`items/frames.py` `looks`), the pose model
stays loaded for the whole video and takes the same pictures the detector does, and a scan's
decoder scales and hands over only the picked frames. Finding the cuts still decodes the whole
video once (27 s here): that is the decoder's own speed.

## Viewer

Sample day, 1600×1000, headless Chromium with its software renderer (SwiftShader), medians of
three runs. The software renderer caps the frame rate, so the gain shows as main-thread headroom;
a real GPU needs its own trace.

| Four seconds of 1× playback | Value |
| --- | ---: |
| Main-thread script time | 171 ms |
| Tasks longer than 50 ms | 0 |
| Shader programs linked | 0 |
| Buffer allocations | 0 |
| WebGL draw calls per frame, every pass | 10.1 |

Four seconds idle after settling: 0 draw calls, 1 ms of main-thread task time.

```powershell
node scripts/profile-viewer.mjs current            # idle, with the dev server running
node scripts/profile-viewer.mjs current playback
```

A scan's 3D view on your own GPU: `check-3d.cmd` ([splat](splat.md)).

## Windows app: size and memory

Measured on 2026-11-04 after trimming what the installer carried and nothing ran. The install was
measured from the CI installer on `windows-latest` (silent install, seeding and indexing with the
installed Python, `--self-test`); memory on a two-core Linux workspace from the same bundle on
the sample vault. Each comparison holds the output fixed.

| What | Before | After |
| --- | ---: | ---: |
| Windows installer | 271.8 MB | 185.3 MB |
| Windows install | 1,351 MB | 868 MB |
| of which `runtime/core` + `runtime/gaze` | 798 + 609 MB | 391 + 458 MB |
| Embedding 2,000 texts with the lengths of a vault in use | 226.4 s, peak 1,227 MB | 36.1 s, peak 367 MB |
| The API's embedding model, loaded, then unused for 10 minutes (Windows CI) | 198 MB, kept | 86 MB |
| The receiver 80 s after start | 222 MB, Open3D loaded | 82 MB |

The embedding rows are [before](../eval/results/embed-before-2026-11-04.json) and
[after](../eval/results/embed-after-2026-11-04.json): the same texts, every vector equal to the
text embedded alone. Every CI build of the installer writes its size and installed size to
`sizes.txt` (`apps/desktop/scripts/smoke-test.ps1`).

```powershell
uv run --project services/capture-win --extra desktop python eval/benchmark_embed.py --output test-results/embed.json --compare eval/results/embed-after-2026-11-04.json
```

## Eye tracker

Windows PC (AMD Ryzen 7 9800X3D, Logitech C920 at 640×480, MSMF, EyeTrax 0.4.0): 29.3 frames a
second processed at a 30 frames-a-second target, a usable face in every recent frame. Per frame:
28.3 ms waiting for the camera, 4.7 ms finding landmarks, 0.3 ms predicting. The report is
[gaze-windows-30fps.json](../eval/results/gaze-windows-30fps.json).

```powershell
uv run python eval/profile_gaze.py --seconds 15 --output test-results/gaze-rate.json
```

Accuracy, not speed, is the eye tracker's open problem: see [capture](capture.md#when-your-head-moves)
and the training comparison in [gaze training](gaze-training.md): 4.1° to 2.4° on its benchmark.

## Measuring a change

Name the workload and its size, the machine, the warm-up and the number of runs. Take before and
after on the same machine, and compare outputs as well as times. Timing asserts do not belong in
unit tests; counters do (draws, allocations, model calls, rows read).
