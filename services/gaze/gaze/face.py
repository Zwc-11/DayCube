"""MediaPipe face landmarks for the head-aware gaze model.

One FaceLandmarker per process, the same model file EyeTrax uses. Each frame gives 478 landmarks
(468 face mesh points and 10 iris points) in pixels, and the canonical face mesh that comes with
the model gives the 3D shape they are fitted to, in centimetres, for solving the head pose.
"""

from __future__ import annotations

import struct
import time
import zipfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np

# Landmark indices (MediaPipe face mesh). "Right" and "left" are the person's.
RIGHT_IRIS = 468  # centre; 469-472 around it
LEFT_IRIS = 473  # centre; 474-477 around it
RIGHT_EYE = (33, 133, 159, 145)  # outer corner, inner corner, upper lid, lower lid
LEFT_EYE = (263, 362, 386, 374)
METADATA = "geometry_pipeline_metadata_landmarks.binarypb"


@dataclass(frozen=True)
class Face:
    points: np.ndarray  # (478, 3) float: x, y in image pixels; z in the same pixel scale
    size: tuple[int, int]  # image width, height


def model_path(path: str | Path | None = None) -> Path:
    """The FaceLandmarker task file EyeTrax caches (downloaded on first use)."""
    from eyetrax.gaze import _ensure_face_landmarker_task

    return _ensure_face_landmarker_task(path)


def _fields(data: bytes):
    """Protocol buffer wire format: yield (field number, wire type, value)."""
    i = 0
    while i < len(data):
        key, i = _varint(data, i)
        number, kind = key >> 3, key & 7
        if kind == 0:
            value, i = _varint(data, i)
        elif kind == 1:
            value, i = data[i : i + 8], i + 8
        elif kind == 2:
            length, i = _varint(data, i)
            value, i = data[i : i + length], i + length
        elif kind == 5:
            value, i = data[i : i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {kind}")
        yield number, kind, value


def _varint(data: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


@cache
def canonical(path: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The canonical face mesh (468, 3) in centimetres (x right, y up, z out of the face) and the
    weight MediaPipe gives each landmark when fitting the head pose (rigid parts weigh most).

    Read from the task file's geometry pipeline metadata, so no separate asset is shipped."""
    with zipfile.ZipFile(model_path(path)) as archive:
        data = archive.read(METADATA)
    vertices: list[float] = []
    weights = np.zeros(468)
    for number, kind, value in _fields(data):
        if number == 1 and kind == 2:  # canonical_mesh
            for field, wire, item in _fields(value):
                if field == 3 and wire == 2:  # packed vertex_buffer
                    vertices.extend(struct.unpack(f"<{len(item) // 4}f", item))
                elif field == 3 and wire == 5:
                    vertices.append(struct.unpack("<f", item)[0])
        elif number == 2 and kind == 2:  # procrustes_landmark_basis
            landmark, weight = 0, 0.0
            for field, wire, item in _fields(value):
                if field == 1:
                    landmark = item
                elif field == 2 and wire == 5:
                    weight = struct.unpack("<f", item)[0]
            weights[landmark] = weight
    mesh = np.array(vertices, dtype=float).reshape(-1, 5)[:, :3]  # x, y, z, u, v per vertex
    if mesh.shape != (468, 3):
        raise ValueError(f"unexpected canonical mesh shape {mesh.shape}")
    return mesh, weights


class Landmarker:
    """MediaPipe FaceLandmarker: image mode finds the face in each image on its own (what the
    head-aware model uses, see gaze.head.HeadEstimator); video mode follows it from frame to frame."""

    def __init__(self, video: bool = False, path: str | Path | None = None) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions

        self._mp, self.video, self._last_ms = mp, video, 0
        mode = vision.RunningMode.VIDEO if video else vision.RunningMode.IMAGE
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path(path))),
            running_mode=mode,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)

    def detect(self, image_bgr: np.ndarray) -> Face | None:
        import cv2

        rgb = np.ascontiguousarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        if self.video:
            ms = max(int(time.monotonic() * 1000), self._last_ms + 1)
            self._last_ms = ms
            result = self._landmarker.detect_for_video(image, ms)
        else:
            result = self._landmarker.detect(image)
        if not result.face_landmarks:
            return None
        height, width = image_bgr.shape[:2]
        points = np.array([(p.x * width, p.y * height, p.z * width) for p in result.face_landmarks[0]])
        return Face(points, (width, height))

    def close(self) -> None:
        self._landmarker.close()
