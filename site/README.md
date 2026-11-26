# DayCube product page

The page at https://zwc-11.github.io/DayCube/: one scroll through the fictional sample day, from the
cube on the landing view to the replay, Ask, Recall and Memories, then the app's screenshots, the
vault and the architecture. It has its own dependencies and lockfile; nothing in the app imports it.

```powershell
cd site
npm ci
npm run dev        # http://localhost:3100
npm run build      # type check, then dist/
```

`.github/workflows/site.yml` builds it and publishes it with GitHub Pages on every push to `main`
that touches `site/`, `docs/media/` or the font. Pages must be set to **Source: GitHub Actions** once
in the repository settings.

## How it is put together

```text
src/lib/scroll.ts     one scroll state per frame: section progress and the page clock (06:45 to 23:24)
src/lib/sky.ts        sky colours by hour; every surface colour follows the clock
src/lib/day.ts        the sample day from fixtures/sample-day: visits, events, times
src/lib/ask.ts        the notes graph and the three questions the demo answers
src/three/director.ts scene state from scroll: cube, world, streams, graph, memory, camera keys
src/three/Stage.tsx   the one fixed canvas: lights, camera rig, bloom, tone mapping
src/three/World.tsx   the diorama as one instanced mesh of boxes, plus two figures
src/three/Memory.tsx  a procedural room as points that turn from a photo into depth
src/sections/*.tsx    the page sections over the canvas
```

Scroll is the only source of time: the replay's play button, the timeline and "Show in the day"
all move the scroll position. Screenshots and the demo video are imported from `docs/media/`, so
`npm run media` at the repository root updates the page too. All data shown is the fictional
sample data; the memory room is procedural and labelled illustrative.
