"""Reading turned into labels for the eye tracker (capture/labels.py): only a line read between the
one above and the one below, with the decoder sure and the page's place on the monitor measured."""

from __future__ import annotations

from capture.attention import _on_monitor
from capture.labels import LineLabels
from capture.reading import LineBox
from capture.records import Geometry

PLACE = (0.5, 0.1, 0.6)


def read(labels: LineLabels, lines: list[int], *, block: str = "b1", p: float = 0.95, per_line: int = 3) -> None:
    for line in lines:
        for k in range(per_line):
            labels.add(f"{block}-{line}-{k}", "doc", block, line, p, PLACE)


def ids(labels: LineLabels) -> list[str]:
    return [label["id"] for label in labels.drain()]


def test_a_line_is_a_label_once_read_between_its_neighbours() -> None:
    labels = LineLabels()
    read(labels, [0, 1])
    assert ids(labels) == []  # line 1 is confirmed only when the eyes go on to line 2
    read(labels, [2])
    assert ids(labels) == ["b1-1-0", "b1-1-1", "b1-1-2"]
    read(labels, [3])
    assert ids(labels) == ["b1-2-0", "b1-2-1", "b1-2-2"]
    labels = LineLabels()
    read(labels, [0, 1, 2, 3])
    assert ids(labels) == [f"b1-{line}-{k}" for line in (1, 2) for k in range(3)]


def test_a_jump_back_another_block_or_leaving_the_text_starts_reading_order_again() -> None:
    labels = LineLabels()
    read(labels, [0, 1, 0, 1, 2])  # back to line 0: line 1 is read again, then confirmed by 0 -> 1 -> 2 only
    assert ids(labels) == ["b1-1-0", "b1-1-1", "b1-1-2"]
    labels = LineLabels()
    read(labels, [0, 1])
    read(labels, [2], block="b2")
    read(labels, [3])
    assert ids(labels) == []
    labels = LineLabels()
    read(labels, [0, 1])
    labels.off()
    read(labels, [2, 3])
    assert ids(labels) == []


def test_unsure_decoding_one_fixation_or_an_estimated_page_position_give_no_labels() -> None:
    labels = LineLabels()
    read(labels, [0, 1, 2], p=0.6)
    assert ids(labels) == []
    labels = LineLabels()
    read(labels, [0, 1, 2], per_line=1)
    assert ids(labels) == []
    labels = LineLabels()
    labels.add("a", "doc", "b1", 0, 0.9, PLACE)
    labels.add("b", "doc", "b1", 1, 0.9, None)  # the page's position estimated, not measured
    labels.add("c", "doc", "b1", 1, 0.9, PLACE)
    labels.add("d", "doc", "b1", 2, 0.9, PLACE)
    assert ids(labels) == ["c"]


def test_a_line_is_placed_on_the_monitor_where_attention_puts_fixations_on_it() -> None:
    g = Geometry(vx=10.0, vy=87.0, scale=2.0, inner_w=1014, inner_h=520, screen_w=2048, screen_h=1152,
                 primary=True, exact=True, scroll_y=300.0, scroll_h=4000.0)  # fmt: skip
    box = LineBox(x=40.0, y=500.0, w=600.0, h=29.0, text="a line")
    y, x0, x1 = _on_monitor(box, g)
    # Attention._place, given a fixation at these monitor fractions, lands on the line's middle.
    page_y = (y * g.screen_h - g.vy) / g.scale + g.scroll_y
    assert abs(page_y - (box.y + box.h / 2)) < 1e-9
    assert abs((x0 * g.screen_w - g.vx) / g.scale - box.x) < 1e-9
    assert abs((x1 * g.screen_w - g.vx) / g.scale - (box.x + box.w)) < 1e-9
