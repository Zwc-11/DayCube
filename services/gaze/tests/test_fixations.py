"""Fixation detection and smoothing on synthetic gaze."""

import random
import statistics

from gaze.fixations import Fixation, FixationDetector, OneEuro, Smoother

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


def webcam(seconds: float, sigma_px: float, seed: int = 3, fps: float = 30) -> list[tuple[float, float, float]]:
    """A steady look at the middle of the screen as a noisy webcam reports it, after smoothing:
    correlated frame jitter of ``sigma_px`` on each axis."""
    rng, smooth = random.Random(seed), Smoother()
    ex = ey = 0.0
    out = []
    for i in range(int(seconds * fps)):
        ex = 0.6 * ex + 0.8 * rng.gauss(0, sigma_px)
        ey = 0.6 * ey + 0.8 * rng.gauss(0, sigma_px)
        x, y = smooth(i / fps, 960 + ex, 540 + ey)
        out.append((i / fps, x / W, y / H))
    return out


def coverage(detector: FixationDetector, samples: list[tuple[float, float, float]]) -> float:
    found = [f for t, x, y in samples for f in detector.add(t, x, y)] + detector.flush()
    return sum(f.end - f.start for f in found) / (samples[-1][0] - samples[0][0])


def test_the_threshold_follows_the_noise_so_a_noisy_look_is_not_lost() -> None:
    noisy = webcam(20, 120)  # a 640x480 camera in dim light: 120 px of frame jitter
    fixed = coverage(FixationDetector(W, H, noise_k=0), noisy)  # the old fixed 6% threshold
    adaptive = FixationDetector(W, H)
    assert fixed < 0.5 and coverage(adaptive, noisy) > 0.8
    assert 20 < adaptive.noise_px < 120 and adaptive.dispersion > adaptive.base  # scatter after smoothing
    quiet = FixationDetector(W, H)
    coverage(quiet, webcam(20, 20))
    assert quiet.dispersion == quiet.base  # a quiet session keeps the floor, and its resolution


def test_steady_motion_does_not_count_as_noise() -> None:
    detector = FixationDetector(W, H)
    for i in range(300):  # 10 s of slow pursuit across the screen
        detector.add(i / 30, 0.1 + i * 0.0025, 0.5)
    assert detector.noise_px < 5


def test_quick_looks_between_two_targets_stay_two_targets() -> None:
    """Saccades are not noise: eyes moving between two points 300 px apart every 0.3 s (one
    saccade in every second difference the estimator sees) still give fixations on the points."""
    rng, smooth, detector = random.Random(1), Smoother(), FixationDetector(2560, 1440)
    found, ex, ey = [], 0.0, 0.0
    for i in range(600):
        t = i / 30
        ex, ey = 0.6 * ex + 0.8 * rng.gauss(0, 20), 0.6 * ey + 0.8 * rng.gauss(0, 20)
        x, y = smooth(t, 1000 + 300 * (int(t / 0.3) % 2) + ex, 700 + ey)
        found += detector.add(t, x / 2560, y / 1440)
    found += detector.flush()
    between = sum(f.end - f.start for f in found if 1075 < f.x * 2560 < 1225)
    assert len(found) > 40 and between < 0.5  # of 20 s: at most a brief piece while the smoother catches up
