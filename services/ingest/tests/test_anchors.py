"""Clicks as calibration points: matching gaze to clicks, the live error, and the offset."""

import random

from capture.anchors import Anchors

W, H = 2560, 1440


def watch(
    anchors: Anchors, clicks: list[tuple[float, float, float]], offset: tuple[float, float], noise: float = 0
) -> None:
    """Each click at time t gets fixations around it, offset from the click as a tracker would be."""
    rng = random.Random(1)
    for t, x, y in clicks:
        gx, gy = x + offset[0] + rng.gauss(0, noise), y + offset[1] + rng.gauss(0, noise)
        anchors.click(t, x / W, y / H)
        anchors.fixation(t - 0.6, t + 0.2, gx / W, gy / H)
        anchors.fixation(t + 0.3, t + 0.9, (gx + 400) / W, gy / H)  # looking on after the click


def anchors(**options: float) -> Anchors:
    a = Anchors(**options)
    a.set_monitor((W, H), 70.0)
    return a


def test_a_click_waits_for_the_gaze_around_it_and_measures_the_offset() -> None:
    a = anchors()
    a.click(100.0, 0.5, 0.5)
    assert a.live(100.0) == (0, None, None)  # no fixation yet
    a.fixation(99.5, 100.05, (1280 + 30) / W, (720 - 40) / H)
    assert len(a.anchors) == 0  # gaze up to 0.1 s after the click may still come
    a.fixation(100.2, 100.8, 0.9, 0.9)
    (anchor,) = a.anchors
    assert (round(anchor.dx), round(anchor.dy)) == (30, -40)


def test_clicks_without_gaze_or_far_from_it_are_not_used() -> None:
    a = anchors()
    a.click(10.0, 0.5, 0.5)
    a.fixation(11.0, 11.5, 0.5, 0.5)  # starts after the click: nothing around it
    a.click(20.0, 0.2, 0.2)
    a.fixation(19.6, 20.3, 0.8, 0.8)  # 1,700 px away: clicked without looking
    assert len(a.anchors) == 0 and a.rejected == 1
    a.click(30.0, 0.5, 0.5)
    a.fixation(41.0, 41.2, 0.5, 0.5)  # gaze stopped arriving: the click is given up after 10 s
    assert not a.pending


def test_the_offset_is_learned_and_shrunk_toward_zero_when_clicks_are_few() -> None:
    a = anchors()
    watch(a, [(10.0, 1000, 600)], (50, 30))
    one = a.offset(1000 / W, 600 / H, 11.0)
    assert 0.4 < one[0] * W / 50 < 0.6  # one click is worth about as much as the prior of no offset
    watch(a, [(10.0 + 5 * i, 600 + 150 * i, 400 + 60 * i) for i in range(1, 9)], (50, 30))
    dx, dy = a.offset(1000 / W, 600 / H, 60.0)
    assert abs(dx * W - 50) < 8 and abs(dy * H - 30) < 6
    n, raw, corrected = a.live(60.0)
    assert n == 9 and abs(raw - 58.3) < 1 and corrected < 15  # each click predicted from the others
    assert a.error_px(60.0) == corrected
    health = a.health(60.0)
    assert health["clicks"] == 9 and health["offset_px"][0] > 40


def test_old_clicks_and_another_monitor_are_forgotten() -> None:
    a = anchors(max_age_s=600)
    watch(a, [(10.0 + i, 800 + 100 * i, 700) for i in range(6)], (60, 0))
    assert a.live(20.0)[0] == 6 and a.live(1000.0)[0] == 0
    assert a.offset(0.5, 0.5, 1000.0) == (0.0, 0.0)
    a.set_monitor((1920, 1080), 70.0)
    assert not a.anchors and a.live(20.0) == (0, None, None)


def test_a_new_calibration_starts_over_and_stale_clicks_stop_setting_the_error() -> None:
    a = anchors()
    watch(a, [(10.0 + 5 * i, 600 + 150 * i, 400 + 60 * i) for i in range(6)], (50, 30))
    assert a.error_px(60.0) is not None
    assert a.error_px(10.0 + 25 + 601) is None  # the newest click is over 10 minutes old
    a.set_monitor((W, H), 70.0)  # the same calibration reported again
    assert len(a.anchors) == 6
    a.set_monitor((W, H), 64.2)  # recalibrated: the old offsets belong to the old model
    assert not a.anchors and a.live(60.0) == (0, None, None)


def test_the_live_error_uses_only_earlier_clicks() -> None:
    a = anchors()
    # The offset changes halfway: the first click after the change is predicted from the old ones.
    watch(a, [(10.0 + 5 * i, 600 + 100 * i, 500) for i in range(4)], (0, 0))
    watch(a, [(40.0 + 5 * i, 600 + 100 * i, 500) for i in range(4)], (80, 0))
    _, raw, corrected = a.live(60.0)
    assert corrected > 20  # leave-one-out with later clicks would see the new offset coming


def test_clicks_never_answered_by_gaze_are_bounded() -> None:
    a = anchors()
    for i in range(1000):
        a.click(float(i), 0.5, 0.5)
    assert len(a.pending) == 50
