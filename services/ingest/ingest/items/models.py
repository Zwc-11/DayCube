"""The open models the items pipeline uses, each as it is released: nothing is trained here.

| Role | Model | Licence |
| --- | --- | --- |
| Boxes and names, captions | Florence-2 base (``florence-community/Florence-2-base``) | MIT |
| The writing in a picture | RapidOCR (PP-OCRv4, Chinese and English) where installed, else Florence-2 | Apache-2.0 |
| Image and text embeddings | SigLIP 2 base (``google/siglip2-base-patch16-224``) | Apache-2.0 |
| Depth where no LiDAR is | Depth Anything V2 Metric Small, indoor and outdoor | Apache-2.0 |
| The camera's poses through a video's shot | Depth Anything 3 Small, in ``services/poses`` (``poses.py``) | Apache-2.0 |

They run on the CPU (seconds per frame) or a GPU; weights come from the Hugging Face hub on first
use (``HF_HOME``). Loaded once per process and only when called.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import numpy as np

from ..config import environment_python

DETECTOR = "florence-community/Florence-2-base"
EMBEDDER = "google/siglip2-base-patch16-224"
DEPTH = {"indoor": "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf",
         "outdoor": "depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf"}  # fmt: skip
SCENE_WORDS = frozenset(
    {"sky", "ground", "floor", "wall", "ceiling", "road", "grass", "forest", "field", "land", "water"}
)
POSES = "depth-anything/DA3-SMALL"  # named in poses.py, where it runs
POSES_TIMEOUT_S = 600  # for the model to finish and leave
READ_SURE = 0.8  # RapidOCR's confidence in a line, under which the line is left out
LARGEST = 0.6  # a box over this share of the frame is the scene, not a thing in it
SMALLEST = 0.0008


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in the picture given
    label: str
    score: float = 1.0


def device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


@cache
def _florence():
    import torch
    from transformers import AutoProcessor, Florence2ForConditionalGeneration

    model = Florence2ForConditionalGeneration.from_pretrained(DETECTOR, torch_dtype=torch.float32).to(device()).eval()
    return model, AutoProcessor.from_pretrained(DETECTOR)


def florence(image: np.ndarray, tasks: dict[str, int]) -> dict[str, Any]:
    """Florence-2 tasks on one RGB picture (``<OD>``, ``<DENSE_REGION_CAPTION>``, ``<CAPTION>``,
    ``<DETAILED_CAPTION>``, ``<OCR>``), each with its budget of new tokens: each task's parsed
    answer. The picture goes through the vision tower once, whatever the number of tasks."""
    import torch
    from PIL import Image

    model, processor = _florence()
    picture = Image.fromarray(image)
    seen, out = None, {}
    with torch.inference_mode():
        for task, tokens in tasks.items():
            inputs = processor(text=task, images=picture, return_tensors="pt").to(model.device)
            pixels = inputs.pop("pixel_values")
            seen = model.get_image_features(pixels) if seen is None else seen
            ids = model.generate(
                **inputs, mm_encoder_outputs={"image": seen}, max_new_tokens=tokens, num_beams=1, do_sample=False
            )
            text = processor.batch_decode(ids, skip_special_tokens=False)[0]
            out[task] = processor.post_process_generation(text, task=task, image_size=picture.size)[task]
    return out


@dataclass(frozen=True)
class Look:
    """One picture as Florence-2 reads it."""

    things: list[Detection]
    caption: str = ""
    text: str = ""  # the writing in the picture


def look(image: np.ndarray, caption: bool = True) -> Look:
    """The things in the picture (Florence-2's object detection, its own names) and, with
    ``caption``, a sentence about the picture and the writing in it."""
    written = read(image) if caption else ""
    tasks = {"<OD>": 512} | ({"<DETAILED_CAPTION>": 96} if caption else {}) | ({"<OCR>": 96} if written is None else {})
    answers = florence(image, tasks)
    found = answers["<OD>"]
    return Look(
        things(zip(found["bboxes"], found["labels"], strict=False), image.shape[1], image.shape[0]),
        str(answers.get("<DETAILED_CAPTION>", "")).strip(),
        str(answers.get("<OCR>", "")).strip() if written is None else written,
    )


@cache
def _reader():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def read(image: np.ndarray) -> str | None:
    """The writing in the picture, a line each, by RapidOCR; None where it is not installed.
    (Florence-2 reads Latin letters and makes up Chinese ones.)"""
    if find_spec("rapidocr_onnxruntime") is None:
        return None
    lines, _ = _reader()(image[:, :, ::-1])  # it takes BGR
    return "\n".join(text for _, text, sure in lines or [] if float(sure) >= READ_SURE)


def clean(label: str) -> str:
    return " ".join(label.lower().replace("_", " ").split())


def things(boxes: Iterable[tuple[Sequence[float], str]], W: int, H: int) -> list[Detection]:
    """Detections worth keeping: not the whole scene, not a speck, not a repeat of another box of
    the same name."""
    out: list[Detection] = []
    for box, label in boxes:
        x0, y0, x1, y1 = (float(v) for v in box)
        share = max(x1 - x0, 0) * max(y1 - y0, 0) / (W * H)
        name = clean(label)
        if not name or share > LARGEST or share < SMALLEST or name in SCENE_WORDS:
            continue
        if any(d.label == name and _iou(d.box, (x0, y0, x1, y1)) > 0.85 for d in out):
            continue
        out.append(Detection((x0, y0, x1, y1), name))
    return out


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


@cache
def _siglip():
    from transformers import AutoModel, AutoProcessor

    return AutoModel.from_pretrained(EMBEDDER).to(device()).eval(), AutoProcessor.from_pretrained(EMBEDDER)


def _unit(features: Any) -> np.ndarray:
    import torch

    if not isinstance(features, torch.Tensor):  # a model output object: its pooled vector
        features = features.pooler_output
    features = features / features.norm(dim=-1, keepdim=True)
    return features.float().cpu().numpy()


def embed_images(images: list[np.ndarray], batch: int = 16) -> np.ndarray:
    """(n, d) unit vectors of RGB pictures."""
    import torch
    from PIL import Image

    model, processor = _siglip()
    out = []
    for i in range(0, len(images), batch):
        inputs = processor(images=[Image.fromarray(im) for im in images[i : i + batch]], return_tensors="pt").to(
            model.device
        )
        with torch.inference_mode():
            out.append(_unit(model.get_image_features(**inputs)))
    return np.concatenate(out) if out else np.zeros((0, 768), np.float32)


def embed_texts(texts: list[str]) -> np.ndarray:
    import torch

    model, processor = _siglip()
    inputs = processor(text=texts, padding="max_length", max_length=64, truncation=True, return_tensors="pt").to(
        model.device
    )
    with torch.inference_mode():
        return _unit(model.get_text_features(**inputs))


def crop(image: np.ndarray, box: tuple[float, float, float, float], margin: float = 0.08) -> np.ndarray:
    """The box with a little around it, as the embedder and the item's own picture see it."""
    H, W = image.shape[:2]
    x0, y0, x1, y1 = box
    mx, my = margin * (x1 - x0), margin * (y1 - y0)
    a, b = int(max(x0 - mx, 0)), int(max(y0 - my, 0))
    c, d = int(min(x1 + mx, W)), int(min(y1 + my, H))
    return image[b : max(d, b + 1), a : max(c, a + 1)]


@cache
def _depth(place: str):
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    return AutoModelForDepthEstimation.from_pretrained(DEPTH[place]).to(
        device()
    ).eval(), AutoImageProcessor.from_pretrained(DEPTH[place])


def poses_python() -> Path | None:
    return environment_python("poses", windowless=False)


class Poses:
    """Depth Anything 3, a child process in its own environment (``services/poses``) that stays
    for the length of a video: called with a shot's pictures (N, H, W, 3), it returns what
    ``poses.py`` answers (``w2c``, ``K``, ``depth``)."""

    def __enter__(self) -> Poses:
        self.folder = tempfile.TemporaryDirectory()
        self.child = subprocess.Popen(
            [str(poses_python()), str(Path(__file__).with_name("poses.py")), self.folder.name], text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )  # fmt: skip
        return self

    def __call__(self, pictures: np.ndarray) -> dict[str, np.ndarray]:
        shot = Path(self.folder.name) / "shot.npy"
        np.save(shot, pictures)
        assert self.child.stdin is not None and self.child.stdout is not None
        self.child.stdin.write("shot\n")
        self.child.stdin.flush()
        if self.child.stdout.readline().strip() != "shot":
            raise RuntimeError("the pose model stopped: its own messages say why")
        with np.load(shot.with_suffix(".npz")) as answer:
            return dict(answer)

    def __exit__(self, *error: object) -> None:
        assert self.child.stdin is not None
        self.child.stdin.close()
        self.child.wait(timeout=POSES_TIMEOUT_S)
        self.folder.cleanup()


@cache
def _places() -> np.ndarray:
    return embed_texts(["a photo taken outdoors", "a photo taken indoors, inside a room"])


def outdoors(image: np.ndarray) -> bool:
    """Whether the picture is outdoors, by which of two sentences its embedding is nearer."""
    return bool((embed_images([image]) @ _places().T)[0].argmax() == 0)


def model_depth(image: np.ndarray) -> tuple[np.ndarray, str]:
    """Metric depth (metres, the picture's size) from the indoor or the outdoor model, and "model"."""
    import torch
    from PIL import Image

    model, processor = _depth("outdoor" if outdoors(image) else "indoor")
    inputs = processor(images=Image.fromarray(image), return_tensors="pt").to(model.device)
    with torch.inference_mode():
        depth = model(**inputs).predicted_depth
    depth = torch.nn.functional.interpolate(depth[None], size=image.shape[:2], mode="bilinear", align_corners=False)[
        0, 0
    ]
    return depth.float().cpu().numpy(), "model"
