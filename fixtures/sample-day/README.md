# Fictional sample day

All locations, people, activity, quotes, thumbnails and geometry are synthetic illustrations.
There are 5 visits, 60 fixes, 14 events, 2 OCR records with 6 blocks, a graph and one moment
with a 20-second synthetic transcript. No source video, audio, face images or GLB are included.
`media_available: false` and `scene.kind: unavailable` make that boundary explicit.

Use `uv run daycube seed` to copy this fixture into a new local day; seeding never replaces an
existing day. Build `index.sqlite` from files instead of checking a binary index into source.
`scripts/build-fixture.py` reproducibly regenerates this fictional source fixture.
