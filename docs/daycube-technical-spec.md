# DAYCUBE — Technical Specification (implementation level)
Version 1.0 · 2026-10-20 · Companion to `daycube-spec-and-plan.md`

This document says how to make the whole system actually work: contracts, schemas, algorithms, commands, prompts, performance budgets, tests and the runbook. Where a library's exact call might have changed, the text says "per the repo README" and gives the known-good alternative.

---

## 0. Conventions and the one rule that makes the project integrate

- **The vault is the contract.** Every component reads and writes plain files in `vault/<date>/`. The viewer never talks to the pipeline directly; it reads the vault through a thin API. This is what lets four people work in parallel from hour zero.
- **Fixture first.** `fixtures/sample-day/` is a complete, hand-written vault for one day that validates against the schemas. The viewer, the API and the evaluation harness are built against it before any real data exists.
- **Schemas are shared.** `packages/schemas/` holds JSON Schema files; Python uses pydantic models generated from them, TypeScript uses zod. CI validates every fixture and every produced vault against them.
- **Three fidelity levels, everywhere:** `known` (captured or measured), `estimated` (fused, with confidence), `illustrative` (stage geometry, not user data). The viewer renders them differently; the card labels them.
- **Time** is ISO 8601 with offset in files; seconds since local midnight (`tod`) in the viewer's state.
- **Units** are meters in a place-local frame (x east, y north, z up); layer frames are centered on the place.

Toolchain: Python 3.11 (`uv` or `venv`), Node 20 with `pnpm`, SQLite 3.45+, ffmpeg 6, Modal CLI, Chrome, Windows 11 for the capture service, a webcam.

---

## 1. Repository layout

```
daycube/
  Makefile
  packages/schemas/            # day.schema.json, event.schema.json, venue.schema.json, moment.schema.json, graph.schema.json
  fixtures/sample-day/         # a full example vault for 2026-10-20
  services/ingest/             # FastAPI app + pipelines (Python)
    ingest/api.py              # endpoints
    ingest/vault.py            # read/write helpers, schema validation
    ingest/skeleton/           # parsers, visits, floors, places, layers
    ingest/mind/               # event synthesis from capture logs, attention
    ingest/moments/            # keyframes, transcripts, depth, scenes, splats
    ingest/index/              # units, fts, vec, embeddings
    ingest/ask/                # rewrite, retrieve, phrase, verify
    tests/
  services/capture-win/        # Windows capture service (Python): window sampler, screenshots, OCR, gaze, tray
  extensions/chrome/           # MV3 extension
  jobs/modal/                  # GPU jobs: whisperx, depth, splat
  apps/viewer/                 # Next.js 15 + React Three Fiber
  venues/                      # venue packs
  eval/                        # question sets, experiment protocol, scoring scripts, results
  docs/                        # architecture.md, consent.md, runbook.md, README (judging script)
```

`Makefile` targets: `make setup`, `make capture`, `make ingest DATE=2026-10-20`, `make index DATE=...`, `make viewer`, `make demo` (rebuild index from fixture and start viewer), `make eval`, `make test`.

---

## 2. Data model and schemas

### 2.1 `day.json`

```json
{
  "schema": "daycube.day/1",
  "date": "2026-10-20",
  "tz": "America/Los_Angeles",
  "places": [
    {"id": "home1", "name": "Home", "kind": "house", "t0": "2026-10-20T08:00:00-07:00", "t1": "2026-10-20T08:45:00-07:00",
     "lat": 34.0689, "lon": -118.4452, "osm_id": null, "venue": null, "floors_visited": [0], "conf": 0.86, "layer_index": 0},
    {"id": "hall", "name": "Engineering Hall", "kind": "venue", "t0": "2026-10-20T09:20:00-07:00", "t1": "2026-10-20T11:45:00-07:00",
     "lat": 34.0694, "lon": -118.4430, "osm_id": "way/123456", "venue": "venues/engineering-hall.json", "floors_visited": [0, 2], "conf": 0.91, "layer_index": 2}
  ],
  "transits": [{"from": "home1", "to": "cafe1", "t0": "...", "t1": "...", "mode": "walk", "conf": 0.7}],
  "cube_faces": ["mo_001", "ev_000123", "mo_002", "ev_000130", "ev_000140", "mo_003"],
  "stats": {"events": 11, "moments": 3, "places": 5, "floor_changes": 1}
}
```

### 2.2 `skeleton.geojson`

A `FeatureCollection` of `Point` features, one per fix (1 Hz when the recorder runs, sparser from exports):
```json
{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-118.4430, 34.0694]},
 "properties": {"t": "2026-10-20T09:52:10-07:00", "alt_rel": 6.3, "floor": 2, "floor_conf": 0.91,
                "place": "hall", "x": 12.0, "y": 6.0, "pos_conf": 0.4, "fidelity": "estimated", "source": "arc_gpx"}}
```
`x, y` are place-local meters (null when unknown). `pos_conf` is the indoor position confidence (0.4 = corridor-level guess).

### 2.3 `events.jsonl`

One JSON object per line, append-only, sorted by `t` at index time.
```json
{"id": "ev_000123", "t": "2026-10-20T09:38:10-07:00", "t_end": "2026-10-20T10:16:00-07:00",
 "type": "screen.page", "label": "Opened GitHub pull request #12",
 "place": "hall", "floor": 2, "x": 12.0, "y": 6.0,
 "source": {"kind": "browser_ext", "ref": "screens/0938_10.jpg", "url": "https://github.com/org/repo/pull/12", "title": "..."},
 "confidence": 0.98, "fidelity": "known", "attention": 0.71, "people": [], "topics": ["demo", "hinge"],
 "text": "Add hinge inference loop · pull request #12 ..."}
```
Types and required extras:

| type | extras | produced by |
|---|---|---|
| `place.visit` | `place`, `conf` | skeleton |
| `place.floor` | `place`, `floor`, `conf` | skeleton |
| `place.transit` | `from`, `to`, `mode` | skeleton |
| `screen.app` | `app`, `window_title`, duration via `t_end` | mind |
| `screen.page` | `url`, `title`, `scroll_depth` | mind (extension) |
| `screen.read` | `blocks[]` (ids), `attention` | mind (gaze) |
| `moment.video` | `moment_id` | moments |
| `moment.audio` | `moment_id` | moments |
| `said.quote` | `speaker`, `text`, `moment_id`, `t` precise | moments |
| `git.push` | `repo`, `commits` | mind (optional) |
| `calendar.event` | `title`, `attendees` | import (optional) |

### 2.4 `screens/ocr.jsonl`

```json
{"shot": "0938_10", "t": "2026-10-20T09:38:10-07:00", "app": "chrome.exe", "title": "Pull request #12",
 "w": 1920, "h": 1080, "blocks": [{"id": "b0", "bbox": [120, 210, 980, 262], "text": "Add hinge inference loop", "conf": 0.93, "attention": 0.71, "dwell_ms": 4200}]}
```
`bbox` is `[x0, y0, x1, y1]` in screen pixels. Thumbnails are 480 px wide JPEGs; full frames are never stored.

### 2.5 `moments/<id>/`

```
source.mp4            the original (or a symlink/copy)
meta.json             {"t0": ..., "duration_s": 42.1, "lat": ..., "lon": ..., "place": "cafe1", "camera": {"fov_deg": 62}}
keyframes/00001.jpg   2 fps
depth/00001.png       16-bit, millimeters (metric) or 0..65535 normalized (mono)
scene.json            {"kind": "2.5d", "keyframe": "00012", "mesh": "scene.glb", "camera_pose": [...], "speakers": [{"id": "S1", "side": "left"}]}
scene.glb             displaced mesh with the keyframe as texture (Tier 1)
scene.ply             Gaussian splat (Tier 2)
transcript.json       whisperx output, plus "speaker_map": {"SPEAKER_00": "person:sam"}
```

### 2.6 `graph.json`

```json
{"nodes": [{"id": "person:sam", "kind": "person", "label": "Sam"}, {"id": "place:cafe1", "kind": "place"}, {"id": "topic:demo", "kind": "topic"}, {"id": "mo_001", "kind": "moment"}],
 "edges": [{"from": "mo_001", "to": "person:sam", "rel": "with", "evidence": ["transcript:mo_001:seg3"]}, {"from": "ev_000123", "to": "topic:demo", "rel": "about", "evidence": ["ev_000123"]}]}
```

### 2.7 `index.sqlite` DDL

```sql
CREATE TABLE units (
  id INTEGER PRIMARY KEY, day TEXT, t TEXT, t_end TEXT, type TEXT,
  text TEXT, place TEXT, floor INTEGER, x REAL, y REAL,
  evidence_id TEXT, source_ref TEXT, attention REAL, fidelity TEXT
);
CREATE VIRTUAL TABLE units_fts USING fts5(text, content='units', content_rowid='id');
-- sqlite-vec:
CREATE VIRTUAL TABLE units_vec USING vec0(unit_id INTEGER PRIMARY KEY, embedding float[384]);
CREATE TABLE speakers (moment_id TEXT, diar_label TEXT, person_id TEXT, confirmed INTEGER);
CREATE TABLE deletions (t0 TEXT, t1 TEXT, reason TEXT, applied_at TEXT);
```
Unit rows come from: transcript segments (≤ 15 s, merged by speaker), OCR blocks with `attention ≥ 0.3` (or all blocks when gaze is unavailable, with `attention = null`), event labels, place names. The index is rebuildable from the files; `make index` drops and recreates it.

### 2.8 Venue pack `venues/<id>.json`

```json
{"schema": "daycube.venue/1", "id": "engineering-hall", "name": "Engineering Hall",
 "entrance": {"lat": 34.0694, "lon": -118.4430}, "axis_deg": 12.0, "floor_height_m": 3.2, "floors": 3,
 "levels": [{"index": 0, "walls": [[[0,0],[18,0]], [[18,0],[18,11]]], "doors": [[[3,0],[5,0]]], "stairs": [[15,4.5,2.4,2.0]],
             "rooms": [{"id": "lobby", "poly": [[0,0],[18,0],[18,4],[0,4]], "kind": "lobby"}],
             "graph": {"nodes": [[1,2],[6,2],[6,8]], "edges": [[0,1],[1,2]]}}],
 "wifi": [{"bssid": "aa:bb:...", "x": 6, "y": 2, "floor": 0, "rssi": -58}]}
```
Coordinates are meters in the venue frame; `axis_deg` rotates the venue frame to true north for placing it on the OSM footprint.

### 2.9 Schema enforcement

`packages/schemas/*.schema.json` (draft 2020-12). Python: `datamodel-codegen --input packages/schemas --output services/ingest/ingest/models.py` at build time, and `vault.validate(date)` runs `jsonschema` over every file. TypeScript: `json-schema-to-zod` into `apps/viewer/src/schemas.ts`. A failing validation is a failing build.

---

## 3. Skeleton pipeline (phone → places, floors, layers)

Module: `services/ingest/ingest/skeleton/`.

### 3.1 Inputs and parsers

- **Arc Timeline GPX** (`parsers/arc_gpx.py`): `trkpt` with `lat`, `lon`, `ele`, `time`; keep `ele` as `alt_abs`. Arc writes one `trk` per activity; concatenate in time order.
- **Apple Health export** (`parsers/health_xml.py`): stream-parse `export.xml` with `xml.etree.iterparse`; keep `HKQuantityTypeIdentifierFlightsClimbed` (`startDate`, `endDate`, `value`) and `HKQuantityTypeIdentifierStepCount`.
- **Google Timeline export** (`parsers/google_timeline.py`): `semanticSegments[]`; `visit.topCandidate.placeLocation.latLng` strings like `"34.0694°, -118.4430°"`; `timelinePath[].point` with `time`.
- **Recorder app JSONL** (Tier 2): `{"t":..., "lat":..., "lon":..., "hacc":..., "alt_rel":..., "steps":..., "heading":...}` at 1 Hz; iOS: `CLLocationManager` with `allowsBackgroundLocationUpdates = true`, `desiredAccuracy = kCLLocationAccuracyBest`, `CMAltimeter.startRelativeAltitudeUpdates`, `CMPedometer.startUpdates`; Android: `FusedLocationProviderClient` at 1 s, `Sensor.TYPE_PRESSURE`, `Sensor.TYPE_STEP_COUNTER`, `WifiManager.startScan` every 30 s (throttled by the OS).

All parsers emit a common `Fix(t, lat, lon, hacc, alt_abs, alt_rel, steps, heading, source)` list sorted by time.

### 3.2 Visit detection

```python
from sklearn.cluster import DBSCAN
coords = np.radians([[f.lat, f.lon] for f in fixes])
labels = DBSCAN(eps=25/6371000.0, min_samples=5, metric="haversine").fit_predict(coords)
```
Group consecutive fixes with the same cluster label; a visit is a run ≥ 4 minutes. `conf = clip(0.5 + 0.1*log10(n_fixes) - 0.3*(median_hacc/50), 0.3, 0.98)`. Gaps between visits become `place.transit` with `mode = walk` if median speed < 2.5 m/s else `vehicle`.

### 3.3 Place resolution

Overpass query (cached by rounded lat/lon):
```
[out:json][timeout:25];
way(around:40,{lat},{lon})["building"];
out geom tags;
```
Pick the footprint containing the centroid, else the nearest. Keep `osm_id`, `name`, `building:levels`, `height`, and the polygon (lat/lon). If a venue pack's `entrance` is within 60 m, attach the venue. Users can rename places; names are stored in `vault/places.json` and reused across days.

### 3.4 Floor estimation

Input: `alt_rel` (recorder) or `alt_abs` (GPX elevation); `floor_height_m` from the venue pack (default 3.3 m when unknown, labeled `estimated` with lower confidence).

1. Within a visit, median-filter altitude with a 15 s window.
2. Re-zero: `h0 = mean(alt over the first 20 s of the visit)`; `Δh = alt - h0`.
3. Candidate floor: `f = round(Δh / H)`. Apply hysteresis: switch floors only when `|Δh - f_current*H| > 0.6*H` for ≥ 10 s.
4. Confidence per segment: `conf = 1 - min(1, |Δh - f*H| / (0.5*H))`, then averaged with the Health "flights climbed" agreement when available (a flights sample within ±90 s of a floor change adds +0.15, capped at 0.98).
5. Emit `place.floor` events at changes; write `floor` and `floor_conf` into `skeleton.geojson`.

Known failure modes to state in the card: weather drift over hours (handled by re-zeroing at each visit start), elevators with pressurization transients (hysteresis), buildings without a venue pack (default floor height, lower confidence).

### 3.5 Indoor position

- **Tier 1:** `x, y` for the visit is a straight line from the entrance to the room implied by the screen log (the desk room on the visited floor) at `pos_conf = 0.4`, fidelity `illustrative`. The viewer draws it as a dotted path.
- **Tier 2 (recorder):** pedestrian dead reckoning: step events × stride (0.7 m default, calibrated from the GPS segment before entry) along the heading; then HMM map matching on the venue level graph: states = graph nodes (1 m spacing), emission `exp(-d²/(2σ²))` with `σ = 3 m`, transition allowed between neighbors within 2.5 m per second, Viterbi over the visit. `pos_conf` from the posterior margin. Wi-Fi fingerprints (Android) add an emission term from RSSI distance to the nearest fingerprint.

### 3.6 Layers

Layer order = visits in time order; visits shorter than 5 minutes merge into the previous layer as a `place.visit` pin. `layer_index` written into `day.json`. Each layer carries `floors_visited` so the viewer knows how tall to draw it.

### 3.7 Tests

`tests/test_visits.py`: synthetic track with two stops → two visits, correct durations. `tests/test_floors.py`: synthetic pressure profile with a 2-floor climb and an elevator transient → floors `[0, 2]`, no spurious switch. `tests/test_mapmatch.py`: PDR path with 15% heading error → snapped path within 2 m of truth on a corridor graph.

---

## 4. Mind layer (Windows capture, OCR, gaze, events)

Module: `services/capture-win/` (runs on the user's PC) and `services/ingest/ingest/mind/` (turns captured logs into events).

### 4.1 Capture service architecture

A single Python process with four threads and one shared, append-only log directory `capture/<date>/`:
- `sampler` (every 5 s): foreground window → `window.jsonl` `{t, app, title, pid}` via `win32gui.GetForegroundWindow()`, `win32gui.GetWindowText(hwnd)`, `win32process.GetWindowThreadProcessId(hwnd)` → `psutil.Process(pid).name()`. Skip if the app or title matches `config/exclude.yaml`.
- `shooter` (every 30 s, and 2 s after the extension reports a scroll or URL change, debounced): `mss` grab of the primary monitor → OCR → `screens/ocr.jsonl` + 480 px thumbnail. Full frame discarded after OCR. Skips when paused or excluded.
- `gaze` (15–30 FPS): webcam → features → calibrated model → fixations → `gaze.jsonl` `{t, x, y, conf, fix_id}` (fixations only, not raw samples, to keep the log small).
- `http` (FastAPI on `127.0.0.1:7700`): `POST /events` for the extension, `GET /status`, `POST /pause`, `POST /resume`, `DELETE /hours/{t0}/{t1}`.
- Tray (`pystray`): recording indicator (red dot = capturing, grey = paused), Pause/Resume, Delete last hour, Open vault, Quit. Hotkey `Ctrl+Alt+P` toggles pause (`keyboard` or `pynput`).

### 4.2 OCR

Primary: `rapidocr-onnxruntime` (`engine = RapidOCR(); result, _ = engine(img_bgr)`; result rows are `[box4pts, text, score]`). Convert the 4-point box to `[x0,y0,x1,y1]`. Merge boxes on the same line (vertical overlap > 60%, gap < 1.5 × height) into blocks, then merge consecutive lines into paragraphs when left edges align within 20 px. Alternative on Windows: `Windows.Media.Ocr` via the `winsdk` package (fast, bundled with the OS); keep the RapidOCR path as the known-good one. OCR runs in a worker process so it never blocks capture; budget ≤ 2 s per frame on CPU.

### 4.3 Chrome extension (MV3)

`manifest.json`: `"manifest_version": 3`, `"permissions": ["tabs", "scripting", "storage"]`, `"host_permissions": ["<all_urls>", "http://127.0.0.1/*"]`, background `service_worker`, content script on `<all_urls>` at `document_idle`.
- Content script: on load, on `scroll` (throttled 1 s), on `visibilitychange`: send `{url, title, scrollY, viewport, blocks}` where `blocks` are visible text elements (`p, li, h1..h6, pre, td, article`) with `getBoundingClientRect()` and `innerText` (first 500 chars). Skip when `document.activeElement` is `input[type=password]` or the URL matches the exclusion list stored in `chrome.storage.sync`.
- Background: batches messages and `fetch("http://127.0.0.1:7700/events", {method: "POST", body: JSON.stringify(batch)})` every 5 s. Drops silently if the service is down.
- The block rectangles let the mind layer map gaze onto page text even between screenshots, and give `scroll_depth`.

### 4.4 Gaze estimation

**Features** per frame from MediaPipe Tasks Face Landmarker (`face_landmarker.task` with iris; `running_mode = VIDEO`, `num_faces = 1`, `output_facial_transformation_matrixes = True`):
- Iris centers: landmarks 468–472 (left iris, 468 center) and 473–477 (right iris, 473 center). Eye corners: left 33 (outer), 133 (inner); right 362 (inner), 263 (outer). Eyelids for blink: left 159/145, right 386/374.
- Per eye: `u = (iris_x - corner_inner_x) / (corner_outer_x - corner_inner_x)`, `v = (iris_y - lid_top_y) / (lid_bottom_y - lid_top_y)`.
- Head pose from the 4×4 transformation matrix: yaw, pitch, roll (degrees) and translation `tx, ty, tz` (cm).
- Blink: eye aspect ratio `(lid_bottom - lid_top) / (corner_outer - corner_inner) < 0.18` → drop the frame.
- Feature vector `φ = [uL, vL, uR, vR, yaw, pitch, roll, tx, ty, tz]` plus pairwise products of the first six (quadratic terms) → 31 features, standardized with the calibration mean and std.

**Calibration** (`capture-win/gaze/calibrate.py`, a fullscreen Tk window): 9 targets on a 3×3 grid plus 4 at mid-edges, each shown 2.5 s; discard the first 0.5 s; collect `(φ, target_xy)`; fit `sklearn.linear_model.Ridge(alpha=1.0)` with `y ∈ R²`; evaluate on 4 held-out targets; store `{coef, intercept, mean, std, val_error_px}` in `vault/calibration/gaze-<session>.json`. Threshold: `val_error_px ≤ 150` → gaze enabled; else gaze disabled for the session and attention falls back to behavioral signals. Recalibrate on demand from the tray; auto-prompt when the head translation drifts more than 8 cm from the calibration mean for 30 s.

**Runtime:** smooth predictions with a one-euro filter (`min_cutoff = 1.0, beta = 0.007`) or an EMA with `α = 0.3`. Per-sample confidence from landmark presence scores and head-pose distance to calibration.

**Fixations (I-DT):** sliding window; while `(max_x - min_x) + (max_y - min_y) ≤ 50 px` extend the window; emit a fixation when duration ≥ 100 ms with centroid, duration, confidence. Log fixations only.

### 4.5 Attention per block

For each screenshot interval `[t_shot, t_next)`, with OCR blocks and extension blocks for the same window:
```
dwell(block) = Σ duration(fix) for fixations with centroid inside bbox(block) expanded by 40 px
visible(block) = time the block was on screen in the interval (from scroll events; else the whole interval)
attention(block) = min(1, dwell / max(visible, 0.5 s))
```
Mapping across screenshots: blocks are matched by text similarity (`rapidfuzz.ratio ≥ 85`) so attention accumulates while the page is static. When gaze confidence < 0.5 or gaze is disabled: `attention = 0.6 * scroll_visibility + 0.4 * mouse_proximity` (mouse within 150 px of the block), labeled `estimated`.

### 4.6 Event synthesis (`ingest/mind/synth.py`)

- `screen.app`: merge consecutive `window.jsonl` samples with the same app; keep sessions ≥ 60 s.
- `screen.page`: merge extension records by URL (ignoring fragments and tracking params); keep ≥ 20 s; `scroll_depth = max scrollY / page height`.
- `screen.read`: per page session, blocks with `attention ≥ 0.3`; `label` = page title; `text` = top blocks' text joined; `attention` = mean of the kept blocks. A page with gaze available but no block ≥ 0.3 becomes a `screen.page` with `attention = 0.05` ("skimmed").
- `git.push` (optional): a `post-push` git hook in the user's repos writes `{t, repo, commits}` to `capture/<date>/git.jsonl`.
- Pin to place and time: each event gets `place`, `floor`, `x, y` from the skeleton at `t` (nearest fix within 10 min; else `place = null`).

### 4.7 Reading-state classifier (Tier 2, optional real training)

Window = 2 s of fixations. Features: `n_fix`, mean and median fixation duration, mean saccade amplitude, fraction of rightward saccades, count of long leftward sweeps (> 300 px), vertical drift per second, gaze confidence mean. Labels from three 10-minute self-recordings (`reading`, `skimming`, `not_looking`). Model: `LogisticRegression(C=1.0)` or `HistGradientBoostingClassifier`; stratified 5-fold CV; report the confusion matrix in `eval/results.md`. The classifier's `reading` probability multiplies `attention` for the window.

### 4.8 Privacy controls (implemented, not promised)

Pause flag checked by every thread; `DELETE /hours` removes `window.jsonl` lines, screenshots, OCR and gaze in the range and records the deletion in `deletions`; exclusion list for apps, title regexes and URL patterns; password-field skip in the extension; thumbnails only; no audio capture on the PC except explicit moment recordings. The tray indicator is always visible while capturing.

### 4.9 Tests

`test_idt.py`: synthetic gaze with two fixations and a saccade → two fixations with correct centroids. `test_attention.py`: fixations inside and outside a block → expected attention. `test_synth.py`: window samples → merged sessions with durations.

---

## 5. Moments pipeline (camera roll → enterable scenes with transcripts)

Module: `services/ingest/ingest/moments/`; GPU jobs in `jobs/modal/`.

### 5.1 Ingest and pinning

```
ffprobe -v quiet -print_format json -show_format -show_streams source.mp4
```
Read `creation_time` and, when present, the `com.apple.quicktime.location.ISO6709` tag (iPhone) for GPS. Fallback: the user picks the place in the viewer. `t0` = creation time; `place` = the visit active at `t0`; `x, y` = the skeleton position at `t0` (Tier 1: the layer's path point at `t0`).

### 5.2 Frames and audio

```
ffmpeg -i source.mp4 -vf "fps=2,scale=960:-2" -q:v 3 keyframes/%05d.jpg
ffmpeg -i source.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le audio.wav
```

### 5.3 Transcript and speakers

Modal job `transcribe(audio_bytes) -> dict` runs:
```
whisperx audio.wav --model large-v3 --language en --diarize --hf_token $HF_TOKEN \
        --output_format json --output_dir out
```
Output: `segments[]` with `start`, `end`, `text`, `speaker`, and `words[]` with `start`, `end`, `word`, `score`. Store as `transcript.json`. Local fallback: `faster-whisper` (`large-v3` on GPU, `small` on CPU) without diarization; speakers then come from the user.

Speaker mapping: `speaker_map` in `transcript.json` starts empty; the card shows "Who is SPEAKER_01?" with a person picker; confirmation writes `speakers` rows and rewrites `said.quote` events with `speaker = person_id`. Quotes are emitted per segment (≤ 15 s), `t` = segment start, `text` = segment text, `source.ref = transcript:<moment>:<segment index>`.

### 5.4 Depth and the 2.5D scene (Tier 1)

Known-good depth path (CPU or GPU):
```python
from transformers import pipeline
depth = pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf")
out = depth(Image.open("keyframes/00012.jpg"))  # out["depth"] is a PIL image (relative depth)
```
Upgrade path: Depth Anything 3 per the repo README (metric variants give meters; the small Apache model fits in 12 GB). Save depth as 16-bit PNG.

Keyframe choice: the sharpest frame (variance of Laplacian) among the frames closest to the loudest second of audio, so the scene matches the moment people remember.

Mesh: a 256 × 144 grid plane; vertex `z = -depth_normalized * scale` with `scale` chosen so the median depth sits 2.5 m from the camera; UVs map the keyframe as the texture; export `scene.glb` with `trimesh` (`trimesh.Trimesh(vertices, faces, visual=TextureVisuals(uv, image))`). Edge stretching is accepted; the viewer limits orbit to ±25° so stretched borders stay out of view.

### 5.5 Gaussian splats (Tier 2)

1. Poses: `python demo_colmap.py --scene_dir moments/<id>/colmap` from the VGGT repo (writes a COLMAP sparse model from the keyframes). Fallback: COLMAP itself (`colmap automatic_reconstructor`, minutes).
2. Train: `python examples/simple_trainer.py default --data_dir moments/<id>/colmap --data_factor 1 --max_steps 7000 --result_dir moments/<id>/gs --save_ply` (gsplat; flag names per the installed version's `--help`).
3. Copy the final `.ply` to `scene.ply`. Spark loads `.ply` directly; optional `spz` compression halves the size.
4. People: SAM 3 masks on the keyframes → billboards placed at the median depth of the mask, rendered as alpha-cut quads at the camera pose for their frame. Holes behind them are left visible.

Budget on Modal (A10G): poses 20–60 s, training 2–4 min at 7000 steps for a 40-second clip. Only two clips are splatted for the demo.

### 5.6 Face blur and consent

Before any frame is stored: MediaPipe Face Detection on keyframes; blur (Gaussian, kernel 31) every face not matched to a tagged person (matching via a per-person embedding from `insightface` is Tier 3; Tier 1 blurs everyone except the owner, who is tagged once). Audio of others is only used from clips the team recorded with consent; the consent form is `docs/consent.md`.

### 5.7 Spatial audio metadata

For each speaker label, `side` = the horizontal position of the speaking face in the keyframe when a face is talking (mouth-open ratio from landmarks correlates with the segment); else `center`. The viewer places a `PannerNode` at `x = ±1.5 m` for left/right.

---

## 6. Index and ask (retrieval, grounding, verification)

Module: `services/ingest/ingest/index/` and `ingest/ask/`.

### 6.1 Building the index

```python
import sqlite3, sqlite_vec
from sqlite_vec import serialize_float32
from fastembed import TextEmbedding

db = sqlite3.connect(path); db.enable_load_extension(True); sqlite_vec.load(db); db.enable_load_extension(False)
model = TextEmbedding("BAAI/bge-small-en-v1.5")       # 384-d, CPU, ~1 ms per short unit after warmup
for unit in units:
    cur = db.execute("INSERT INTO units(day,t,t_end,type,text,place,floor,x,y,evidence_id,source_ref,attention,fidelity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ...)
    uid = cur.lastrowid
    db.execute("INSERT INTO units_fts(rowid, text) VALUES (?, ?)", (uid, unit.text))
for uid, vec in zip(ids, model.embed(texts)):
    db.execute("INSERT INTO units_vec(unit_id, embedding) VALUES (?, ?)", (uid, serialize_float32(vec.tolist())))
```
Unit text for events = `label + " " + text`; for quotes = `speaker + ": " + text`; for OCR blocks = block text with the page title prefixed.

### 6.2 Query pipeline

1. **Rewrite** (Claude Haiku 5.5, structured output):
```python
from pydantic import BaseModel
class Rewrite(BaseModel):
    terms: list[str]; time_window: list[str] | None; place_hint: str | None; people: list[str]; wants: str  # "moment"|"screen"|"place"|"any"
r = client.messages.parse(model="claude-haiku-5-5", max_tokens=512, output_config={"effort": "low"},
    system=[{"type": "text", "text": REWRITE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": f"Date: {date}. Places today: {place_names}. People today: {people}. Question: {q}"}],
    output_format=Rewrite)
rw = r.parsed_output
```
2. **Retrieve:** FTS5 `MATCH` on the terms (top 30, `bm25()` scores) and `units_vec` nearest neighbors on the embedded question (top 30). Fuse with reciprocal rank fusion `score = Σ 1/(60 + rank)`. Apply filters softly: +0.02 for units inside `time_window`, +0.02 for `place_hint` match, +0.01 for `wants` type match. Take top 10.
3. **Locate:** the top unit gives `(place → layer_index, t, floor, x, y)`; the viewpoint is the layer camera looking at `(x, y)`; for `moment.*` and `said.quote` units, the viewer opens the moment at `t - moment.t0`.
4. **Phrase** (Claude Opus 5.5, default model; the summary never asserts beyond the evidence):
```python
resp = client.beta.messages.create(
    model="claude-opus-5-5", max_tokens=1200,
    output_config={"effort": "medium", "format": {"type": "json_schema", "schema": CARD_SCHEMA}},
    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    system=[{"type": "text", "text": CARD_SYSTEM, "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": json.dumps({"question": q, "evidence": top10_as_dicts})}])
card = json.loads(next(b.text for b in resp.content if b.type == "text"))
```
`CARD_SCHEMA`: `{"answer": [{"sentence": str, "evidence": [str]}], "teleport": {"unit_id": int}}`, `additionalProperties: false`. Server-side fallbacks are enabled by default so a safety decline re-runs on a fallback model inside the same call; drop the two parameters if you do not want that.
5. **Verify:** rule: every `evidence` id must be in the top-10 list, and the sentence must share ≥ 1 content token (after stop-word removal) with the cited evidence text; otherwise ask Claude Haiku 5.5 as a judge (`{"supported": bool}` structured output, `effort: low`) and drop unsupported sentences. Log every drop to `eval/grounding.log`.
6. **Return** `{layer_index, t, floor, x, y, moment_id?, offset_s?, sentences[], evidence[]}`.

Latency budget: rewrite ≤ 250 ms, retrieval ≤ 30 ms, phrasing ≤ 400 ms streamed, verify ≤ 100 ms; the viewer teleports as soon as step 3 completes and fills the card as sentences arrive.

### 6.3 Prompts (store in `ingest/ask/prompts/`, keep stable for caching)

`REWRITE_SYSTEM`:
```
You turn a question about one person's day into a search request over their own records.
Output only the schema. terms: 3-8 distinctive words or short phrases likely to appear in transcripts, page titles or event labels.
time_window: two ISO times if the question implies a time ("this morning" = 05:00-12:00 local; "lunch" = 11:30-14:00), else null.
place_hint: one of the given place names if implied, else null. people: names mentioned. wants: moment | screen | place | any.
```
`CARD_SYSTEM`:
```
You write the card for a memory system. You are given evidence records from the user's own day. Each record has an id.
Write 1-4 short sentences that answer the question using only the evidence. Every sentence must cite the ids it relies on.
Never add facts that are not in the evidence. If the evidence does not answer the question, say so in one sentence and cite nothing.
Choose teleport.unit_id as the single record a person would most want to be taken to.
```
`JUDGE_SYSTEM`: "Given a sentence and the evidence records it cites, answer whether every claim in the sentence is directly supported by the cited text. Output only the schema."

### 6.4 Daily summary and cube faces

Once per day (`make index`): Claude Opus 5.5, `effort: medium`, input = all events and quotes for the day as compact JSON (≤ 40k tokens), output schema `{"places": [{"id": str, "summary": str, "evidence": [str]}], "day_summary": str, "cube_faces": [str]}` with the same citation rule and verifier. `cube_faces` must be six ids among moments and events; the model is told to prefer moments, quotes, long reads and place changes, spread across the day. The viewer renders faces from thumbnails (moment keyframe, screenshot thumbnail, or a place icon).

### 6.5 Tests

`test_rrf.py`: known ranks → fused order. `test_verify.py`: a sentence citing a missing id is dropped; a sentence with no shared tokens goes to the judge (mocked). `test_ask_fixture.py`: the fixture day with 10 questions and expected unit ids.

---

## 7. API (FastAPI, local)

All endpoints under `http://127.0.0.1:7701`, bearer token from `vault/.token` (generated at setup) required for mutating calls; CORS allows `http://localhost:3000` and the extension origin.

| Method | Path | Body / params | Returns |
|---|---|---|---|
| GET | `/days` | | `[{date, stats}]` |
| GET | `/day/{date}` | | `day.json` plus derived `layers[]` for the viewer (see 8.1) |
| GET | `/day/{date}/events` | `?t0&t1&type` | events slice |
| GET | `/day/{date}/skeleton` | | geojson |
| GET | `/moment/{id}` | | `scene.json`, transcript, speaker map; files served from `/vault/...` static mount |
| POST | `/ask` | `{date, question}` | ask result (6.2 step 6); supports `?stream=1` as server-sent events |
| POST | `/events` | batch from the extension or capture service | `{accepted}` |
| POST | `/ingest/{date}` | `{steps: ["skeleton","mind","moments","index","summary"]}` | job id; progress via `GET /jobs/{id}` (SSE) |
| POST | `/speakers/confirm` | `{moment_id, diar_label, person_id}` | ok, re-synthesizes quotes |
| POST | `/places/rename` | `{place_id, name}` | ok |
| DELETE | `/day/{date}/hours` | `{t0, t1}` | ok, deletion recorded |
| POST | `/pause`, `/resume` | | capture state |

`/jobs/{id}` emits `{stage, pct, message}` events the viewer shows as the pipeline cinematic during the live demo.

---

## 8. Viewer (Next.js + React Three Fiber)

### 8.1 Types (zod-generated; the shapes the mock already uses)

```ts
type Layer = { index: number; placeId: string; name: string; kind: "house"|"cafe"|"venue"|"outdoor";
  t0: number; t1: number;                   // seconds of day
  frame: { lat: number; lon: number; axisDeg: number; size: [number, number] };
  floors: number[]; floorHeight: number;
  geometry: { footprint: [number, number][]; levels?: VenueLevel[] };   // meters, layer-local
  path: { x: number; y: number; floor: number; t: number; conf: number }[];
  events: EventPin[]; quotes: Quote[]; moments: MomentRef[];
  summary: { text: string; evidence: string[] }; screen: string;
};
type EventPin = { id: string; t: number; x: number; y: number; floor: number; kind: "pin"|"event"|"moment";
  label: string; source: string; fidelity: "known"|"estimated"|"illustrative"; confidence: number; attention?: number; momentId?: string };
type Quote = { t: number; who: string; them: boolean; text: string; evidence: string };
type AskResult = { layerIndex: number; t: number; floor: number; x: number; y: number; momentId?: string; offsetS?: number;
  sentences: { text: string; evidence: string[] }[]; evidence: EvidenceRecord[] };
```
`GET /day/{date}` builds `layers[]` server-side from `day.json`, `skeleton.geojson`, `events.jsonl` and the venue packs, so the viewer has no pipeline logic.

### 8.2 Scene generation

- **Exterior block:** rasterize the OSM footprint polygon (converted to layer-local meters, rotated by `axisDeg`) onto a 1 m grid with `point-in-polygon`; each filled cell becomes a column of cubes to the building height (`height` tag, else `levels × 3.3`), capped at 4 floors for the diorama. Windows are a darker cube every 3 m on exterior cells. Roads and lawns from the OSM `highway` and `landuse` ways within 40 m, rasterized to flat tiles.
- **Dollhouse (venue):** from the venue pack: slab per visited floor plus the ground floor; walls as boxes along wall segments (0.25 m thick, 1.1 m tall in dollhouse mode); doors as gaps; stairs as a stepped block; rooms get props by `kind` (desk + chair for `office`, counter + tables for `cafe`, bed + desk for `bedroom`).
- **Props kit:** 12 voxel prefabs in `apps/viewer/src/kit/` (tree, lamp, desk, chair, table, counter, bed, sofa, car, bench, sign, plant), each a small list of boxes.
- **Path:** glowing cubes every 0.6 m along `path[]` up to the current time, at the floor's z; transit between layers as a dotted line.
- **Pins and frames:** billboards (sprites) above `(x, y, floor)`; frames for moments show the keyframe thumbnail.
- **Character:** 10 boxes; walk cycle driven by path speed; speech bubble (HTML overlay via drei `<Html>`) when a quote is within ±4 min.

### 8.3 Rendering

- One `InstancedMesh` per material (about 8 materials); cube geometry shared; per-instance color via `setColorAt`; `MeshStandardMaterial` with `flatShading` and low roughness variation; a `DirectionalLight` whose azimuth follows time of day plus a `HemisphereLight`; soft shadows (`PCFSoftShadowMap`, 2048 map) on the active layer only.
- Orthographic camera; drei `OrbitControls` with `minPolarAngle = maxPolarAngle = 0.95 rad`, zoom limits; `follow` mode lerps the target to the active layer's center at `z = base + 0.45 × layerHeight`.
- Inactive layers: material `opacity = 0.3` through a per-layer group uniform (clone materials per layer, or render inactive layers to a second instanced set).
- Postprocessing: `@react-three/postprocessing` `EffectComposer` with `DepthOfField` (`focusDistance` tied to the active layer, `bokehScale = 3`) and `Vignette`; `prefers-reduced-motion` disables the walk bob and camera lerp.
- Budget: ≤ 8 draw calls for the diorama, ≤ 20k instances per day, 60 FPS on an Intel Iris or Apple M-series integrated GPU at 1080p; first render ≤ 2 s from a warm API.

### 8.4 Moment view

- 2.5D: load `scene.glb` with `useGLTF`; camera at the recorded pose; orbit clamped to ±25° yaw, ±15° pitch; the video element (`<video>` as a `VideoTexture`) plays in a frame at the camera pose, synced to the transcript cursor.
- Splat (Tier 2): `import { SplatMesh } from "@sparkjsdev/spark"`; `new SplatMesh({ url })`; orbit clamped to the captured frustum ±40°.
- Audio: `AudioContext`, `MediaElementAudioSourceNode` → `PannerNode` (`panningModel: "HRTF"`, position from `speakers[].side`) → destination; the listener at the camera. Audio starts on a click because of autoplay rules.
- Transcript strip under the scene; clicking a segment seeks the video; the quote that an `ask` teleported to is highlighted.

### 8.5 Cube shelf and card

- Shelf: a row of cubes (one per day), each face a `CanvasTexture` of the thumbnail with a caption; click → the layers fly in (camera `open` tween from the mock).
- Card: React panel; sentences with evidence pills; clicking a pill highlights the pin or seeks the moment; speaker confirmation control; "open moment" and "show on screen" buttons.
- Ask box: on submit, `POST /ask?stream=1`; teleport on the first event, fill sentences as they arrive.

### 8.6 Head-tracked parallax (Tier 3)

MediaPipe Face Landmarker in the browser (`@mediapipe/tasks-vision`) at 20 FPS; head position in camera space → off-axis projection (`camera.projectionMatrix` from a skewed frustum); low-pass filter; disabled on phones and under reduced motion.

### 8.7 Tests

Playwright: load the fixture day, open the cube, play to 10:05, click the character, assert the card shows the café summary and two quotes; ask "where was I when we decided the demo order" and assert the layer index changes. Storybook stories for the card and the pins at all three fidelity levels.

---

## 9. Venue pack tooling and calibration

### 9.1 Tracing tool (`apps/viewer/src/tools/trace`)

A page that loads a floor-plan photo, lets the user set scale by clicking two points of known distance (a door, 0.9 m), then draw walls (polyline), doors (segments), stairs (rectangles), rooms (polygons with a `kind`), and the corridor graph (nodes and edges). Export `venues/<id>.json`. Keyboard: `W` wall, `D` door, `S` stairs, `R` room, `G` graph, `Z` undo. Thirty minutes per floor is the target.

### 9.2 Calibration walk (30 minutes, person D)

1. Start the recorder (or Arc) at the entrance; stand still 30 s (re-zero).
2. Walk each corridor once at normal pace; note the time you pass each door (voice memo).
3. Climb each stair once, stopping 20 s per landing; the altitude steps give `floor_height_m` (median of the per-floor deltas).
4. Android only: stand 10 s at each corridor node for Wi-Fi fingerprints.
5. Export; run `python -m ingest.skeleton.calibrate --venue engineering-hall --walk walk.jsonl` which writes `floor_height_m`, checks the stride estimate, and reports floor-detection accuracy on the walk itself.

---

## 10. Jobs on Modal

```python
import modal
app = modal.App("daycube")
img = (modal.Image.debian_slim(python_version="3.11").apt_install("ffmpeg", "git")
       .pip_install("whisperx", "torch", "torchaudio", "transformers", "pillow", "numpy"))

@app.function(image=img, gpu="A10G", timeout=900, secrets=[modal.Secret.from_name("hf-token")])
def transcribe(audio: bytes, language: str = "en") -> dict: ...

@app.function(image=img, gpu="A10G", timeout=600)
def depth(frames: list[bytes]) -> list[bytes]: ...

@app.function(image=img_gs, gpu="A10G", timeout=1800)
def splat(keyframes_tar: bytes) -> bytes: ...
```
Deploy once (`modal deploy jobs/modal/jobs.py`); the ingest service calls `modal.Function.from_name("daycube", "transcribe").remote(audio_bytes)` (older SDKs: `modal.Function.lookup`). Retries: one automatic retry on failure, then the local fallback. Cost at A10G rates: a 1-minute clip costs cents; the whole demo day is under a few dollars.

---

## 11. Evaluation harness

### 11.1 Retrieval accuracy

`eval/questions.jsonl`: `{"q": "where was I when we decided the demo order?", "date": "2026-10-20", "gt_unit_type": "said.quote", "gt_t": "2026-10-20T12:40:05-07:00", "gt_place": "cafe2", "tolerance_s": 5}`; fifty questions written by person D from the seven real days, covering screen, moment, place and people questions in roughly equal parts. `eval/score_retrieval.py` runs `/ask` for each, scores top-1 and top-3 hits (`|t_pred - gt_t| ≤ tolerance_s` and place match), and writes `eval/results/retrieval.json` plus a markdown table. Also logs the rewrite output so failures can be attributed to rewrite, retrieval or locate.

### 11.2 Grounding audit

`eval/score_grounding.py`: for 100 card sentences across the question set, counts sentences with zero evidence ids, sentences dropped by the verifier, and sentences a human marks unsupported in a 20-sentence spot check. Report the three numbers.

### 11.3 Floor and position accuracy

From the calibration walk and the volunteers' sessions: ground truth floor changes from the voice memos; `eval/score_floors.py` compares `place.floor` events (± 60 s) and reports precision and recall. Position: ten checkpoints with known venue coordinates; mean error in meters against the Tier 2 path (Tier 1 reports "not evaluated, illustrative path").

### 11.4 Gaze validity

`eval/gaze_pages/`: ten pages, each with instructions "read paragraphs 1, 3 and 5; skip 2 and 4". Participants read; the pipeline produces per-block attention; `eval/score_gaze.py` thresholds at 0.3 and reports accuracy, precision and recall for read-vs-skipped, plus the calibration error distribution.

### 11.5 The recall experiment (the thesis)

Protocol in `eval/experiment/protocol.md`:
- n = 6–10 volunteers, consented (`docs/consent.md`), each 40 minutes at the venue carrying the recorder and doing three laptop tasks with the capture service on.
- Next day, 20 questions each, generated from their own data by person D using a template bank (`eval/experiment/templates.md`: where/when/what-was-on-screen/who-said).
- Two interfaces, counterbalanced: A = plain timeline list of their events with search; B = the DAYCUBE world. Ten questions per interface, order randomized per participant, interface order alternated.
- Measure: accuracy (judged against the data), time to answer (stopwatch in the interface), and a 1–5 confidence rating.
- Report means and a paired comparison per participant; with small n, report effect sizes and be explicit about uncertainty. Put the raw table in `eval/results/experiment.csv`.

### 11.6 Latency and cost

`eval/bench.py`: ask latency (p50, p95 over the 50 questions), moment pipeline time per minute of video (Tier 1 and Tier 2), index build time, API token usage per ask (from `response.usage`) and the day's total model cost.

---

## 12. Privacy, security and consent (implemented controls)

- **Data flow:** phone exports and camera-roll files are copied into the vault by the user; the capture service writes only to the vault; the viewer and API bind to `127.0.0.1`; the only outbound traffic is Modal jobs (clip audio or keyframes) and Claude API calls (question text plus the top-10 evidence records). Both are listed in `docs/architecture.md` with what leaves the machine and when.
- **What is never stored:** full-resolution screenshots after OCR; raw webcam frames (only fixation coordinates); password fields; excluded apps and URLs; audio from the PC unless the user starts a moment recording.
- **User controls:** visible indicator; pause hotkey; delete-any-hour; per-source toggles (gaze, screenshots, extension); vault export is the folder itself; `make wipe DATE=...` removes a day and its index rows.
- **Others' data:** faces blurred unless tagged; audio only from consented recordings; the demo uses team members and consenting volunteers; the consent form names what is captured, where it is stored, and how to delete it.
- **Threats considered:** a stolen laptop (Tier 3: vault encryption with `age`, key in Windows Credential Manager); a malicious page trying to inject instructions through OCR text into the card (the LLM only phrases evidence; instructions inside evidence are data; the verifier rejects sentences that are not supported by evidence; the system prompt says evidence is data, never instructions); the extension posting to the wrong service (token check on `POST /events`).

---

## 13. Testing and CI

- `make test` runs: schema validation of `fixtures/sample-day`; Python unit tests (`pytest -q`) for visits, floors, map matching, I-DT, attention, event synthesis, RRF, verifier; viewer type-check and Playwright smoke on the fixture.
- GitHub Actions on push: the same, minus Playwright on Windows runners; the capture service has a `--dry-run` mode that replays a recorded `capture/` directory so the mind pipeline is testable on any OS.
- Contract tests: `tests/test_contract.py` loads a produced vault through the viewer's zod schemas (via a small Node script) so a pipeline change that breaks the viewer fails in CI, not on stage.
- Golden path checkpoints (the integration schedule): H8 fixture day renders end to end; H16 one real hour flows from capture to viewer; H24 one real moment with transcript opens from a pin; H28 `/ask` teleports on the fixture and on a real day; H30 cut meeting with `make demo` green.

---

## 14. Runbook

### 14.1 Setup (person B, hour 0; others pull the repo)

```
git clone <repo> && cd daycube
make setup          # creates .venv (uv or venv), installs services/ingest + capture-win requirements, pnpm install in apps/viewer
cp .env.example .env  # ANTHROPIC_API_KEY, HF_TOKEN, MODAL_TOKEN_ID/SECRET, OVERPASS_URL
modal token set --token-id ... --token-secret ...
modal deploy jobs/modal/jobs.py
python -m ingest.vault init --root ./vault     # writes vault/.token and vault/places.json
```
Windows capture (on the demo laptop):
```
cd services/capture-win && python -m capture --vault ..\..\vault --calibrate   # runs the 13-point calibration, then starts the tray
```
Chrome: `chrome://extensions` → Developer mode → Load unpacked → `extensions/chrome`.

### 14.2 Ingest a day

```
python -m ingest.skeleton.parse --date 2026-10-20 --arc exports/arc.gpx --health exports/export.xml --out vault
python -m ingest.skeleton.build --date 2026-10-20 --venues venues/        # visits, places, floors, layers → day.json, skeleton.geojson
python -m ingest.mind.synth --date 2026-10-20                             # capture/ → events.jsonl, screens/
python -m ingest.moments.run --date 2026-10-20 --videos exports/videos/   # pins, keyframes, transcript, depth, scene
python -m ingest.index.build --date 2026-10-20                            # index.sqlite, graph.json
python -m ingest.ask.summarize --date 2026-10-20                          # place summaries, cube faces
make viewer                                                               # http://localhost:3000
```
`make ingest DATE=2026-10-20` runs all six in order and prints a timing table.

### 14.3 Live demo procedure (Tier 2)

1. T−30 min: capture service running and calibrated on the demo laptop; recorder app installed on the demo phone; venue pack loaded; `make demo` green on the seven pre-collected days.
2. The judge walks (3 min) with the phone and films 15 s on floor 2; meanwhile a teammate opens the sponsor page on the laptop.
3. AirDrop or USB the recorder JSONL and the clip into `exports/live/`; run `make live` (skeleton for the walk window, moment pipeline for the clip, index update, no summary); progress shows in the viewer's pipeline cinematic. Target under 3 minutes; the Tier 1 2.5D path is used for the live clip.
4. Open the new layer, click the character, ask "where was I when the sponsor page was opened?".
5. Open `vault/` in Explorer.

### 14.4 Recovery

- Capture service dead: `python -m capture --resume` continues the same day; events are append-only.
- Modal down: `INGEST_LOCAL=1 make ingest` uses faster-whisper small and Depth Anything V2 small on CPU (slower, works).
- Index corrupt: `make index` rebuilds from files.
- Viewer blank: `pnpm build && pnpm start` serves the production build; check `GET /day/<date>` returns 200.
- Gaze calibration fails on stage: disable gaze in the tray; attention falls back to behavioral signals; say so.

---

## 15. Fixture: `fixtures/sample-day/` (abbreviated)

`day.json` with five places as in the mock (home, café, hall, café, home); `skeleton.geojson` with 60 fixes; `events.jsonl` with the 11 events from the mock plus three `said.quote` lines; `screens/ocr.jsonl` with two screenshots and six blocks; `moments/mo_001/` with a 10-second CC-licensed clip, a transcript with two segments and two speakers, a 2.5D `scene.glb`; `graph.json` with four nodes; `index.sqlite` built by `make index DATE=sample`. The viewer's Storybook and the Playwright test run against this fixture, and the first thing the frontend owner does at hour zero is render it.

---

## 16. Decisions log and open questions

| Decision | Choice | Why |
|---|---|---|
| Vault format | plain files + rebuildable SQLite | integration contract, local-first, debuggable |
| Gaze model | ridge regression on landmark features | data-efficient, CPU, explainable; CNN is a post-hackathon upgrade |
| OCR | RapidOCR primary | cross-platform, known-good; Windows OCR as an alternative |
| Embeddings | bge-small via fastembed | 384-d, CPU, no network |
| LLMs | Claude Opus 5.5 for phrasing and daily summary; Claude Haiku 5.5 for rewrite and judge | quality where it shows, cost where it does not; structured outputs everywhere; prompts cached |
| Scenes | 2.5D by default, splats for two clips | reliability on stage |
| 3D style | voxel diorama, dollhouse interiors | honesty, coverage, performance, shareability |
| Indoor position | building + floor reliable; corridor-level Tier 2; never desk-level | avoid the accuracy sinkhole |

Open: whether the recorder app ships (needs a Mac or Android); whether the volunteers can be scheduled on day one; whether Depth Anything 3's small model is used or V2 (either is fine for 2.5D); whether to show the reading-state classifier on the slide (only if its CV numbers are honest and good).
