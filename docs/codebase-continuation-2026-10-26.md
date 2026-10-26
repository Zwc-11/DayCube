# Continued codebase review

In progress from `2450e86`. The earlier [performance pass](codebase-efficiency-2026-10-26.md)
retains its own results and unfinished coverage; this continuation reviews those remaining modules.
The same pinned DeepInfer reference supplies ownership, preparation/replay and batching principles.
This is not a reproduction of its accelerator results or a claim that every dependency is optimal.

## Failed and late scene loaders

An isolated browser fixture exposed two ownership failures: MeshView registered cleanup only after
awaiting glTF, and SplatView left its loading animation running after both loads failed. A scene now
registers releases as it acquires resources. Releases run once in reverse order, including late
models arriving after close. Failure closes the renderer/controls/observer/loop; shared mesh geometry,
materials and textures are released once. Error UI remains explicit and the successful view's design
is retained. Cleanup errors are reported while the remaining releases continue.

| Synthetic scenario | Before | After |
| --- | --- | --- |
| Mesh load rejects | Renderer not disposed, including after unmount | One disposal |
| Mesh arrives after unmount | Renderer/late geometry not disposed | Renderer and geometry disposed once |
| Both splat loads reject | 18 further draws in a 300 ms sample | Zero draws after settling; one disposal |

These are actual renderer-method/geometry counters in headless Chromium, not FPS, RSS or hardware
GPU measurements. No recording/frame is read or saved. The baseline profiler/fixture source is
retained in commit `20c09d4`; the after profiler shares its driver with three browser regressions.
Reports: [before](../eval/results/scene-lifecycle-before-2026-10-26.json),
[after](../eval/results/scene-lifecycle-after-2026-10-26.json).

## Camera map playback

The filtered points array invalidated geometry preparation on every media update, and SVG path
strings were rebuilt on every render. The map now owns one prepared Float64 coordinate buffer and
one path string per camera input. Cursor lookup uses bisection for chronological poses and the
original first-match scan for unordered/invalid times. Its projection, heading, units, clamping,
duplicate-time policy and final SVG markup are unchanged. Extents no longer spread arrays into
function arguments, so long recordings do not exceed the JavaScript argument limit.

Actual React component, synthetic paths, 61 updates after five warmups, Chromium 156.0.8078.4,
Node 24.11.1, 800×600/device scale 1, Ryzen 7 9800X3D. Preparation and SVG paint are outside the
React update durations. Allocation sampling covers the whole page across those updates, including
collected objects; it is cumulative statistical allocation, not peak live memory.

| Points | Before median / p95 | After median / p95 | Before / after sampled allocation |
| --- | ---: | ---: | ---: |
| 600 | 0.20 / 0.40 ms | 0.10 / 0.20 ms | 14.24 / 0.71 MB |
| 12,000 | 2.60 / 3.80 ms | 0.10 / 0.20 ms | 276.11 / 0.79 MB |
| 50,000 | 9.10 / 13.80 ms | 0.10 / 0.20 ms | 1,134.02 / 0.77 MB |

Exact SVG hashes match at each scale. The baseline source is retained in `93764d2`. Unit cases
compare the original projection, cursor ties/order and a 150,000-point buffer. Reports:
[before](../eval/results/camera-map-before-2026-10-26.json),
[after](../eval/results/camera-map-after-2026-10-26.json).

## Long day adaptation and status accuracy

The real `fetchDay` path returned `offline` for 200,000 unpositioned path points because a spread
of their confidence values exceeded the browser's argument limit. A reduction preserves the
minimum and initial 0.4 cap without a temporary values array or argument expansion. The browser
regression fails before the change and passes after with confidence 0.125 and illustrative fidelity.
No transport latency improvement is claimed from this correctness case.

The disconnected viewer no longer says recording is stopped: it directs users to the extension's
authoritative status. Privacy text also reflects stored original phone media and unavailable face
blurring, rather than promising processing that is not implemented. No live capture state changed.

## Coverage and validation

Review notes are being added per file and will be merged into the hash-bound ledger after the
remaining source/test review. Identified gaps stay visible; reading a module is not proof of optimal
algorithms or completed live-camera/GPU/model acceptance. Final check results and coverage are pending.
