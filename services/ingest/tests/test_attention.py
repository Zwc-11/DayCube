"""Placing fixations on page text and detecting dwells."""

import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from capture.attention import Attention, Hit
from capture.records import Blocks, Click, Fixation, Focus, GazeState, Page, View

T0 = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
GEOMETRY = {
    "vx": 0.0, "vy": 100.0, "scale": 1.0, "inner_w": 1920.0, "inner_h": 980.0, "screen_w": 1920.0,
    "screen_h": 1080.0, "primary": True, "exact": True, "scroll_y": 0.0, "scroll_h": 4000.0,
}  # fmt: skip
SHORT = "Gradient checkpointing trades compute for memory."
LONG = " ".join(["word"] * 150)  # cut at 1,000 characters; the block reports 200 words
PRECISE = 12.0  # median gaze error in monitor px: a tracker good enough to point at a line


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


def tracker(attention: Attention, error_px: float | None = PRECISE, monitor: tuple | None = (2560, 1440)) -> None:
    state = GazeState(tracking=True, calibrated=True, error_px=error_px, monitor=list(monitor) if monitor else None)
    attention.gaze_state(state)


def watching(error_px: float = PRECISE, **options: float) -> Attention:
    attention = Attention(**options)
    tracker(attention, error_px)  # same 16:9 shape as the browser's 1920x1080 DIP screen
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
    assert (hit.block, hit.x, hit.y) == ("b1", 500, 260) and hit.p > 0.8
    assert attention.place(fixation(0.2, 0.4, 500, 325)).block == "b1"  # 5 px below: within the tracker's error
    assert attention.place(fixation(0.2, 0.4, 500, 350)).block is None  # in the space between the paragraphs
    assert attention.place(fixation(0.2, 0.4, 1300, 360)).block is None  # on the page, well beside the text
    assert attention.place(fixation(0.2, 0.4, 500, -50)) is None  # on the browser's toolbar


def test_placement_follows_zoom_scroll_and_monitor() -> None:
    zoomed = GEOMETRY | {"scale": 1.25, "inner_w": 1536.0, "inner_h": 784.0}
    attention = Attention()
    tracker(attention, monitor=(1920, 1080))
    attention.browser(snapshot(geometry=zoomed))
    assert attention.place(fixation(0.2, 0.4, 500, 260, zoomed)).block == "b1"
    scrolled = zoomed | {"scroll_y": 200.0}
    attention.browser(view(1, scrolled))
    # Scrolled 200 px since the snapshot: b2 (page y 400-700) now sits at viewport y 200-500.
    assert attention.place(fixation(1.2, 1.4, 500, 260, scrolled)).block == "b2"
    attention.browser(snapshot(2, geometry=GEOMETRY | {"primary": False}))
    assert attention.place(fixation(2.2, 2.4, 500, 260)) is None  # the tracker only knows the primary monitor
    attention.browser(snapshot(3))
    tracker(attention, monitor=(1920, 1200))
    assert attention.place(fixation(3.2, 3.4, 500, 260)) is None  # a different monitor shape


def test_a_short_line_stared_at_becomes_a_dwell_at_the_floor() -> None:
    attention = watching()
    assert stare(attention, 0, 7.5) == []
    events = stare(attention, 7.5, 1.0)
    assert [(e["phase"], e["block"], e["dwell_s"]) for e in events] == [("start", "b1", 8.0)]
    assert events[0]["text"] == SHORT and events[0]["reading_s"] == 1.51
    assert stare(attention, 8.5, 3.0) == []  # one start per dwell
    # Looking away: the first second elsewhere could still be tracker error, then 2.5 s more end it.
    assert attention.fixation(fixation(11.5, 12.5, 1500, 900))[1] == []
    assert attention.fixation(fixation(12.5, 14.5, 1500, 900))[1] == []
    ended = attention.fixation(fixation(14.5, 15.5, 1500, 900))[1]
    # 22 of the 23 fixations: the first alone does not outweigh the 200-word paragraph's prior.
    assert [(e["phase"], e["fixations"]) for e in ended] == [("end", 22)]
    assert 11.0 <= ended[0]["dwell_s"] <= 11.5  # the first second away adds only its chance of being on the line


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


def test_blinks_between_fixations_count_as_looking() -> None:
    attention = watching()
    events, t = [], 0.0
    while t < 8.6:  # 0.4 s looks with 0.2 s blinks: 8 s on the line, 5.6 s of it in fixations
        events += attention.fixation(fixation(t, t + 0.4, 500, 260))[1]
        t += 0.6
    assert [(e["phase"], e["block"]) for e in events] == [("start", "b1")]


# -- webcam-grade gaze ---------------------------------------------------------------------------

# example.com at 200% zoom on a 2560 x 1440 monitor at 125% scaling, as the extension measures it.
EXAMPLE = GEOMETRY | {"vx": 0.0, "vy": 87.0, "scale": 2.0, "inner_w": 1024.0, "inner_h": 508.5,
                      "screen_w": 2048.0, "screen_h": 1152.0}  # fmt: skip
HEADING, BODY, LINK = [212, 133.4, 260.3, 39], [212, 193.9, 586.3, 40], [212, 249.9, 143.3, 20]
TEXT = (
    "This domain is for use in illustrative examples in documents. You may use this domain in literature "
    "without prior coordination or asking for permission."
)  # 24 words: a dwell needs twice their 6.05 s reading time


def example(error_px: float, **options: float) -> Attention:
    attention = Attention(**options)
    tracker(attention, error_px)
    attention.browser(
        snapshot(geometry=EXAMPLE, h1=(HEADING, "Example Domain"), body=(BODY, TEXT), more=(LINK, "More"))
    )
    return attention


def noisy_stare(
    attention: Attention, start: float, seconds: float, rect: list, offset: tuple, spread: float
) -> list[dict]:
    """Fixations anywhere in the middle of ``rect``, shifted by a tracker bias, with tracker noise."""
    rng, events, t = random.Random(4), [], start
    left, top, width, height = rect
    while t < start + seconds - 1e-9:
        if t % 5 == 0:
            events += attention.browser(view(t, EXAMPLE))
        x = left + width * rng.uniform(0.2, 0.8) + offset[0] + rng.gauss(0, spread)
        y = top + height * rng.uniform(0.25, 0.75) + offset[1] + rng.gauss(0, spread)
        hit, found = attention.fixation(fixation(t, t + 0.5, x, y, EXAMPLE))
        events += found
        t += 0.5
    return events


def test_a_stare_survives_webcam_error_that_lands_beside_the_paragraph() -> None:
    # The recalibrated Windows session: 69 px median error, here a 14 CSS px downward bias plus
    # 10 px of scatter. Many fixations fall in the gap below the paragraph or on the link.
    attention = example(69.1)
    events = noisy_stare(attention, 0, 20, BODY, (0, 14), 10)
    starts = [e for e in events if e["phase"] == "start"]
    assert [e["block"] for e in starts] == ["body"]
    assert 12.1 <= starts[0]["dwell_s"] < 12.7


def test_clicks_learn_a_bias_too_large_for_the_page_to_absorb() -> None:
    # A head moved since calibration: every fixation is 45 CSS px (90 DIP) too low, which puts a
    # stare at the heading on the paragraph below it.
    def after(clicks: bool) -> list[str]:
        attention = example(69.1)
        if clicks:
            rng = random.Random(2)
            for i in range(6):  # browsing: look at a link, click it
                x, y = rng.uniform(100, 900), rng.uniform(50, 450)
                t = -60 + 8 * i
                click = Click.model_validate({
                    "id": f"click-{i:04d}", "t": at(t), "kind": "click", "tab": 7, "window": 1,
                    "url": "https://example.com/a", "title": "A page", "x": x, "y": y, "geometry": EXAMPLE,
                })  # fmt: skip
                attention.browser(click)
                attention.fixation(fixation(t - 0.5, t + 0.2, x, y + 45, EXAMPLE))
                attention.fixation(fixation(t + 0.3, t + 1.0, x + 200, y + 45, EXAMPLE))
            health = attention.health(at(0).timestamp())
            assert health["clicks"] == 6 and abs(health["live_error_px"] - 112.5) < 1  # 45 CSS px at 200%, 125%
        return [e["block"] for e in noisy_stare(attention, 0, 16, HEADING, (0, 45), 4) if e["phase"] == "start"]

    assert after(clicks=False) == ["body"]  # 16 s on the heading credited to the paragraph under it
    assert after(clicks=True) == ["h1"]


def test_looking_at_another_block_is_an_excursion_not_a_gap() -> None:
    attention = watching(threshold_s=2, factor=1)
    assert [e["phase"] for e in stare(attention, 0, 3)] == ["start"]
    # 2.4 s on the other paragraph, with gaze arriving all along: longer than the 2 s gap rule,
    # shorter than the 2.5 s excursion, so the run on b1 goes on.
    assert attention.fixation(fixation(3.0, 4.0, 500, 550))[1] == []
    assert attention.fixation(fixation(4.0, 5.3, 500, 550))[1] == []
    assert attention.fixation(fixation(5.3, 5.4, 500, 260))[1] == [] and attention.run.block == "b1"
    ended = stare(attention, 5.5, 3.0, y=550)
    assert [(e["phase"], e["block"]) for e in ended] == [("end", "b1")]


def test_the_block_the_eyes_moved_to_keeps_the_time_since_they_moved() -> None:
    attention = watching(threshold_s=2, factor=1)
    stare(attention, 0, 3)
    events = stare(attention, 3, 6, y=550)  # on to the long paragraph and staying there
    assert [(e["phase"], e["block"]) for e in events] == [("end", "b1")]
    assert attention.run.block == "b2" and attention.run.t0 == at(3) and attention.run.dwell == 6.0


def test_a_re_rendered_block_is_found_again_quickly() -> None:
    attention = watching()
    stare(attention, 0, 5)
    attention.browser(snapshot(5, b9=([100, 200, 800, 120], SHORT), b2=([100, 400, 800, 300], LONG)))
    stare(attention, 5, 1.0)  # two fixations on the same line, now called b9
    assert attention.belief["b9"] > 0.8


def test_a_monitor_that_goes_away_mid_run_does_not_break_anything() -> None:
    attention = watching()
    stare(attention, 0, 7.5)
    tracker(attention, monitor=None)  # undocked: the tracker reports no monitor
    assert attention.fixation(fixation(7.5, 8.5, 500, 260)) == (None, [])
    assert attention.health(at(9).timestamp())["planned_error_px"] == PRECISE


def lined(top: float, n: int, words: int = 12) -> dict:
    """A paragraph of ``n`` lines 28.8 px apart from page y ``top``, with its lines measured."""
    lines = [
        {"rect": [100, top + 28.8 * i, 700 if i < n - 1 else 260, 20], "text": f"line {i} " + "w " * (words - 2)}
        for i in range(n)
    ]
    return {"rect": [100, top, 700, 28.8 * n], "text": " ".join(line["text"] for line in lines), "lines": lines}


def reading_page(seconds: float = 0, **blocks: dict) -> Blocks:
    return Blocks.model_validate({
        "id": f"blocks-{seconds}", "t": at(seconds), "kind": "blocks", "tab": 7, "window": 1,
        "url": "https://example.com/a", "title": "A page", "doc": "doc-1", "geometry": GEOMETRY,
        "blocks": [{"id": k, "tag": "p", **block} for k, block in blocks.items()],
    })  # fmt: skip


def read_lines(
    attention: Attention, blocks: dict, start: float, offset: float, rng: random.Random
) -> Iterator[tuple[Hit | None, list[dict], tuple[str, int]]]:
    """Fixations along each line, left to right; yields (hit, events, (block, line))."""
    t = start
    for block, spec in blocks.items():
        for i, line in enumerate(spec["lines"]):
            left, top, width, height = line["rect"]
            attention.browser(view(t))  # the page's heartbeat
            for k in range(max(1, round(width / 90))):
                x = left + width * (k + 0.5) / max(1, round(width / 90)) + rng.gauss(0, 8)
                y = top + height / 2 + offset + rng.gauss(0, 8)
                hit, events = attention.fixation(fixation(t, t + 0.25, x, y))
                yield hit, events, (block, i)
                t += 0.3


def test_the_line_being_read_is_decoded_and_each_paragraph_read_is_recorded() -> None:
    blocks = {"p1": lined(200, 3), "p2": lined(320, 4)}
    attention = Attention()
    tracker(attention, error_px=40.0)  # 22 CSS px per axis here: a line either way is likely
    attention.browser(reading_page(**blocks))
    hits, reads = [], []
    for hit, events, truth in read_lines(attention, blocks, 0.5, 25.0, random.Random(4)):  # reads 25 px low
        hits.append(((hit.line_block, hit.line), truth))
        reads += [e for e in events if e["kind"] == "read"]
    assert sum(got == truth for got, truth in hits) / len(hits) >= 0.9
    assert attention.reading["block"] == "p2" and attention.reading["line"] == 3
    assert attention.reading["text"].startswith("line 3")
    (first,) = reads  # p1 ended when the eyes had been on p2 for two fixations
    assert first["block"] == "p1" and first["line_count"] == 3 and first["coverage"] == 1.0
    assert [entry["line"] for entry in first["lines"]] == [0, 1, 2] and first["url"] == "https://example.com/a"
    assert [line for _, line in first["path"]] == [0, 1, 2]
    (last,) = attention.browser(
        Focus(id="focus-30", t=at(30), kind="focus", tab=8, window=1, url="https://b.test", title="B")
    )
    assert last["kind"] == "read" and last["block"] == "p2" and last["coverage"] == 1.0
    assert attention.reading is None


def test_re_measured_lines_end_the_visit_on_their_block() -> None:
    blocks = {"p1": lined(200, 3)}
    attention = Attention()
    tracker(attention, error_px=20.0)
    attention.browser(reading_page(**blocks))
    for _ in read_lines(attention, blocks, 0.5, 0.0, random.Random(1)):
        pass
    narrower = lined(200, 4)  # the window got narrower: four lines now
    (ended,) = attention.browser(reading_page(10, p1=narrower))
    assert ended["kind"] == "read" and ended["line_count"] == 3
    assert attention.page.blocks["p1"][3].text.startswith("line 3")


def test_without_lines_no_line_is_decoded() -> None:
    attention = watching()
    hits = [attention.fixation(fixation(0.2 + 0.3 * k, 0.45 + 0.3 * k, 500, 260))[0] for k in range(5)]
    assert hits[-1].block == "b1" and all(hit.line is None for hit in hits) and attention.reading is None
