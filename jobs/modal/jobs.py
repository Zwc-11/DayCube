"""Optional cloud definitions. Importing does not deploy jobs or upload user data.

Run `uv run --extra modal modal deploy jobs/modal/jobs.py` only when ready to deploy.
These functions are not connected to the ingest endpoint in this starter.
"""
import modal

app = modal.App("daycube")
image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.11")
    .apt_install("ffmpeg")
    .pip_install("faster-whisper>=1.2,<2", "transformers>=4.49,<5", "torch>=2.6,<3",
                 "pillow>=11,<13", "numpy>=2,<3")
)


@app.function(image=image, gpu="A10G", timeout=900)
def transcribe(audio: bytes, language: str = "en") -> dict:
    """Whisper transcription with word timings. Diarization is not implemented here."""
    import tempfile
    from pathlib import Path

    from faster_whisper import WhisperModel

    if not audio or len(audio) > 50_000_000:
        raise ValueError("Audio must be non-empty and smaller than 50 MB")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "audio.wav"
        path.write_bytes(audio)
        model = WhisperModel("large-v3", device="cuda", compute_type="float16")
        segments, _ = model.transcribe(str(path), language=language, word_timestamps=True)
        return {"segments": [
            {"start": segment.start, "end": segment.end, "text": segment.text,
             "speaker": None, "words": [
                 {"start": word.start, "end": word.end, "word": word.word, "score": word.probability}
                 for word in segment.words or []]}
            for segment in segments], "speaker_map": {}}


@app.function(image=image, gpu="A10G", timeout=600)
def depth(frames: list[bytes]) -> list[bytes]:
    """16-bit normalized relative inverse depth, NOT millimeters or measured geometry."""
    import io

    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    if not frames or len(frames) > 16 or any(len(frame) > 10_000_000 for frame in frames):
        raise ValueError("Supply 1–16 frames, at most 10 MB each")
    name = "depth-anything/Depth-Anything-V2-Small-hf"
    processor = AutoImageProcessor.from_pretrained(name)
    model = AutoModelForDepthEstimation.from_pretrained(name).to("cuda").eval()
    result = []
    for frame in frames:
        image = Image.open(io.BytesIO(frame)).convert("RGB")
        inputs = processor(images=image, return_tensors="pt").to("cuda")
        with torch.no_grad():
            prediction = model(**inputs).predicted_depth
            prediction = torch.nn.functional.interpolate(prediction.unsqueeze(1),
                size=(image.height, image.width), mode="bicubic", align_corners=False).squeeze()
        values = prediction.cpu().numpy()
        normalized = (values - values.min()) / max(float(np.ptp(values)), 1e-8)
        buffer = io.BytesIO()
        Image.fromarray((normalized * 65535).astype(np.uint16)).save(buffer, format="PNG")
        result.append(buffer.getvalue())
    return result
