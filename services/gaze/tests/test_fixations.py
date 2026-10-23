"""Fixation detection and smoothing on synthetic gaze."""

import random
import statistics

from gaze.fixations import Fixation, FixationDetector, OneEuro

W, H = 1920, 1080


def samples(
    detector: FixationDetector, start: float, seconds: float, x: float, y: float, jitter_px: float = 0, fps: float = 15
) -> list[Fixation]:
    out, rng = [], random.Random(int(start * 100))
    for i in range(int(seconds * fps)):
        dx, dy = rng.uniform(-jitter_px, jitter_px) / W, rng.uniform(-jitter_px, jitter_px) / H
        out += detector.add(start + i / fps, x + dx, y + dy)
    return out


def test_two_fixations_around_a_saccade_have_the_right_centroids() -> None:
    detector = FixationDetector(W, H, max_duration=5)
    found = samples(detector, 0.0, 0.6, 0.25, 0.30, jitter_px=20)
    found += samples(detector, 0.6, 0.6, 0.70, 0.60, jitter_px=20)
    found += detector.flush()
    assert len(found) == 2
    (a, b) = found
    assert abs(a.x - 0.25) < 0.01 and abs(a.y - 0.30) < 0.02 and abs(b.x - 0.70) < 0.01 and abs(b.y - 0.60) < 0.02
    assert a.end <= b.start and a.samples >= 8


def test_a_long_stare_is_reported_while_it_lasts() -> None:
    detector = FixationDetector(W, H)
    found = samples(detector, 0.0, 3.2, 0.5, 0.5, jitter_px=30)
    assert len(found) == 3 and all(0.9 <= f.end - f.start <= 1.0 + 1e-9 for f in found)


def test_gaps_split_fixations_and_wandering_gaze_is_not_one() -> None:
    detector = FixationDetector(W, H, max_duration=5)
    found = samples(detector, 0.0, 0.5, 0.4, 0.4) + samples(detector, 1.0, 0.5, 0.4, 0.4) + detector.flush()
    assert len(found) == 2  # a half-second gap (blink, lost face) ends the first
    drifting = FixationDetector(W, H)
    found = [f for i in range(20) for f in drifting.add(i / 15, 0.05 + i * 0.042, 0.5)]  # 80 px a frame
    assert found + drifting.flush() == []


def test_one_euro_steadies_a_still_point_and_follows_a_jump() -> None:
    rng = random.Random(1)
    smooth = OneEuro()
    raw = [500 + rng.gauss(0, 40) for _ in range(150)]
    out = [smooth(i / 15, value) for i, value in enumerate(raw)]
    assert statistics.pstdev(out[30:]) < statistics.pstdev(raw[30:]) / 2
    after = [smooth(10 + i / 15, 1400.0) for i in range(30)]
    assert abs(after[-1] - 1400) < 50  # within two seconds of a saccade-sized jump
