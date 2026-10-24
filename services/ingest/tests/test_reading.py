"""Decoding the line being read from noisy gaze (capture/reading.py)."""

import random
from collections.abc import Iterable, Iterator

import pytest
from capture import attention as attention_module
from capture.attention import Attention
from capture.reading import OFF, LineBox, PageReader, Visit

LEFT, WIDTH, PITCH, HEIGHT = 644.0, 745.0, 28.8, 20.0
VIEW = (0.0, 1040.0, 2048.0)  # viewport top, bottom (page px) and width


def test_line_reader_follows_layout_eviction_and_releases_on_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attention_module, "LAYOUT_CACHE", 4)
    attention = Attention()
    r = attention.page = PageReader("doc", 20, 20)
    for i in range(4):
        attention.layout("doc", str(i), [LineBox(0, i * 40, 400, 20, f"Block {i}")])
    r.update(100, 10, 0, VIEW)
    assert ("0", 0) in r.alpha
    for i in range(4, 12):
        attention.layout("doc", str(i), [LineBox(0, i * 40, 400, 20, f"Block {i}")])
    assert len(attention.layouts) == len(r.blocks) == 4
    assert all(key is OFF or key[0] in r.blocks for key in r.alpha)
    r.update(100, 450, 1, VIEW)  # rebuild without references to an evicted line
    assert {line.key[0] for line in r.lines} == {"8", "9", "10", "11"}
    attention.layout("doc", "0", [LineBox(0, 0, 400, 20, "Block 0")])
    assert r.text(("0", 0)) == "Block 0"
    assert len(r.blocks) == 4
    attention.clear()
    assert attention.page is None


def paragraph(top: float, n: int, last: float = 0.4) -> list[LineBox]:
    """``n`` lines of body text from ``top``; the last one ``last`` of the width."""
    return [
        LineBox(LEFT, top + i * PITCH, WIDTH * (last if i == n - 1 else 1.0), HEIGHT, f"line {i}") for i in range(n)
    ]


PAGE = {
    "h1": [LineBox(LEFT, 41.0, 383.0, 36.0, "A heading")],
    "p1": paragraph(106.4, 2, 0.85),
    "p2": paragraph(240.6, 6),
    "p3": paragraph(437.4, 3, 0.15),
    "p4": paragraph(600.3, 8, 0.36),
}


def reader(sigma: float = 14.0, offset_sd: float = 40.0) -> PageReader:
    r = PageReader("doc-1", sigma, offset_sd)
    for block, lines in PAGE.items():
        r.set_block(block, lines)
    return r


Look = tuple[float, float, float, tuple[str, int]]


def read(
    blocks: list[str], rng: random.Random, offset: tuple[float, float], scatter: float, t: float = 0.0
) -> Iterator[Look]:
    """Fixations along each line, left to right, about one per 1.5 words: (x, y, t, (block, line))."""
    for block in blocks:
        for i, line in enumerate(PAGE[block]):
            stops = max(1, round(line.w / 75))
            for k in range(stops):
                x = line.x + line.w * (0.03 + 0.94 * (k + 0.5) / stops) + offset[0] + rng.gauss(0, scatter)
                y = line.y + line.h / 2 + offset[1] + rng.gauss(0, scatter)
                yield x, y, t, (block, i)
                t += 0.3


def accuracy(r: PageReader, fixations: Iterable[Look]) -> float:
    right = total = 0
    for x, y, t, truth in fixations:
        r.update(x, y, t, VIEW)
        key, _ = r.best()
        total += 1
        right += key == truth
    return right / total


def test_reading_is_followed_line_by_line_through_an_offset_of_one_and_a_half_lines() -> None:
    # The tracker reads 43 px low: a fixation on line 2 lands on line 3. Return sweeps and the
    # page's shape (short last lines, gaps between paragraphs) give the offset away.
    results = []
    for seed in range(10):
        r = reader()
        results.append(accuracy(r, read(["h1", "p1", "p2", "p3", "p4"], random.Random(seed), (20, 43), 14)))
        assert abs(r.offset() - 43) < 15
    assert sum(results) / len(results) >= 0.9 and min(results) >= 0.75


def test_an_offset_upward_is_learned_too() -> None:
    results = [
        accuracy(reader(), read(["h1", "p1", "p2", "p3", "p4"], random.Random(seed), (-15, -40), 14))
        for seed in range(10)
    ]
    assert sum(results) / len(results) >= 0.9


def test_re_reading_a_paragraph_goes_back_to_its_first_line() -> None:
    r = reader()
    rng = random.Random(3)
    path = []
    for x, y, t, truth in read(["p2", "p2"], rng, (0, 30), 14):
        r.update(x, y, t, VIEW)
        key, _ = r.best()
        if truth[1] == 0:
            path.append(key)
    # Line 0 of the second pass (the last fixations of path) is decoded as line 0 again.
    assert path[-3:] == [("p2", 0)] * 3


def test_a_look_at_a_picture_beside_the_text_is_off_the_text() -> None:
    r = reader()
    for x, y, t, _ in read(["p2"], random.Random(1), (0, 0), 14):
        r.update(x, y, t, VIEW)
    for k in range(4):
        r.update(1700 + 10 * k, 300.0, 100.0 + 0.3 * k, VIEW)
    assert r.best()[0] is OFF


def test_a_re_measured_block_forgets_what_was_decoded_on_it() -> None:
    r = reader()
    for x, y, t, _ in read(["p2"], random.Random(2), (0, 0), 14):
        r.update(x, y, t, VIEW)
    assert any(key is not None and key[0] == "p2" for key in r.alpha)
    r.set_block("p2", paragraph(240.6, 7))  # narrower window: one more line
    assert not any(key is not None and key[0] == "p2" for key in r.alpha)
    r.update(LEFT + 30, 240.6 + 6 * PITCH + 10, 200.0, VIEW)
    assert len(r.blocks["p2"]) == 7 and r.best()[0] is not None


def test_only_lines_in_view_are_candidates() -> None:
    r = reader()
    posterior = r.update(LEFT + 100, 650.0, 0.0, (500.0, 900.0, 2048.0))
    assert {key[0] for key in posterior if key is not None} == {"p3", "p4"}


def test_a_visit_counts_time_and_passes_per_line() -> None:
    visit = Visit("doc-1", "p2", 6, 1000.0, texts=[f"line {i}" for i in range(6)])
    for k, line in enumerate([0, 0, 0, 1, 1, 1, 2, 0, 0, 1]):  # line 2 once (0.3 s): not a pass
        visit.add(line, 1000.0 + 0.3 * k, 1000.0 + 0.3 * k + 0.25, 0.3)
    summary = visit.summary()
    lines = {entry["line"]: entry for entry in summary["lines"]}
    assert lines[0]["seconds"] == 1.5 and lines[0]["passes"] == 2
    assert lines[1]["passes"] == 1 and lines[2]["passes"] == 1  # the last stay is 0.25 s
    assert summary["coverage"] == 0.5 and summary["line_count"] == 6
    assert summary["path"][0] == [0.0, 0] and lines[0]["text"] == "line 0"
