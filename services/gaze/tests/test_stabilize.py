"""The gaze ring's filter (gaze.stabilize): still while the eyes rest, quick when they jump, deaf to
a single bad frame."""

from __future__ import annotations

import math
import random

from gaze.fixations import Smoother
from gaze.stabilize import Stabilizer

WIDTH = 2560
FPS = 30.0


def noisy(rng: random.Random, x: float, y: float, sd: float = 45.0) -> tuple[float, float]:
    return x + rng.gauss(0, sd), y + rng.gauss(0, sd)


def movement(points: list[tuple[float, float]]) -> float:
    """RMS distance between consecutive shown points."""
    steps = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:], strict=False)]
    return math.sqrt(sum(s * s for s in steps) / len(steps))


def test_resting_eyes_give_a_still_ring() -> None:
    """Webcam noise as measured (eval/gaze_sim.py): jitter correlated from frame to frame and 8% of
    frames with a bad landmark far off."""
    rng = random.Random(1)
    stabilizer, euro = Stabilizer(WIDTH), Smoother()
    raw, shown, smoothed = [], [], []
    jitter = [0.0, 0.0]
    for k in range(300):
        t = k / FPS
        jitter = [0.6 * j + rng.gauss(0, 49.0 * math.sqrt(1 - 0.36)) for j in jitter]
        bad = [rng.gauss(0, 200.0) if rng.random() < 0.08 else 0.0 for _ in range(2)]
        x, y = 1200.0 + jitter[0] + bad[0], 700.0 + jitter[1] + bad[1]
        raw.append((x, y))
        shown.append(stabilizer(t, x, y))
        smoothed.append(euro(t, x, y))
    settled = slice(60, None)
    assert movement(shown[settled]) < 0.25 * movement(raw[settled])
    assert movement(shown[settled]) < 0.6 * movement(smoothed[settled])  # stiller than the ring was
    assert stabilizer.jumps == 0  # noise alone never looked like a saccade


def test_a_saccade_moves_the_ring_within_two_frames_and_one_bad_frame_does_not() -> None:
    rng = random.Random(2)
    stabilizer = Stabilizer(WIDTH)
    t = 0.0
    for _ in range(60):
        stabilizer(t, *noisy(rng, 600.0, 500.0))
        t += 1 / FPS
    before = stabilizer(t, 1900.0, 1200.0)  # one frame with a bad landmark
    t += 1 / FPS
    assert math.hypot(before[0] - 600, before[1] - 500) < 60
    after = stabilizer(t, *noisy(rng, 600.0, 500.0))
    assert math.hypot(after[0] - 600, after[1] - 500) < 60
    t += 1 / FPS
    shown = []
    for _ in range(3):  # the eyes jump 1,100 px
        shown.append(stabilizer(t, *noisy(rng, 1700.0, 600.0)))
        t += 1 / FPS
    assert math.hypot(shown[0][0] - 600, shown[0][1] - 500) < 60  # the first far frame may be a bad one
    assert math.hypot(shown[1][0] - 1700, shown[1][1] - 600) < 80  # the second confirms it: there


def test_a_jump_to_the_next_button_is_followed_within_a_quarter_second() -> None:
    rng = random.Random(3)
    stabilizer = Stabilizer(WIDTH)
    t = 0.0
    for _ in range(90):
        stabilizer(t, *noisy(rng, 1000.0, 700.0))
        t += 1 / FPS
    reached = None
    for k in range(30):  # 180 px to the right: within one frame's noise, not several frames'
        x, y = stabilizer(t, *noisy(rng, 1180.0, 700.0))
        t += 1 / FPS
        if reached is None and x > 1000 + 0.7 * 180:
            reached = k
    assert reached is not None and (reached + 1) / FPS <= 0.25, reached


def test_reading_along_a_line_is_followed_smoothly() -> None:
    rng = random.Random(4)
    stabilizer = Stabilizer(WIDTH)
    shown = []
    for k in range(120):  # 4 s reading at 300 px/s, as fixations every 0.25 s
        t = k / FPS
        x = 400.0 + 75.0 * int(t / 0.25)
        shown.append(stabilizer(t, *noisy(rng, x, 600.0)))
    assert abs(shown[-1][0] - (400.0 + 75.0 * 15)) < 120  # keeps up with the line
    assert max(abs(b[0] - a[0]) for a, b in zip(shown[30:], shown[31:], strict=False)) < 80  # no lurches


def test_reset_starts_from_the_next_sample_and_a_blink_does_not_count_as_watched_time() -> None:
    stabilizer = Stabilizer(WIDTH)
    for k in range(30):
        stabilizer(k / FPS, 500.0, 500.0)
    stabilizer.reset()
    assert stabilizer(2.0, 900.0, 300.0) == (900.0, 300.0)
    for k in range(30):
        stabilizer(2.0 + k / FPS, 900.0, 300.0)
    resumed, plain = Stabilizer(WIDTH), Stabilizer(WIDTH)
    for s in (resumed, plain):
        for k in range(30):
            s(k / FPS, 900.0, 300.0)
    resumed.resume(29 / FPS + 0.3, 1 / FPS)  # 0.3 s with the eyes shut
    x_resumed, _ = resumed(29 / FPS + 0.3, 960.0, 300.0)
    x_plain, _ = plain(29 / FPS + 0.3, 960.0, 300.0)
    assert x_resumed - 900 < 0.5 * (x_plain - 900)  # one frame's pull, not 0.3 s worth


def test_once_a_look_has_settled_the_ring_holds_its_height_more_firmly_than_its_place_across() -> None:
    """Resting eyes with webcam noise: after the fixation has settled, the ring's height wanders
    less than with the horizontal time constant (text runs across, so up and down is mostly
    noise), and across it is unchanged."""

    def spread(axis: int, **options: float) -> float:
        rng = random.Random(9)
        stabilizer = Stabilizer(WIDTH, **options)
        shown = [stabilizer(k / FPS, *noisy(rng, 1200.0, 700.0, sd=20.0)) for k in range(150)][int(0.5 * FPS) :]
        values = [p[axis] for p in shown]
        mean = sum(values) / len(values)
        return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))

    assert spread(1) < 0.75 * spread(1, tau_y_s=None)
    assert spread(0) == spread(0, tau_y_s=None)
