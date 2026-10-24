"""Head-aware gaze: where the eyes point, from the head's pose and the eyes' turn within the head.

EyeTrax maps eye landmarks straight to screen pixels with a regression learned while the head held
still. That map is only right for the head position it was learned at: move the head 5 cm or turn
it 10 degrees and the same eye picture means a different screen point, which the regression cannot
know. Here the regression only learns what it can learn from a still head, the eye's direction
within the head; the head's pose comes from the face each frame and the rest is geometry:

1. Head pose: the face landmarks fitted to MediaPipe's canonical face (perspective-n-point), so
   the head's rotation R and position t in camera coordinates (millimetres).
2. Eye-in-head direction: where the irises sit relative to the eye corners and lids, measured in
   the head's own frame (each landmark's camera ray met with the plane of the eye corners), then a
   ridge regression from those measurements to the gaze direction in head coordinates.
3. Gaze ray: from the eyes (R, t applied to the canonical eye position) along R times that
   direction, met with the screen's plane, gives the screen point.

Calibration inverts step 3: for each target, the head pose at that frame gives the direction the
eyes must have had in the head to look at it, and the regression learns those directions.

Coordinates: camera frame x right in the image, y down, z away from the camera; head frame the
same axes for a face looking straight into the camera, origin at the canonical face's origin.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np

from .face import LEFT_EYE, LEFT_IRIS, RIGHT_EYE, RIGHT_IRIS, Face, canonical

DEFAULT_HFOV_DEG = 65.0  # a laptop or USB webcam's horizontal field of view: 60-78 degrees
CAMERA_ABOVE_MM = 10.0
EYEBALL_MM = 12.0  # an adult eyeball's radius
# How far the eyeball's centre sits behind the eye corners. 8-12 mm fit MPIIFaceGaze about equally
# (eval/head_bench.py); 10 was best, 4 and 16 clearly worse.
EYEBALL_DEPTH_MM = 10.0  # from the camera down to the screen's top pixel row, for a webcam on the bezel


def _head_frame(points_cm: np.ndarray) -> np.ndarray:
    """Canonical (x right, y up, z out of the face, cm) -> head frame (x right, y down, z into the
    face, mm)."""
    return points_cm * np.array([10.0, -10.0, -10.0])


@dataclass(frozen=True)
class Camera:
    f: float  # focal length, pixels
    cx: float
    cy: float

    @classmethod
    def nominal(cls, width: int, height: int, hfov_deg: float = DEFAULT_HFOV_DEG) -> Camera:
        return cls(width / 2 / math.tan(math.radians(hfov_deg) / 2), width / 2, height / 2)

    @property
    def matrix(self) -> np.ndarray:
        return np.array([[self.f, 0, self.cx], [0, self.f, self.cy], [0, 0, 1.0]])

    def rays(self, pixels: np.ndarray) -> np.ndarray:
        """Unit-z rays (x/z, y/z, 1) through image points."""
        pixels = np.asarray(pixels, dtype=float)
        return np.stack(
            [(pixels[..., 0] - self.cx) / self.f, (pixels[..., 1] - self.cy) / self.f, np.ones(pixels.shape[:-1])], -1
        )


@dataclass(frozen=True)
class Screen:
    """The screen's pixel grid in camera coordinates: pixel (u, v) is at origin + s·(u·ex + v·ey)."""

    origin: np.ndarray  # the top-left pixel, mm
    ex: np.ndarray  # unit vector along a pixel row (to the user's right: the camera's -x)
    ey: np.ndarray  # unit vector down a pixel column
    mm_per_px: float

    @classmethod
    def below_camera(
        cls,
        size_px: tuple[int, int],
        width_mm: float,
        *,
        right_mm: float = 0.0,
        below_mm: float = CAMERA_ABOVE_MM,
        tilt_deg: float = 0.0,
    ) -> Screen:
        """A screen centred under the camera, its top row ``below_mm`` below it, in the camera's
        plane (tilted back by ``tilt_deg``); ``right_mm`` moves the screen to the user's right."""
        s = width_mm / size_px[0]
        tilt = math.radians(tilt_deg)
        ex = np.array([-1.0, 0.0, 0.0])
        ey = np.array([0.0, math.cos(tilt), math.sin(tilt)])
        origin = np.array([width_mm / 2 - right_mm, 0.0, 0.0]) + below_mm * ey
        return cls(origin, ex, ey, s)

    @cached_property
    def normal(self) -> np.ndarray:
        return np.cross(self.ex, self.ey)

    def point(self, pixels: np.ndarray) -> np.ndarray:
        pixels = np.asarray(pixels, dtype=float)
        return self.origin + self.mm_per_px * (pixels[..., :1] * self.ex + pixels[..., 1:2] * self.ey)

    def pixel(self, origins: np.ndarray, directions: np.ndarray) -> np.ndarray:
        """Where rays meet the screen's plane, in pixels (nan for rays that never do)."""
        n = self.normal
        along = directions @ n
        with np.errstate(divide="ignore", invalid="ignore"):
            k = ((self.origin - origins) @ n) / along
        k = np.where(k > 0, k, np.nan)
        hit = origins + k[..., None] * directions - self.origin
        return np.stack([hit @ self.ex, hit @ self.ey], -1) / self.mm_per_px


@dataclass(frozen=True)
class Head:
    """One frame's head pose and eye measurements, the person's right eye first."""

    R: np.ndarray  # head -> camera rotation
    t: np.ndarray  # head origin in camera coordinates, mm
    eyes: np.ndarray  # (2, 3) the eyeballs' centres, camera mm
    features: np.ndarray  # (2, k) each eye's measurements in the head frame (see eye_features)


class Poser:
    """Head pose and eye-in-head features for each frame of one camera."""

    def __init__(
        self, camera: Camera, eyeball_mm: float = EYEBALL_MM, eyeball_depth_mm: float = EYEBALL_DEPTH_MM
    ) -> None:
        mesh, weights = canonical()
        self.camera, self.eyeball_mm = camera, eyeball_mm
        self.model = _head_frame(mesh)
        self.rigid = np.flatnonzero(weights > 0)
        # The eyeball centre sits behind the eye corners; the gaze ray starts there.
        right = self.model[list(RIGHT_EYE[:2])].mean(0)
        left = self.model[list(LEFT_EYE[:2])].mean(0)
        self.eye_centres = np.array([right, left]) + np.array([0.0, 0.0, eyeball_depth_mm])
        self._rigid_model = np.ascontiguousarray(self.model[self.rigid], dtype=np.float64)
        self._matrix = camera.matrix
        self._irises = np.array([RIGHT_IRIS, LEFT_IRIS])
        self._eyes = np.array([RIGHT_EYE, LEFT_EYE])  # (2, 4): outer and inner corner, upper and lower lid
        self._eye_depths = self.model[self._eyes, 2]
        # The landmarks a tracking frame needs: the rest need not leave MediaPipe.
        self.needed = np.unique(np.r_[self.rigid, self._irises, self._eyes.ravel()])

    def pose(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Head rotation and position: the rigid landmarks (those MediaPipe weighs for its own pose)
        fitted to the canonical face by perspective-n-point."""
        import cv2

        image = np.ascontiguousarray(points[self.rigid, :2], dtype=np.float64)
        _, rvec, tvec = cv2.solvePnP(self._rigid_model, image, self._matrix, None, flags=cv2.SOLVEPNP_SQPNP)
        R, _ = cv2.Rodrigues(rvec)
        return R, tvec.ravel()

    def on_planes(self, pixels: np.ndarray, R: np.ndarray, t: np.ndarray, depths: np.ndarray) -> np.ndarray:
        """Image points carried along their camera rays onto head-frame planes z = depth (planes
        facing out of the face), returned in head coordinates. A landmark met at its own
        canonical depth lands where it is in the head however the head is turned."""
        rays = self.camera.rays(pixels)  # camera frame, z = 1
        normal = R[:, 2]  # the head's z axis in camera coordinates
        k = (t @ normal + depths) / (rays @ normal)
        return (rays * k[:, None] - t) @ R  # R.T @ (hit - t) for each row

    def on_eyeball(self, pixel: np.ndarray, R: np.ndarray, t: np.ndarray, centre: np.ndarray) -> np.ndarray:
        """The unit direction, in head coordinates, from an eyeball's centre to where the iris
        centre's camera ray meets the eyeball's front (or comes closest to it)."""
        ray = self.camera.rays(pixel)
        ray /= np.linalg.norm(ray)
        c = R @ centre + t
        along = ray @ c
        k = along - math.sqrt(max(along**2 - c @ c + self.eyeball_mm**2, 0.0))
        g = R.T @ (ray * k - c)
        return g / np.linalg.norm(g)

    def measure(self, face: Face) -> Head:
        points = face.points
        R, t = self.pose(points)
        return Head(R, t, self.eye_centres @ R.T + t, self.eye_features(points, R, t))

    def eye_features(self, points: np.ndarray, R: np.ndarray, t: np.ndarray) -> np.ndarray:
        """For each eye: the direction from the eyeball's centre to the iris (x, y components, head
        frame), and the upper and lower lids' heights relative to the eye corners (head frame, mm;
        the lids follow the eye up and down and so help with the vertical, which the iris alone
        shows least). Both eyes at once: ``on_eyeball`` and ``on_planes``, vectorised."""
        cam = self.camera
        # Irises: each camera ray met with its eyeball (or the point where it comes closest).
        rays = np.ones((2, 3))
        rays[:, 0] = (points[self._irises, 0] - cam.cx) / cam.f
        rays[:, 1] = (points[self._irises, 1] - cam.cy) / cam.f
        rays /= np.sqrt(np.einsum("ij,ij->i", rays, rays))[:, None]
        c = self.eye_centres @ R.T + t
        along = np.einsum("ij,ij->i", rays, c)
        k = along - np.sqrt(np.maximum(along * along - np.einsum("ij,ij->i", c, c) + self.eyeball_mm**2, 0.0))
        g = (rays * k[:, None] - c) @ R  # R.T @ v for each row
        g /= np.sqrt(np.einsum("ij,ij->i", g, g))[:, None]
        # Corners and lids: each carried to its own canonical depth in the head.
        lids = np.ones((2, 4, 3))
        lids[..., 0] = (points[self._eyes, 0] - cam.cx) / cam.f
        lids[..., 1] = (points[self._eyes, 1] - cam.cy) / cam.f
        normal = R[:, 2]
        k = (t @ normal + self._eye_depths) / (lids @ normal)
        y = ((lids * k[..., None] - t) @ R)[..., 1]
        middle = (y[:, 0] + y[:, 1]) / 2
        return np.stack([g[:, 0], g[:, 1], y[:, 2] - middle, y[:, 3] - middle], 1)


def directions(xy: np.ndarray) -> np.ndarray:
    """Unit gaze directions in head coordinates from their x and y components; looking out of the
    face is -z. An eyeball turned to direction g puts its iris r·(gx, gy) from where it sits
    looking straight ahead, so the eye measurements are close to linear in (gx, gy)."""
    xy = np.asarray(xy, dtype=float)
    norm = np.linalg.norm(xy, axis=-1, keepdims=True)
    xy = np.where(norm > 0.95, xy * 0.95 / np.maximum(norm, 1e-12), xy)
    z = -np.sqrt(1 - (xy**2).sum(-1, keepdims=True))
    return np.concatenate([xy, z], -1)


@dataclass
class GazeModel:
    """Each eye's direction in the head from its measurements (a ridge regression per eye), then
    each eye's ray to the screen; the screen point is the average of the two eyes' points.
    ``fit`` learns from frames with known screen targets, ``predict`` gives screen pixels."""

    screen: Screen
    alphas: tuple[float, ...] = tuple(np.logspace(-4, 2, 13))
    eyes: list = field(default_factory=list)  # per eye: (mean, sd, weights)
    alpha: list = field(default_factory=list)

    def required(self, heads: list[Head], targets_px: np.ndarray) -> np.ndarray:
        """(n, 2, 2): the direction (x, y components, head frame) each eye needed to look at each
        frame's target."""
        R = np.array([h.R for h in heads])
        aim = self.screen.point(targets_px)[:, None, :] - np.array([h.eyes for h in heads])
        aim /= np.linalg.norm(aim, axis=-1, keepdims=True)
        return np.einsum("nji,nej->nei", R, aim)[..., :2]  # R.T @ aim per eye

    def fit(self, heads: list[Head], targets_px: np.ndarray) -> GazeModel:
        from sklearn.linear_model import RidgeCV

        X = np.array([h.features for h in heads])
        y = self.required(heads, targets_px)
        self.eyes, self.alpha = [], []
        for e in range(2):
            mean, sd = X[:, e].mean(0), X[:, e].std(0) + 1e-9
            ridge = RidgeCV(alphas=self.alphas).fit((X[:, e] - mean) / sd, y[:, e])
            self.eyes.append((mean, sd, np.r_[ridge.intercept_[None, :], ridge.coef_.T]))
            self.alpha.append(float(ridge.alpha_))
        return self

    def _stacked(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Both eyes' (mean, sd, weights) as arrays, rebuilt when ``eyes`` is replaced (by
        learning from clicks, on another thread: one reference is read, so never half of each)."""
        eyes = self.eyes
        stack = getattr(self, "_stack", None)
        if stack is None or stack[0] is not eyes:
            mean = np.array([m for m, _, _ in eyes])
            sd = np.array([sd for _, sd, _ in eyes])
            weights = np.array([w for _, _, w in eyes])  # (2, 1 + k, 2)
            stack = self._stack = (eyes, (mean, sd, weights))
        return stack[1]

    def directions_of(self, R: np.ndarray, F: np.ndarray) -> np.ndarray:
        """(n, 2, 3): each eye's gaze direction in camera coordinates, from head rotations
        (n, 3, 3) and eye measurements (n, 2, k)."""
        mean, sd, weights = self._stacked()
        Z = (F - mean) / sd
        # Both eyes are one matrix batch; keep the per-eye dot products and output ownership.
        xy = (Z.transpose(1, 0, 2) @ weights[:, 1:] + weights[:, None, 0]).transpose(1, 0, 2)
        return np.matmul(directions(xy), R.transpose(0, 2, 1))

    def predict_arrays(self, R: np.ndarray, eyes: np.ndarray, F: np.ndarray) -> np.ndarray:
        """Screen pixels (n, 2) from head rotations, eyeball centres (n, 2, 3) and eye measurements:
        each eye's ray met with the screen, the two points averaged (one eye alone when the other's
        ray misses the screen, nan when both do)."""
        rays = self.directions_of(R, F)
        s = self.screen
        n = s.normal
        with np.errstate(divide="ignore", invalid="ignore"):
            k = ((s.origin - eyes) @ n) / (rays @ n)
            k = np.where(k > 0, k, np.nan)
            hit = eyes + k[..., None] * rays - s.origin
            points = np.stack([hit @ s.ex, hit @ s.ey], -1) / s.mm_per_px  # (n, 2 eyes, 2)
            valid = np.isfinite(points).all(-1, keepdims=True)
            count = valid.sum(1)
            return np.where(valid, points, 0.0).sum(1) / np.where(count > 0, count, np.nan)

    def direction(self, heads: list[Head]) -> np.ndarray:
        """(n, 2, 3): each eye's gaze direction in camera coordinates."""
        return self.directions_of(np.array([h.R for h in heads]), np.array([h.features for h in heads]))

    def predict(self, heads: list[Head]) -> np.ndarray:
        return self.predict_arrays(
            np.array([h.R for h in heads]), np.array([h.eyes for h in heads]), np.array([h.features for h in heads])
        )

    def to_json(self) -> dict:
        s = self.screen
        return {
            "screen": {"origin": s.origin.tolist(), "ex": s.ex.tolist(), "ey": s.ey.tolist(), "mm_per_px": s.mm_per_px},
            "eyes": [{"mean": m.tolist(), "sd": sd.tolist(), "weights": w.tolist()} for m, sd, w in self.eyes],
            "alpha": self.alpha,
        }

    @classmethod
    def from_json(cls, data: dict) -> GazeModel:
        s = data["screen"]
        screen = Screen(np.array(s["origin"]), np.array(s["ex"]), np.array(s["ey"]), float(s["mm_per_px"]))
        eyes = [(np.array(e["mean"]), np.array(e["sd"]), np.array(e["weights"])) for e in data["eyes"]]
        return cls(screen, eyes=eyes, alpha=list(data.get("alpha", [])))


# A frame travels through the tracker and calibration as one flat vector, like EyeTrax features:
# rotation (9), position (3), both eyeball centres (6), both eyes' measurements (2 x FEATURES).
FEATURES = 4
VECTOR = 9 + 3 + 6 + 2 * FEATURES


def pack(head: Head) -> np.ndarray:
    return np.concatenate([head.R.ravel(), head.t, head.eyes.ravel(), head.features.ravel()])


def unpack(X: np.ndarray) -> list[Head]:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    return [Head(x[:9].reshape(3, 3), x[9:12], x[12:18].reshape(2, 3), x[18:].reshape(2, FEATURES)) for x in X]


class Blinks:
    """Per-eye aperture against what it should be for where the eye points, also rejecting rapid
    closing transitions.

    The upper lid follows the eye: looking down lowers it, so an open eye looking at the bottom of
    the screen is narrower than one looking at the top. On MPIIFaceGaze (15 people, 37,000 webcam
    images) the lid gap was 0.99 of its top-of-screen size in the screen's top quarter and 0.83 in
    the bottom quarter, and a fixed 80% reference called 35% of bottom-quarter frames closed: the
    gaze stopped there and stayed where it was, about the middle of the screen. So the open
    reference follows the iris: each eye's aperture is compared with the aperture expected at its
    iris's height in the eye (a line through the open-eye frames of the last half minute or so,
    re-levelled by the last 50), which brings the bottom quarter's false closures to 7% on the same
    images; a narrowing that lasts ``shallow_s`` without the lids ever closing is a look downward or
    a squint, not a blink, and is accepted and learned from, so the reference catches up.

    A blink still is: a fall of more than 10% since the previous frame beyond what the iris's own
    move explains (the lids start closing before the iris landmarks fail; a downward saccade
    lowers them with the eye), or an aperture under ``ratio`` of the expected one, or under
    ``deep`` of the open average whatever the eye's direction. Closed frames never lower an
    established reference, and one eye cannot hide the other's closure. The tracker separately
    waits for reopening landmarks to settle before prediction.
    """

    def __init__(
        self,
        history: int = 50,
        ratio: float = 0.8,
        minimum: int = 15,
        deep: float = 0.5,
        shallow_s: float = 0.3,
        slope_history: int = 900,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.history: deque[tuple[float, float, float, float]] = deque(maxlen=history)  # (a_r, a_l, v_r, v_l)
        self.total = [0.0, 0.0, 0.0, 0.0]
        self.slopes = (_Slope(slope_history), _Slope(slope_history))
        self.previous: tuple[float, float] | None = None
        self.previous_height: tuple[float, float] = (0.0, 0.0)
        self.ratio, self.minimum, self.deep, self.shallow_s, self.clock = ratio, minimum, deep, shallow_s, clock
        self.narrow_since: float | None = None  # when the eyes last became narrower than expected
        self.deep_at = -math.inf  # the last frame with the lids (nearly) shut

    @staticmethod
    def measure(points: np.ndarray, eye: tuple[int, int, int, int], iris: int) -> tuple[float, float]:
        """An eye's aperture (lid gap over eye width) and its iris's height in the eye (distance
        below the line between the corners, over the eye width; looking down raises it)."""
        outer, inner, upper, lower = eye
        (ox, oy), (ix, iy), (ux, uy), (lx, ly), (cx, cy) = points[[outer, inner, upper, lower, iris], :2].tolist()
        width = math.hypot(ix - ox, iy - oy)
        if not width > 1e-6:
            return math.nan, math.nan
        nx, ny = -(iy - oy) / width, (ix - ox) / width
        if ny < 0:
            nx, ny = -nx, -ny  # down the image
        below = ((cx - (ox + ix) / 2) * nx + (cy - (oy + iy) / 2) * ny) / width
        return math.hypot(ux - lx, uy - ly) / width, below

    def expected(self, eye: int, v: float) -> float:
        """The open aperture expected for this eye at iris height ``v``."""
        n = len(self.history)
        mean_a, mean_v = self.total[eye] / n, self.total[2 + eye] / n
        value = mean_a + self.slopes[eye].slope() * (v - mean_v)
        return min(max(value, 0.6 * mean_a), 1.3 * mean_a)

    def __call__(self, points: np.ndarray, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        (a_r, v_r), (a_l, v_l) = self.measure(points, RIGHT_EYE, RIGHT_IRIS), self.measure(points, LEFT_EYE, LEFT_IRIS)
        ear, height = (a_r, a_l), (v_r, v_l)
        if not all(math.isfinite(value) for value in ear):
            self.previous, self.narrow_since = None, None
            return True
        if not all(math.isfinite(value) for value in height):
            height = (0.0, 0.0)  # no iris landmarks: the reference does not follow the eye
        ready = len(self.history) >= self.minimum
        if ready:
            n = len(self.history)
            narrow = any(ear[e] < self.ratio * self.expected(e, height[e]) for e in range(2))
            shut = any(ear[e] < self.deep * self.total[e] / n for e in range(2))
        else:
            narrow, shut = any(value < 0.2 for value in ear), any(value < 0.1 for value in ear)
        # Closing: the gap fell 10% more since the last frame than the iris's own move explains
        # (a downward saccade lowers the lids with it; a blink lowers them past where the eye goes).
        explained = (
            [self.expected(e, height[e]) / self.expected(e, self.previous_height[e]) for e in range(2)]
            if ready and self.previous is not None
            else [1.0, 1.0]
        )
        closing = self.previous is not None and any(
            value < before * 0.9 * min(1.0, expect)
            for value, before, expect in zip(ear, self.previous, explained, strict=True)
        )
        self.previous, self.previous_height = ear, height
        if shut:
            self.deep_at = now
        if narrow or closing:
            self.narrow_since = self.narrow_since if self.narrow_since is not None else now
        else:
            self.narrow_since = None
        # Narrow for a while since the lids were last shut, without shutting: looking down, or a
        # squint. Not a blink: the frame is used, and learned from.
        held = (
            ready and narrow and not shut and self.narrow_since is not None
            and now - max(self.narrow_since, self.deep_at) >= self.shallow_s
        )  # fmt: skip
        closed = shut or (narrow and not held)
        blink = closed or (closing and not held)
        # The short bootstrap also learns naturally narrow eyes, but not unmistakably closed
        # ones. Once established, only accepted open-eye observations update the reference.
        learn = not blink if ready else (not closing and min(ear) >= 0.1)
        if learn:
            if len(self.history) == self.history.maxlen:
                old = self.history[0]
                for i in range(4):
                    self.total[i] -= old[i]
            entry = (ear[0], ear[1], height[0], height[1])
            self.history.append(entry)
            for i in range(4):
                self.total[i] += entry[i]
        if ready and not shut and not closing:
            # The slope learns from narrow frames too: learning only from accepted ones would leave
            # out the very frames that show how far the lids drop looking down, and flatten it.
            for e in range(2):
                self.slopes[e].add(height[e], ear[e])
        elif learn:
            for e in range(2):
                self.slopes[e].add(height[e], ear[e])
        return blink


class _Slope:
    """How an eye's aperture changes with its iris's height, from the last ``size`` open frames
    (least squares over running sums). Zero until the eye has looked up and down enough to tell;
    never positive (looking down never opens the lids), and at most as steep as the steepest
    person on MPIIFaceGaze, about twice."""

    def __init__(self, size: int) -> None:
        self.points: deque[tuple[float, float]] = deque(maxlen=size)
        self.sums = [0.0, 0.0, 0.0, 0.0]  # v, a, v*v, v*a

    def add(self, v: float, a: float) -> None:
        if len(self.points) == self.points.maxlen:
            ov, oa = self.points[0]
            self.sums[0] -= ov
            self.sums[1] -= oa
            self.sums[2] -= ov * ov
            self.sums[3] -= ov * oa
        self.points.append((v, a))
        self.sums[0] += v
        self.sums[1] += a
        self.sums[2] += v * v
        self.sums[3] += v * a

    def slope(self) -> float:
        n = len(self.points)
        if n < 60:
            return 0.0
        sv, sa, svv, sva = self.sums
        variance = svv / n - (sv / n) ** 2
        if variance < 0.015**2:
            return 0.0
        return min(max((sva / n - sv / n * sa / n) / variance, -3.5), 0.0)


class HeadEstimator:
    """The head-aware model behind EyeTrax's GazeEstimator interface (extract_features, train,
    predict, save_model, load_model, close), so the tracker and the calibration screens use either.

    The camera's field of view is assumed (``DEFAULT_HFOV_DEG``) and the screen sits centred under
    the camera in its plane; on MPIIFaceGaze these assumptions cost under 1 mm against the measured
    geometry, and a screen width 25% off or a field of view of 55 or 78 degrees cost 0-4 mm."""

    def __init__(
        self,
        monitor: tuple[int, int],
        mm_per_px: float,
        landmarker: object | None = None,
        mirror: bool = False,
        video: bool = False,
    ) -> None:
        self.monitor, self.mm_per_px = (int(monitor[0]), int(monitor[1])), float(mm_per_px)
        self.mirror = mirror  # flip frames back first: the webcam delivers a mirrored image
        # Image mode finds the face afresh in every frame; video mode follows it from the previous
        # frame. On MPIIFaceGaze image mode put the eye landmarks better (27-38 mm median error
        # against 33-66 mm, each image held for three frames at 30 frames/s), but on live video
        # video mode is steadier from frame to frame, so calibration tries both on the same frames
        # and keeps the better (gaze.calibrate); the choice is saved with the model.
        self.video = video
        self._landmarker = landmarker
        self.model = GazeModel(Screen.below_camera(self.monitor, self.monitor[0] * self.mm_per_px))
        self._poser: Poser | None = None
        self._blinks = Blinks()
        self.calibrated_head: tuple[np.ndarray, np.ndarray] | None = None  # mean (R, t) while calibrating
        self.last: Head | None = None
        self.timing: tuple[float, float] | None = None  # the last frame's (landmarks, model) ms

    def measure(self, face: Face) -> Head:
        if self._poser is None or (self._poser.camera.cx, self._poser.camera.cy) != (
            face.size[0] / 2,
            face.size[1] / 2,
        ):
            self._poser = Poser(Camera.nominal(*face.size))
        return self._poser.measure(face)

    def _detect(self, frame: np.ndarray) -> Face | None:
        """Landmarks for this frame: only those the model and the blink test use, once the camera's
        image size is known (the landmarker stand-ins in tests take no such argument)."""
        if self._poser is None or not hasattr(self._landmarker, "video"):
            return self._landmarker.detect(frame)
        return self._landmarker.detect(frame, only=self._poser.needed)

    def extract_features(self, frame: np.ndarray) -> tuple[np.ndarray | None, bool]:
        if self._landmarker is None:
            from .face import Landmarker

            self._landmarker = Landmarker(video=self.video)
        if self.mirror and frame is not None:
            import cv2

            frame = cv2.flip(frame, 1)
        started = time.perf_counter()
        face = self._detect(frame)
        found = time.perf_counter()
        if face is None:
            self.timing = None
            return None, False
        self.last = self.measure(face)
        out = pack(self.last), self._blinks(face.points)
        self.timing = ((found - started) * 1000, (time.perf_counter() - found) * 1000)  # landmarks, model
        return out

    def train(self, X: np.ndarray, y: np.ndarray, variable_scaling: object = None) -> None:
        heads = unpack(X)
        self.model.fit(heads, np.asarray(y, dtype=float))
        Rs = np.array([h.R for h in heads]).mean(0)
        u, _, vt = np.linalg.svd(Rs)
        self.calibrated_head = (u @ vt, np.array([h.t for h in heads]).mean(0))

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        return self.model.predict_arrays(
            X[:, :9].reshape(-1, 3, 3), X[:, 12:18].reshape(-1, 2, 3), X[:, 18:].reshape(-1, 2, FEATURES)
        )

    @staticmethod
    def mirrored(X: np.ndarray, targets_px: np.ndarray) -> bool:
        """Whether calibration frames come from a mirrored image. In a camera's own image, eyes
        turning to the screen's right (the user's right) turn to the image's left, so the iris
        direction's x falls as the target's x grows: correlation -0.8 to -0.9 on MPIIFaceGaze."""
        gx = np.array([h.features[:, 0].mean() for h in unpack(X)])
        x = np.asarray(targets_px, dtype=float)[:, 0]
        if gx.std() == 0 or x.std() == 0:
            return False
        return float(np.corrcoef(gx, x)[0, 1]) > 0.3

    def moved(self, head: Head | None = None) -> tuple[float, float] | None:
        """How far the head is from where it was while calibrating: (mm, degrees)."""
        head = head or self.last
        if head is None or self.calibrated_head is None:
            return None
        R0, t0 = self.calibrated_head
        cos = (np.trace(R0.T @ head.R) - 1) / 2
        return float(np.linalg.norm(head.t - t0)), float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))

    def save_model(self, path: str | Path) -> None:
        data = {
            "kind": "head",
            "version": 1,
            "monitor": list(self.monitor),
            "mm_per_px": self.mm_per_px,
            "mirror": self.mirror,
            "landmarks": "video" if self.video else "image",
            **self.model.to_json(),
        }
        if self.calibrated_head is not None:
            data["calibrated_head"] = {"R": self.calibrated_head[0].tolist(), "t": self.calibrated_head[1].tolist()}
        Path(path).write_text(json.dumps(data) + "\n", encoding="utf-8")

    def load_model(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("kind") != "head":
            raise ValueError(f"{path} is not a head-aware gaze model")
        self.model = GazeModel.from_json(data)
        self.mirror = bool(data.get("mirror", False))
        if self._landmarker is None:
            self.video = data.get("landmarks") == "video"
        head = data.get("calibrated_head")
        self.calibrated_head = (np.array(head["R"]), np.array(head["t"])) if head else None

    def release_frame_buffers(self) -> None:
        """Release pixel scratch with the camera, without reloading the neural model."""
        release = getattr(self._landmarker, "release_image_buffer", None)
        if release is not None:
            release()

    def close(self) -> None:
        close = getattr(self._landmarker, "close", None)
        if close is not None:
            close()
        self._landmarker = None
