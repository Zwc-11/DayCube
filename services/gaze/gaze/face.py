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


@cache
def mirror_index() -> np.ndarray:
    """For each landmark, the landmark on the other side of the face (the canonical mesh is
    symmetric about x = 0), irises swapped: how MediaPipe names the points of a mirrored image."""
    mesh = canonical()[0]
    flipped = mesh * np.array([-1.0, 1.0, 1.0])
    face = np.array([int(np.argmin(np.linalg.norm(mesh - flipped[i], axis=1))) for i in range(468)])
    return np.r_[face, [LEFT_IRIS + k for k in range(5)], [RIGHT_IRIS + k for k in range(5)]]


def mirrored(points: np.ndarray, width: int) -> np.ndarray:
    """Landmarks as MediaPipe would give them for the horizontally flipped image."""
    out = np.asarray(points, dtype=float)[mirror_index()].copy()
    out[:, 0] = width - out[:, 0]
    return out


def eyetrax_features(points: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """EyeTrax 0.4's ``GazeEstimator.extract_features`` from pixel landmarks (it works in
    MediaPipe's normalised coordinates: x / width, y / height, z / width), so an EyeTrax model can
    be trained on landmarks recorded during calibration and then run by EyeTrax itself."""
    from eyetrax.constants import LEFT_EYE_INDICES, MUTUAL_INDICES, RIGHT_EYE_INDICES

    w, h = size
    all_points = (np.asarray(points) / np.array([w, h, w])).astype(np.float32)
    left_corner, right_corner, top = all_points[33], all_points[263], all_points[10]
    eye_center = (left_corner + right_corner) / 2.0
    shifted = all_points - eye_center
    x_axis = right_corner - left_corner
    x_axis /= np.linalg.norm(x_axis) + 1e-9
    y_approx = top - eye_center
    y_approx /= np.linalg.norm(y_approx) + 1e-9
    y_axis = y_approx - np.dot(y_approx, x_axis) * x_axis
    y_axis /= np.linalg.norm(y_axis) + 1e-9
    z_axis = np.cross(x_axis, y_axis)
    z_axis /= np.linalg.norm(z_axis) + 1e-9
    R = np.column_stack((x_axis, y_axis, z_axis))
    rotated = (R.T @ shifted.T).T
    dist = np.linalg.norm(R.T @ (right_corner - eye_center) - R.T @ (left_corner - eye_center))
    if dist > 1e-7:
        rotated /= dist
    features = rotated[LEFT_EYE_INDICES + RIGHT_EYE_INDICES + MUTUAL_INDICES].flatten()
    yaw = np.arctan2(R[1, 0], R[0, 0])
    pitch = np.arctan2(-R[2, 0], np.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))
    roll = np.arctan2(R[2, 1], R[2, 2])
    return np.concatenate([features, [yaw, pitch, roll]])


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

    def detect(self, image_bgr: np.ndarray, only: np.ndarray | None = None) -> Face | None:
        """The face's landmarks in pixels. ``only``: convert just these landmarks out of
        MediaPipe's result (the rest are nan); a tracking frame needs about 40 of the 478, and
        converting all of them is a quarter of a millisecond of Python per frame."""
        import cv2

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
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
        landmarks = result.face_landmarks[0]
        scale = np.array([width, height, width], dtype=float)
        if only is None:
            points = np.array([(p.x, p.y, p.z) for p in landmarks]) * scale
        else:
            points = np.full((len(landmarks), 3), np.nan)
            points[only] = np.array([(landmarks[i].x, landmarks[i].y, landmarks[i].z) for i in only]) * scale
        return Face(points, (width, height))

    def close(self) -> None:
        self._landmarker.close()
