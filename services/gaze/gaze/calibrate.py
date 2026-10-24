"""Calibrate the webcam gaze model, and choose which model to run.

One session, about 45 seconds, in a full-screen window:

1. nine dots to learn from (a 3x3 grid), head still;
2. four held-out dots between them, head still: the error with the head where it was;
3. a prompt to move the head (lean back, or a hand's width to one side) and keep it there, then
   the four held-out dots again: the error once the head has moved, which is what a calibration
   meets an hour later.

Every frame goes through MediaPipe twice, in image mode (the face found afresh each frame) and in
video mode (followed from frame to frame, as EyeTrax ran it), and both sets of landmarks are kept.
Candidate models are fitted on step 1 and scored on steps 2 and 3, on the same frames:

- ``head``: the head-aware model (``gaze.head``) on image-mode landmarks;
- ``head-video``: the same on video-mode landmarks;
- ``ridge``: EyeTrax's ridge regression on video-mode landmarks (the tracker's model until now).

A candidate's error is the median distance from its gaze point to the dot after the tracker's own
One Euro smoother (what the fixation detector sees), over each held-out dot's frames. The one with
the lowest mean of the still and moved errors is retrained on every frame and saved. A forced
``model`` skips the choice.

Written to ``vault/calibration/``: the model (``gaze-head.json``, or EyeTrax's ``gaze-model.pkl``),
``gaze.json`` (monitor, camera, model, errors, every candidate's scores) and ``gaze-frames.npz``
(the landmarks and targets of every frame, numbers only, for checking a calibration later; no
images). The still-head error decides whether tracking runs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

MODEL_FILE = "gaze-model.pkl"  # EyeTrax regressions
HEAD_MODEL_FILE = "gaze-head.json"
FRAMES_FILE = "gaze-frames.npz"
CALIBRATION_FILE = "gaze.json"
AUTO = "auto"
CANDIDATES = ("head", "head-video", "ridge")
LABELS = {
    "head": "head-aware, image landmarks",
    "head-video": "head-aware, video landmarks",
    "ridge": "EyeTrax ridge (old)",
}
# Held-out targets sit between the training grid's points, where interpolation is hardest.
HELD_OUT = [(0.3, 0.3), (0.7, 0.3), (0.3, 0.7), (0.7, 0.7)]
TRAIN_ORDER = [(1, 1), (0, 0), (2, 0), (0, 2), (2, 2), (1, 0), (0, 1), (2, 1), (1, 2)]
# Median held-out error above this is too coarse to tell paragraphs apart at 100% zoom. 150 px let
# through a 183 px calibration on a 2560 px monitor (137 px at 1920) that could not single out
# most paragraphs; the recalibration that followed measured 69 px (eval/gaze_sim.py, docs/capture.md).
USABLE_PX_AT_1920 = 120
TRAIN, STILL, MOVED = 0, 1, 2
PULSE_S = 1.0  # the dot pulses while the eyes get there; frames are not used
CAPTURE_S = 1.0
MOVE_S = 5.0  # time to move the head before the moved-head dots
WINDOW = "Calibration"


class Aborted(Exception):
    pass


def usable_limit(width: int) -> float:
    return USABLE_PX_AT_1920 * width / 1920


def model_file(model: str) -> str:
    return HEAD_MODEL_FILE if model.startswith("head") else MODEL_FILE


def estimator_for(model: str, monitor: tuple[int, int], mm_per_px: float | None, landmarker: object = None) -> object:
    """A gaze estimator with EyeTrax's interface: the head-aware model or an EyeTrax regression."""
    if model.startswith("head"):
        from .head import HeadEstimator
        from .system import DEFAULT_MM_PER_PX

        return HeadEstimator(
            monitor, mm_per_px or DEFAULT_MM_PER_PX, landmarker=landmarker, video=model == "head-video"
        )
    from eyetrax import GazeEstimator

    return GazeEstimator(model_name=model)


@dataclass
class Frame:
    t: float
    phase: int  # TRAIN, STILL or MOVED
    target: tuple[int, int]  # monitor pixels
    size: tuple[int, int]  # camera image
    image: np.ndarray | None  # (478, 3) image-mode landmarks, or None without a face
    video: np.ndarray | None  # (478, 3) video-mode landmarks


# -- the session ---------------------------------------------------------------------------------


class Session:
    """The camera, both landmarkers and the full-screen window."""

    def __init__(self, capture: object, width: int, height: int) -> None:
        from .face import Landmarker

        self.capture, self.width, self.height = capture, width, height
        self.image, self.video = Landmarker(video=False), Landmarker(video=True)
        self.frames: list[Frame] = []

    def close(self) -> None:
        self.image.close()
        self.video.close()

    def _show(self, canvas: np.ndarray) -> None:
        import cv2

        cv2.imshow(WINDOW, canvas)
        if cv2.waitKey(1) == 27:
            raise Aborted

    def _canvas(self) -> np.ndarray:
        return np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def _text(self, canvas: np.ndarray, lines: list[str], scale: float = 1.2) -> None:
        import cv2

        y = self.height // 2 - 40 * len(lines) // 2
        for line in lines:
            (w, h), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
            cv2.putText(canvas, line, ((self.width - w) // 2, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2)
            y += h + 24

    def _frame(self, record: tuple[int, tuple[int, int]] | None = None) -> bool | None:
        """Read a frame and run the video landmarker (it must see every frame to follow the face);
        when recording, also the image landmarker, and keep both. Whether a face was found."""
        ok, frame = self.capture.read()
        if not ok:
            return None
        video = self.video.detect(frame)
        if record is None:
            return video is not None
        image = self.image.detect(frame)
        height, width = frame.shape[:2]
        phase, target = record
        self.frames.append(
            Frame(
                time.time(),
                phase,
                target,
                (width, height),
                None if image is None else image.points,
                None if video is None else video.points,
            )
        )
        return image is not None

    def wait_for_face(self, seconds: float = 2.0) -> None:
        import cv2

        since = None
        while True:
            face = self._frame()
            if face is None:
                continue
            canvas = self._canvas()
            now = time.monotonic()
            if face:
                since = since or now
                if now - since >= seconds:
                    return
                share = (now - since) / seconds
                cv2.ellipse(
                    canvas,
                    (self.width // 2, self.height // 2),
                    (50, 50),
                    0,
                    -90,
                    -90 + 360 * (1 - share),
                    (0, 255, 0),
                    -1,
                )
            else:
                since = None
                self._text(canvas, ["Face not found: face the screen, in even light"])
            self._show(canvas)

    def message(self, lines: list[str], seconds: float) -> None:
        import cv2

        start = time.monotonic()
        while (left := seconds - (time.monotonic() - start)) > 0:
            self._frame()
            canvas = self._canvas()
            self._text(canvas, lines)
            cv2.putText(
                canvas,
                f"{left:.0f}",
                (self.width // 2 - 15, self.height - 120),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.5,
                (0, 255, 0),
                3,
            )
            self._show(canvas)

    def dots(self, targets: list[tuple[int, int]], phase: int) -> None:
        import cv2

        for x, y in targets:
            start = time.monotonic()
            while (elapsed := time.monotonic() - start) < PULSE_S + CAPTURE_S:
                capturing = elapsed >= PULSE_S
                self._frame((phase, (x, y)) if capturing else None)
                canvas = self._canvas()
                radius = 20 if capturing else 15 + int(15 * abs(np.sin(2 * np.pi * elapsed)))
                cv2.circle(canvas, (x, y), radius, (0, 255, 0), -1)
                if capturing:
                    share = (elapsed - PULSE_S) / CAPTURE_S
                    cv2.ellipse(canvas, (x, y), (40, 40), 0, -90, -90 + 360 * (1 - share), (255, 255, 255), 4)
                self._show(canvas)


def grid_points(width: int, height: int) -> list[tuple[int, int]]:
    from eyetrax.calibration.common import compute_grid_points

    return compute_grid_points(TRAIN_ORDER, width, height)


def held_out_points(width: int, height: int) -> list[tuple[int, int]]:
    return [(int(width * x), int(height * y)) for x, y in HELD_OUT]


def calibrate(vault: Path, camera: int = 0, model: str = AUTO) -> dict:
    import cv2
    from eyetrax.utils.video import open_camera

    from .system import monitor_mm_per_px, primary_monitor

    width, height = primary_monitor()
    pitch = monitor_mm_per_px()
    capture = open_camera(camera)
    session = Session(capture, width, height)
    cv2.namedWindow(WINDOW, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    try:
        session.wait_for_face()
        session.dots(grid_points(width, height), TRAIN)
        session.dots(held_out_points(width, height), STILL)
        session.message(
            [
                "Now move your head and keep it there:",
                "lean back about 10 cm, or move it a hand's width to one side.",
                "Then follow the dots with your eyes again.",
            ],
            MOVE_S,
        )
        session.dots(held_out_points(width, height), MOVED)
    finally:
        capture.release()
        session.close()
        cv2.destroyAllWindows()
    return choose(session.frames, vault, (width, height), camera, pitch, model)


# -- choosing a model ----------------------------------------------------------------------------


def _blinks(frames: list[Frame], stream: str) -> np.ndarray:
    from .head import Blinks

    blinks, out = Blinks(), np.zeros(len(frames), dtype=bool)
    for i, f in enumerate(frames):
        points = getattr(f, stream)
        out[i] = points is not None and blinks(points)
    return out


def _rows(
    name: str, frames: list[Frame], monitor: tuple[int, int], pitch: float, mirror: bool
) -> tuple[object, np.ndarray, np.ndarray]:
    """A fresh estimator for a candidate, its feature rows, and which frames they come from."""
    from .face import Face, eyetrax_features, mirrored

    stream = "image" if name == "head" else "video"
    usable = np.array([getattr(f, stream) is not None for f in frames]) & ~_blinks(frames, stream)
    index = np.flatnonzero(usable)
    if not name.startswith("head"):  # an EyeTrax model: ridge, elastic_net, svr, tiny_mlp
        from eyetrax.models import create_model

        rows = [eyetrax_features(getattr(frames[i], stream), frames[i].size) for i in index]
        return _EyeTrax(create_model(name)), np.array(rows), index
    from .head import HeadEstimator, pack

    estimator = HeadEstimator(monitor, pitch, landmarker=_NoCamera(), mirror=mirror, video=name == "head-video")
    rows = []
    for i in index:
        points = getattr(frames[i], stream)
        if mirror:
            points = mirrored(points, frames[i].size[0])
        rows.append(pack(estimator.measure(Face(points, frames[i].size))))
    return estimator, np.array(rows), index


class _NoCamera:
    """Calibration measures recorded landmarks: the estimator never sees a frame here."""

    def detect(self, frame: object) -> None:
        raise RuntimeError("no camera during model fitting")

    def close(self) -> None:
        pass


class _EyeTrax:
    """An EyeTrax model, saved where and how EyeTrax's GazeEstimator loads it."""

    def __init__(self, model: object) -> None:
        self.model = model

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        self.model.train(X, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(X)

    def save_model(self, path: Path) -> None:
        self.model.save(path)


def errors(predicted: np.ndarray, frames: list[Frame]) -> tuple[np.ndarray, np.ndarray]:
    """Per frame, the distance to the target raw and after the tracker's One Euro smoother, run
    afresh over each dot's frames in time order (as after the saccade that brought the eyes)."""
    from .fixations import Smoother

    raw = np.linalg.norm(predicted - np.array([f.target for f in frames], dtype=float), axis=1)
    smoothed = np.full(len(frames), np.nan)
    smoother, key = Smoother(), None
    for i, f in enumerate(frames):
        if (f.phase, f.target) != key:
            smoother, key = Smoother(), (f.phase, f.target)
        if np.isfinite(predicted[i]).all():
            x, y = smoother(f.t, *predicted[i])
            smoothed[i] = np.hypot(x - f.target[0], y - f.target[1])
    return raw, smoothed


def _summary(values: np.ndarray) -> float | None:
    values = values[np.isfinite(values)]
    return round(float(np.median(values)), 1) if len(values) else None


def choose(
    frames: list[Frame],
    vault: Path,
    monitor: tuple[int, int],
    camera: int,
    pitch: tuple[float, str],
    model: str = AUTO,
) -> dict:
    """Fit the candidates on the training dots, score them on the held-out ones, keep the best."""
    phases = np.array([f.phase for f in frames])
    if (phases == TRAIN).sum() < 30 or (phases == STILL).sum() < 8:
        raise RuntimeError("Too few frames with a face. Sit facing the camera in even light and try again.")
    from .head import HeadEstimator, unpack

    # A mirrored image (some drivers and virtual cameras) makes the eyes seem to turn the wrong way.
    estimator, X, index = _rows("head", frames, monitor, pitch[0], mirror=False)
    train = phases[index] == TRAIN
    targets = np.array([f.target for f in frames], dtype=float)
    mirror = len(index) >= 30 and HeadEstimator.mirrored(X[train], targets[index][train])

    names = CANDIDATES if model == AUTO else (model,)
    scores: dict[str, dict] = {}
    fitted: dict[str, tuple[object, np.ndarray, np.ndarray]] = {}
    for name in names:
        estimator, X, index = _rows(name, frames, monitor, pitch[0], mirror and name.startswith("head"))
        train = phases[index] == TRAIN
        if train.sum() < 30:
            continue
        estimator.train(X[train], targets[index][train])
        predicted = np.full((len(frames), 2), np.nan)
        predicted[index] = estimator.predict(X)
        raw, smoothed = errors(predicted, frames)
        row = {"frames": int(len(index))}
        for label, phase in (("still", STILL), ("moved", MOVED)):
            sel = phases == phase
            row[f"{label}_px"] = _summary(smoothed[sel])
            row[f"{label}_raw_px"] = _summary(raw[sel])
            row[f"{label}_p90_px"] = (
                round(float(np.nanpercentile(smoothed[sel], 90)), 1) if np.isfinite(smoothed[sel]).any() else None
            )
        scored = [row[k] for k in ("still_px", "moved_px") if row[k] is not None]
        row["score_px"] = round(float(np.mean(scored)), 1) if scored else None
        scores[name] = row
        fitted[name] = (estimator, X, index)
    usable_scores = {k: v for k, v in scores.items() if v["score_px"] is not None and v["still_px"] is not None}
    if not usable_scores:
        raise RuntimeError("Too few frames with a face. Sit facing the camera in even light and try again.")
    chosen = min(usable_scores, key=lambda k: usable_scores[k]["score_px"])

    # How far the head went for the moved-head dots, from the head-aware model's pose.
    heads = unpack(fitted["head"][1]) if "head" in fitted else []
    moved_by = None
    if heads:
        head_phase = phases[fitted["head"][2]]
        still_t = [h.t for h, p in zip(heads, head_phase, strict=True) if p == STILL]
        moved_t = [h.t for h, p in zip(heads, head_phase, strict=True) if p == MOVED]
        if still_t and moved_t:
            moved_by = round(float(np.linalg.norm(np.median(moved_t, 0) - np.median(still_t, 0))), 0)

    estimator, X, index = fitted[chosen]
    estimator.train(X, targets[index])  # the saved model learns from every dot
    folder = vault / "calibration"
    folder.mkdir(parents=True, exist_ok=True)
    estimator.save_model(folder / model_file(chosen))
    _save_frames(folder / FRAMES_FILE, frames)
    best = scores[chosen]
    width = monitor[0]
    result = {
        "t": datetime.now(UTC).isoformat(),
        "monitor": list(monitor),
        "camera": camera,
        "camera_size": list(frames[0].size),
        "model": chosen,
        "model_file": model_file(chosen),
        # Errors after the tracker's smoother, which is what fixations are made of.
        "error_px": best["still_px"],
        "error_p90_px": best["still_p90_px"],
        "error_raw_px": best["still_raw_px"],
        "moved_error_px": best["moved_px"],
        "head_moved_mm": moved_by,
        "usable": best["still_px"] <= usable_limit(width),
        "candidates": scores,
        "frames": {k: int((phases == v).sum()) for k, v in (("train", TRAIN), ("still", STILL), ("moved", MOVED))},
        "mm_per_px": round(pitch[0], 4),
        "mm_per_px_source": pitch[1],
    }
    if mirror and chosen.startswith("head"):
        result["mirrored_camera"] = True
    (folder / CALIBRATION_FILE).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _save_frames(path: Path, frames: list[Frame]) -> None:
    empty = np.full((478, 3), np.nan, dtype=np.float32)
    np.savez_compressed(
        path,
        t=np.array([f.t for f in frames]),
        phase=np.array([f.phase for f in frames], dtype=np.int8),
        target=np.array([f.target for f in frames], dtype=np.int32),
        size=np.array([f.size for f in frames], dtype=np.int32),
        image=np.array([empty if f.image is None else f.image.astype(np.float32) for f in frames]),
        video=np.array([empty if f.video is None else f.video.astype(np.float32) for f in frames]),
    )


def load_frames(path: Path) -> list[Frame]:
    """The frames of a saved calibration, to refit or compare models later."""
    d = np.load(path)

    def points(a: np.ndarray) -> np.ndarray | None:
        return None if np.isnan(a[0, 0]) else a.astype(float)

    return [
        Frame(float(t), int(p), (int(x), int(y)), (int(w), int(h)), points(image), points(video))
        for t, p, (x, y), (w, h), image, video in zip(
            d["t"], d["phase"], d["target"], d["size"], d["image"], d["video"], strict=True
        )
    ]


def load(vault: Path) -> dict | None:
    path = vault / "calibration" / CALIBRATION_FILE
    if not path.is_file():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    if not (vault / "calibration" / result.get("model_file", MODEL_FILE)).is_file():
        return None
    return result
