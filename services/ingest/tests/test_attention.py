"""Placing fixations on page text and detecting dwells."""

from datetime import UTC, datetime, timedelta

from capture.attention import Attention
from capture.records import Blocks, Fixation, Focus, Page, View

T0 = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
GEOMETRY = {
    "vx": 0.0, "vy": 100.0, "scale": 1.0, "inner_w": 1920.0, "inner_h": 980.0, "screen_w": 1920.0,
    "screen_h": 1080.0, "primary": True, "exact": True, "scroll_y": 0.0, "scroll_h": 4000.0,
}  # fmt: skip
SHORT = "Gradient checkpointing trades compute for memory."
LONG = " ".join(["word"] * 150)  # cut at 1,000 characters; the block reports 200 words


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def snapshot(seconds: float = 0, doc: str = "doc-1", geometry: dict | None = None, **blocks: tuple) -> Blocks:
    blocks = blocks or {"b1": ([100, 200, 800, 120], SHORT), "b2": ([100, 400, 800, 300], LONG)}
    return Blocks.model_validate({
        "id": f"blocks-{doc}-{seconds}", "t": at(seconds), "kind": "blocks", "tab": 7, "window": 1,
        "url": "https://example.com/a", "title": "A page", "doc": doc, "geometry": geometry or GEOMETRY,
        "blocks": [
            {"id": k, "tag": "p", "text": text, "words": 200 if text == LONG else None, "rect": rect}
            for k, (rect, text) in blocks.items()
        ],
    })  # fmt: skip


def fixation(start: float, end: float, page_x: float, page_y: float, geometry: dict = GEOMETRY) -> Fixation:
    """A fixation on a page point, converted to monitor fractions the way the tracker reports them."""
    x = (geometry["vx"] + page_x * geometry["scale"]) / geometry["screen_w"]
    y = (geometry["vy"] + page_y * geometry["scale"]) / geometry["screen_h"]
    return Fixation(id=f"fix-{start:08.2f}", t=at(start), t_end=at(end), x=x, y=y, samples=10)


def watching(**options: float) -> Attention:
    attention = Attention(**options)
    attention.gaze_monitor([2560, 1440])  # same 16:9 shape as the browser's 1920x1080 DIP screen
    attention.browser(snapshot())
    return attention


def view(seconds: float, geometry: dict = GEOMETRY) -> View:
    return View.model_validate({
        "id": f"view-{seconds:08.2f}", "t": at(seconds), "kind": "view", "tab": 7, "window": 1,
        "url": "https://example.com/a", "title": "A page", "doc": "doc-1", "geometry": geometry,
    })  # fmt: skip


def stare(attention: Attention, start: float, seconds: float, x: float = 500, y: float = 260) -> list[dict]:
    """Back-to-back 0.5 s fixations on one point, with the page's heartbeat every 5 s."""
    events, t = [], start
    while t < start + seconds - 1e-9:
        if t % 5 == 0:
            events += attention.browser(view(t))
        events += attention.fixation(fixation(t, t + 0.5, x, y))[1]
        t += 0.5
    return events


def test_a_fixation_lands_on_the_block_under_it() -> None:
    attention = watching()
    hit = attention.place(fixation(0.2, 0.4, 500, 260))
    assert (hit.block, hit.x, hit.y) == ("b1", 500, 260)
    assert attention.place(fixation(0.2, 0.4, 500, 340)).block == "b1"  # 20 px below: within the margin
    assert attention.place(fixation(0.2, 0.4, 960, 360)).block is None  # on the page, beside the text
    assert attention.place(fixation(0.2, 0.4, 500, -50)) is None  # on the browser's toolbar


def test_placement_follows_zoom_scroll_and_monitor() -> None:
    zoomed = GEOMETRY | {"scale": 1.25, "inner_w": 1536.0, "inner_h": 784.0}
    attention = Attention()
    attention.gaze_monitor([1920, 1080])
    attention.browser(snapshot(geometry=zoomed))
    assert attention.place(fixation(0.2, 0.4, 500, 260, zoomed)).block == "b1"
    scrolled = zoomed | {"scroll_y": 200.0}
    attention.browser(view(1, scrolled))
    # Scrolled 200 px since the snapshot: b2 (page y 400-700) now sits at viewport y 200-500.
    assert attention.place(fixation(1.2, 1.4, 500, 260, scrolled)).block == "b2"
    attention.browser(snapshot(2, geometry=GEOMETRY | {"primary": False}))
    assert attention.place(fixation(2.2, 2.4, 500, 260)) is None  # the tracker only knows the primary monitor
    attention.browser(snapshot(3))
    attention.gaze_monitor([1920, 1200])
    assert attention.place(fixation(3.2, 3.4, 500, 260)) is None  # a different monitor shape


def test_a_short_line_stared_at_becomes_a_dwell_at_the_floor() -> None:
    attention = watching()
    assert stare(attention, 0, 7.5) == []
    events = stare(attention, 7.5, 1.0)
    assert [(e["phase"], e["block"], e["dwell_s"]) for e in events] == [("start", "b1", 8.0)]
    assert events[0]["text"] == SHORT and events[0]["reading_s"] == 1.51
    assert stare(attention, 8.5, 3.0) == []  # one start per dwell
    assert attention.fixation(fixation(13.0, 13.4, 500, 900))[1] == []  # looking away, still within 1 s
    ended = attention.fixation(fixation(13.4, 14.8, 500, 900))[1]
    assert [(e["phase"], e["dwell_s"], e["fixations"]) for e in ended] == [("end", 11.5, 23)]


def test_reading_a_long_paragraph_at_normal_speed_is_not_a_dwell() -> None:
    attention = watching()
    # 200 words take 50 s at 238 wpm; 60 s on it is reading, not lingering.
    assert stare(attention, 0, 60, y=550) == []
    assert [e["phase"] for e in stare(attention, 60, 41, y=550)] == ["start"]


def test_a_gap_in_gaze_or_a_tab_switch_ends_the_run() -> None:
    attention = watching(threshold_s=2, factor=1)
    assert [e["phase"] for e in stare(attention, 0, 3)] == ["start"]
    assert [e["phase"] for e in attention.advance(at(5.1))] == ["end"]  # eyes lost for over 2 s
    assert [e["phase"] for e in stare(attention, 6, 3)] == ["start"]
    switch = Focus(id="focus-0001", t=at(9.5), kind="focus", tab=8, window=1, url="https://example.org/", title="B")
    assert [e["phase"] for e in attention.browser(switch)] == ["end"]
    assert attention.fixation(fixation(10, 10.5, 500, 260)) == (None, [])  # tab 8 has not reported its blocks


def test_a_same_page_address_change_keeps_the_run() -> None:
    attention = watching(threshold_s=2, factor=1)
    stare(attention, 0, 1.5)
    spy = Page(
        id="page-0000001", t=at(1.6), kind="page", tab=7, window=1, url="https://example.com/a#section-2",
        title="A page", transition="link", qualifiers=["history_state"],
    )  # fmt: skip
    assert attention.browser(spy) == [] and attention.front is not None
    assert [e["phase"] for e in stare(attention, 1.5, 1.0)] == ["start"]
