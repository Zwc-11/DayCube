"""The gaze ring overlay: its picture, where it is drawn each display frame, and its frame loop
(the Windows window itself is exercised under Wine; see docs/capture.md)."""

import numpy as np
import pytest
from gaze.overlay import FADED, RING_PX, Overlay, Trail, ring_pixels


def test_the_ring_is_premultiplied_and_anti_aliased() -> None:
    pixels = ring_pixels()
    assert pixels.shape == (RING_PX, RING_PX, 4) and pixels.dtype == np.uint8
    b, g, r, a = (pixels[..., i].astype(int) for i in range(4))
    assert (b <= a).all() and (g <= a).all() and (r <= a).all()  # premultiplied
    c = RING_PX // 2
    assert a[c, c + 11] > 200 and r[c, c + 11] > b[c, c + 11]  # the red stroke at the radius
    assert 20 < a[c, c] < 60  # a faint fill inside
    assert a[0, 0] == 0 and a[-1, -1] == 0  # clear corners
    edge = a[c, c + 13]  # the white outline just outside the stroke
    assert edge > 0 and abs(int(b[c, c + 13]) - int(r[c, c + 13])) < 40
    assert len(np.unique(a)) > 20  # soft edges, not a hard mask
    dashed = ring_pixels(dashed=True)
    assert dashed[..., 3].sum() < pixels[..., 3].sum() * 0.85  # gaps in the stroke


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_the_trail_shows_the_gaze_one_sample_ago_between_points() -> None:
    clock = Clock()
    trail = Trail(clock)
    assert trail.position() is None
    for i in range(10):  # 30 points a second, moving right 30 px a point
        trail.add(i * 30.0, 50.0, t=100.0 + i / 30)
    assert trail.delay() == pytest.approx(1 / 30)
    # Half a sample after the last point: the frame shows half way between the last two.
    x, y, opacity, on = trail.position(100.0 + 9 / 30 + 1 / 60)
    assert x == pytest.approx(255.0) and y == 50.0 and opacity == 1.0 and on
    # Display frames at 144 Hz move it a little every frame, never backwards.
    xs = [trail.position(100.0 + 9 / 30 + k / 144)[0] for k in range(5)]
    assert all(b > a for a, b in zip(xs, xs[1:], strict=False))
    # No new point: it holds at the last, fades after 0.6 s and goes after 4 s.
    assert trail.position(100.0 + 9 / 30 + 0.2)[0] == 270.0
    assert trail.position(100.0 + 9 / 30 + 1.0)[2] == FADED
    assert trail.position(100.0 + 9 / 30 + 4.5) is None
    trail.clear()
    assert trail.position(100.3) is None


def test_the_delay_follows_the_camera_rate() -> None:
    trail = Trail(Clock())
    for i in range(10):
        trail.add(0, 0, t=i / 60)
    assert trail.delay() == pytest.approx(1 / 60)
    trail.clear()
    trail.add(0, 0, t=0.0)
    assert trail.delay() == 0.06  # one point: the longest delay until the rate is known


class FakeWindow:
    refresh_hz = 144.0

    def __init__(self, clock: Clock) -> None:
        self.clock, self.calls = clock, []
        self.visible = False
        self.at = None

    def show(self, dashed: bool, opacity: float) -> None:
        self.calls.append(("show", dashed, opacity))
        self.visible = True

    def move(self, left: int, top: int) -> None:
        self.calls.append(("move", left, top))
        self.at = (left, top)

    def hide(self) -> None:
        self.calls.append(("hide",))
        self.visible = False

    def wait(self) -> None:
        self.clock.t += 1 / self.refresh_hz

    def pump(self) -> None:
        pass

    def close(self) -> None:
        self.calls.append(("close",))


def test_the_overlay_draws_every_display_frame_from_points_at_camera_rate() -> None:
    clock = Clock()
    window = FakeWindow(clock)
    overlay = Overlay((0, 0, 2560, 1440), backend=lambda: window, clock=clock, start=False)
    overlay._backend = window
    assert not overlay.step()  # nothing yet: idle
    next_point = clock.t
    frames = 0
    while clock.t < 101.0:  # one second: points at 30 a second, frames at 144
        if clock.t >= next_point:
            k = (next_point - 100.0) * 30
            overlay.push(0.25 + 0.01 * k, 0.5)  # moving right
            next_point += 1 / 30
        frames += overlay.step()
    moves = [c for c in window.calls if c[0] == "move"]
    assert frames >= 143 and len(moves) >= 130  # it moves on nearly every display frame
    xs = [m[1] for m in moves]
    assert xs == sorted(xs)  # smooth: never jumps back
    assert window.calls[0][0] == "move" and window.calls[1][0] == "show"  # placed before it appears
    state = overlay.state()
    assert state["shown"] and state["fps"] >= 140 and state["refresh_hz"] is None  # set by the thread
    # Off the monitor: dashed at its edge.
    overlay.push(1.4, 0.5)
    for _ in range(20):
        overlay.step()
    assert window.at[0] == 2560 - RING_PX and ("show", True, 1.0) in window.calls  # whole, at the edge
    overlay.clear()
    assert not overlay.step() and not window.visible and overlay.state()["fps"] is None


def test_an_overlay_that_cannot_start_reports_why() -> None:
    def broken():
        raise OSError("no window station")

    overlay = Overlay((0, 0, 1920, 1080), backend=broken)
    assert not overlay.running and "no window station" in overlay.state()["error"]
    overlay.push(0.5, 0.5)  # harmless
    overlay.close()
