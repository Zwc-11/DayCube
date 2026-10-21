# Optional Modal jobs

`jobs.py` defines word-timed transcription (faster-whisper) and normalized relative depth
(Depth Anything V2 Small). They are **not deployed, GPU-tested or wired into `/ingest`**.
The image installs GPU dependencies on Modal, not on the Windows laptop. A Modal account and
authentication are needed before deployment. A running job can incur charges.

```powershell
uv sync --extra modal
uv run --extra modal modal setup
uv run --extra modal modal deploy jobs/modal/jobs.py
```

Transcription currently has no diarization; labels must stay unknown until a speaker workflow
is added. Depth PNGs are relative inverse depth normalized to 0..65535; do not interpret them
as millimeters. Splat training, face blur, consent gates and 2.5D GLB export remain planned.
The capture/ingest UI does not invoke these functions.
