# Install size and memory of the desktop app

Continues the [2026-11-03 pass](efficiency-pass-2026-11-03.md) on `desktop-app`. The question was
the installed size (1,351 MB, now 868 MB) and what the running app costs. The install was measured from the CI
installer, extracted, per folder and package; memory on this Linux workspace (two cores), from the
same bundle on the sample vault. Absolute times on the Windows PC will be lower; each comparison
holds the output fixed.

## Measured changes

| What | Before | After | How it was held equal |
| --- | ---: | ---: | --- |
| Windows installer (CI) | 271.8 MB | 185.3 MB | Silent install, seeding and indexing with the installed Python, `--self-test`, on `windows-latest` |
| Windows install | 1,351 MB | 868 MB | as above |
| of which `runtime/core` + `runtime/gaze` | 798 + 609 MB | 391 + 458 MB | as above, and the bundle's checks per environment |
| Linux bundle, `runtime/core` + `runtime/gaze` | 1,636 + 890 MB | 1,099 + 641 MB | The bundle's checks; the self-test from the relocated bundle |
| Embedding 2,000 texts with the lengths of a vault in use | 226.4 s, peak 1,227 MB | 36.1 s, peak 367 MB | The same texts (`workload_sha256`); every vector equal to the text embedded alone ([before](../eval/results/embed-before-2026-11-04.json), [after](../eval/results/embed-after-2026-11-04.json)) |
| The vault API's embedding model, loaded, then unused for 10 minutes (Linux) | 284 MB, kept | 94 MB; the next search loads it in 0.3 s | The same model and vectors |
| The same with the installed Python on Windows CI | 198 MB, kept | 86 MB | as above |
| The receiver 80 s after start, past its first job poll | 222 MB, Open3D loaded | 82 MB | Readiness is the same answer: Open3D installed or not |

**What the installer carried that nothing runs.** Measured on the extracted Windows install before
the change; each environment in `apps/desktop/scripts/bundle-runtimes.py` now says what it leaves
out, and is followed by a check that imports and runs what the app uses (Open3D's distance query
included):

| Left out | MB (Windows) | Why it was there |
| --- | ---: | --- |
| Open3D's notebook and web viewers: Plotly, Dash, Flask, IPython, Jupyter widgets and what only they need | 125 without their bytecode | Required by the `open3d` wheel. `open3d.visualization` imports its Plotly module, which the bundle replaces with one that raises if called |
| Test suites | 86 | Shipped in wheels and the standard library |
| Sources beside their bytecode, standard library and third-party | 79 | Python reads the bytecode. Python's embeddable distribution ships its standard library the same way; tracebacks there show file and line without the text. `cv2`'s loader settings stay as source: `cv2/__init__.py` executes them |
| COLMAP (`pycolmap`) | 67 | The receiver's environment carries it for the repository's evaluations; walks from images run in the 3D pack, which has its own |
| Open3D's renderer assets, PyTorch ops, Open3D-ML, examples | 61 | Used by its GUI, by `open3d.ml`, or with PyTorch present; the mesh stage uses none |
| pip, setuptools, ensurepip, IDLE | 38 | Shipped with python-build-standalone; the packs are installed by uv |
| Type stubs, C sources and headers, link libraries | 10 | Shipped in wheels. The interpreter's own `include` and `libs`, which a pack may build against, stay |

**Embedding.** `FastEmbedder.passages` gave fastembed the day's texts in their own order, 64 at a
time. ONNX Runtime pads a batch to its longest text and attention grows with the square of that
length, so a batch with one 1,200-character unit and 63 short ones cost as much as 64 long ones,
and its CPU arena kept the peak for as long as the API ran. Texts now go shortest first, 16 at a
time, and come back in their own order. The workload's lengths follow the units of the vault in use
on the PC (18,106 units over 12 days: median 80 characters, 99th percentile 516, longest 1,200);
its text comes from standard-library docstrings. Reproduce with `eval/benchmark_embed.py`.

**Idle model.** bge-small with ONNX Runtime takes about 270 MB once loaded. The API loads it at
start (to answer the first question quickly) and kept it until it stopped, all day in the tray. It
is now let go 10 minutes after its last use, never during a run; the next search loads it again.
On Linux glibc keeps freed pages, so the release also calls `malloc_trim`; Windows' heap returns
them by itself (198 to 86 MB, recorded by the smoke test as `embed_rss_mb` in `sizes.txt`).

**Open3D in the receiver.** The CPU job thread asks every minute whether each stage can run; the
mesh stage answered by importing Open3D, which then stayed in the receiver. With its notebook
viewer that import took 966 modules, 192 MB and up to 13 s on a cold disk. `mesh.available()` now
looks for the package without importing it, and Open3D is imported when a scan is meshed. The mesh
stage itself is unchanged: from the trimmed bundle it meshes the synthetic scan of
`tests/test_splat.py` in 1.8 s, held-out LiDAR points a median 0.9 cm from the mesh.

## Looked at, left as they are

| What | Finding |
| --- | --- |
| Two interpreters, core and eye tracker | The eye tracker's EyeTrax pins numpy<2, which the rest cannot use. Sharing one interpreter's standard library would save about 60 MB, through a Windows-only path setup |
| Matplotlib in the eye tracker (about 70 MB with its dependencies, 0.37 s of its start) | MediaPipe's vision package imports it for drawing helpers the tracker never calls; replacing MediaPipe's modules is more fragile than Open3D's one |
| Tcl/Tk in the eye tracker's environment (13 MB) | Unused there; the receiver's environment needs it (`capture/doingnow.py`) and its paths differ per platform |
| `uv.exe` (41 MB) | Installs the GPU packs from their lockfiles |
| The tray's status poll (every 4 s) and the shell's process watch (every 250 ms) | A loopback request and a non-blocking wait; not measurable next to the Python processes |

## Found, not changed here

Meaning search on the PC's vault: 393 of 18,106 units have vectors. A day with more than 256 new
units is indexed without them and waits for `daycube index`, which the desktop app never runs. At
the new speed a day of 4,000 units embeds in about 70 s on this workspace's two cores, so a
background fill in the API is now affordable.

## Validation

Python tests and Ruff; the embedder's tests (`tests/test_embed.py`: order, batch size, the model
kept during a run, released after it, loaded again); the bundle's checks per environment; the mesh
stage on the synthetic scan with the trimmed bundle's Open3D; `--self-test` from the relocated
bundle on Linux; the installer's install, seeding, indexing and self-test on Windows CI.
