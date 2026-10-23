# Libraries and accounts

The default setup installs the backend and starter-viewer libraries into `.venv` and
`node_modules`. Exact resolved versions are committed in `uv.lock` and `package-lock.json`.
It does not install GPU model weights or create external accounts.

| Area | Libraries | State |
| --- | --- | --- |
| Local API | FastAPI, Uvicorn, Pydantic, pydantic-settings, HTTPX | Installed; API tested |
| Contracts | JSON Schema, jsonschema, generated Pydantic/TS, Ajv, Zod | Installed; cross-language checks pass |
| Search | Python sqlite3 + FTS5, NumPy | One vault database across days ([search.md](search.md)) |
| Viewer | Preserved React 18, Vite 6, Three, R3F, drei | Separate frontend prototype |
| Development | uv, pytest, Ruff, datamodel-code-generator, Playwright | Installed; Chromium smoke tests supported |
| AI adapter | Anthropic SDK | Optional `ai` extra; requires API key and model id |
| Semantic search | FastEmbed (optional `vector` extra), NumPy | bge-small embeddings stored as int8 in the vault database; searched in two passes in NumPy ([search.md](search.md)) |
| Skeleton inference | NumPy, scikit-learn | Optional `skeleton` extra; GPX parsing already uses stdlib |
| Local scene helpers | Pillow, trimesh, NumPy | Optional `moments` extra; GLB generation still planned |
| Cloud jobs | Modal SDK | Optional `modal` extra; separate account; no jobs deployed |
| Browser receiver | Shared FastAPI/Pydantic libraries | Separate capture environment installed; not started |
| Native capture | pywin32, psutil, mss, Pillow, pystray, pynput | Separate capture project, optional `windows` extra |
| OCR | RapidOCR ONNX, RapidFuzz | Separate capture project, optional `ocr` extra |
| Gaze | MediaPipe, OpenCV, scikit-learn | Separate capture project, optional `gaze` extra |

Install optional backend groups only when working on those integrations:

```powershell
uv sync --extra ai --extra modal
uv sync --extra vector
uv sync --extra skeleton --extra moments
```

Pass the same `--extra` flags to `uv run` commands that need them. Optional group resolution is
locked; the GPU/native execution paths have not been exercised in this development pass.
See `services/capture-win/README.md` for separate capture setup.

FFmpeg/ffprobe are external binaries, not Python packages. They are not currently on the system
PATH. Install an FFmpeg distribution and add its bin folder before exercising media helpers.
Playwright's bundled FFmpeg is for its own browser tooling and does not supply `ffprobe`.

No external credentials are needed for the sample demo. Anthropic needs `ANTHROPIC_API_KEY` and
`DAYCUBE_ANTHROPIC_MODEL` in your local environment or `.env`. Modal uses its own authenticated
CLI setup. Hugging Face tokens are reserved for a future consented diarization pipeline.
Overpass is a keyless lookup but sends coordinates to the configured endpoint when called.

The frontend dependency manifest is maintained separately. The current synchronized npm lockfile
reports zero audit vulnerabilities. The Python API and tests do not use the frontend build-tool
dependency chain.

Primary implementation references: [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/),
[uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/),
[Claude Python API](https://platform.claude.com/docs/en/api/python),
[Modal app definitions](https://modal.com/docs/guide/apps),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[Depth Anything V2 Small model](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf).
