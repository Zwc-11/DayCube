# Viewer rendering performance

The viewer uses demand-driven rendering. A paused diorama should finish camera easing and stop
drawing. OrbitControls invalidates frames while orbiting/zooming; playback requests animation
frames while it runs. The scene and timeline pause while a modal is open or the page is hidden.
The 2D fallback redraws for data/time/camera changes and resize, rather than running an idle loop.

The default render resolution is capped at one device pixel per CSS pixel. Directional shadows
use a cached 512px map refreshed for scene, light/time, cutaway and floor changes. The separate
contact-shadow pass was removed. Large backdrop blurs were replaced with opaque panels; tilt-shift
and mouse parallax remain opt-in. Hover motion values avoid React updates on every pointer move.
The orthographic nameplate uses fixed pixel sizing rather than a distance factor that magnified
its DOM surface hundreds of times. The FPS readout no longer clamps low measurements to 30.

## Measured idle sample

Same 1440×900 page, device scale factor 1.5, four seconds idle after settling, headless Chromium
using its SwiftShader software renderer:

| Measurement | Before | After demand rendering |
| --- | ---: | ---: |
| GPU draw calls in sample | 1,624 | 0 |
| Main-thread task time | 4,143 ms | 1 ms |
| Main-thread tasks longer than 50 ms | 55 | 0 |
| WebGL backing resolution | 2160×1350 | 1440×900 |

These numbers describe this idle benchmark, not a promised interactive frame rate on every GPU.
The final label-size fix additionally removes the oversized composited surface.

With the local dev server running, collect a current sample with:

```powershell
node scripts/profile-viewer.mjs current
```

The report is saved to `test-results/perf-current.json`. Regression checks run with:

```powershell
npm run test:e2e:viewer
```

Those checks verify that idle WebGL/Canvas2D draws stop, orbiting and timeline scrubbing redraw,
playback renders, modal background rendering stops, the nameplate remains normal sized, and API
token binding/search/reindex still work. GPU tests run one browser at a time.

Implementation references: [React Three Fiber demand rendering](https://r3f.docs.pmnd.rs/advanced/scaling-performance)
and the installed drei OrbitControls implementation, which invalidates on control changes.
