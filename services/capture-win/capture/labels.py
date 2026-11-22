"""Reading turned into calibration labels for the eye tracker.

While the line decoder (``reading.py``) follows reading, the eyes are on the line being read: its
height on the monitor is known exactly, and the eyes are somewhere along it. That is a label for
the tracker's gaze model (``services/gaze/gaze/learn.py``), thousands an hour where clicks give a
few hundred a day. EVE (Park et al., ECCV 2020) cut webcam gaze error from 3.48 to 2.49 degrees
by learning from what was on screen.

The danger is circularity: the decoder chooses the line partly from where the gaze says the eyes
are, so a model a line off could be told it is right. A line is therefore only a label when the
reading's own structure confirms it, which the gaze's vertical error cannot fake:

- it was read between the line above and the line below, in that order, on the same block (each
  move to the next line is a return sweep: back across the column, which a vertical offset cannot
  make);
- the decoder was sure of it (the mean probability of its fixations at least ``min_p``), and it
  holds at least ``min_fixations`` fixations;
- the page's position on the monitor was measured (``geometry.exact``: from mouse events), not
  estimated from the window's size, which can be a toolbar off.

Each confirmed line's fixations go to the tracker with the line's middle height and horizontal
extent in monitor fractions (``drain``, sent with the ``/gaze`` reply). The tracker pairs them with
the frames it kept for those fixations; its learner counts a line as a fraction of a click, and
only clicks, which the decoder cannot influence, decide whether a fit is taken.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

MIN_P = 0.8
MIN_FIXATIONS = 2
KEEP = 2000  # labels waiting for the tracker's next report


Place = tuple[float, float, float]  # a line's middle y, left x and right x on the monitor, as fractions


@dataclass
class _Line:
    key: tuple[str, str, int]  # (doc, block, line)
    # Each fixation: its id, the decoder's probability and where the line was on the monitor then
    # (the page may scroll while it is read); None where the page's position was only estimated.
    fixations: list[tuple[str, float, Place | None]] = field(default_factory=list)


class LineLabels:
    """Lines confirmed by reading order, as labels: ``add`` each decoded fixation, ``drain`` the
    labels."""

    def __init__(self, *, min_p: float = MIN_P, min_fixations: int = MIN_FIXATIONS) -> None:
        self.min_p, self.min_fixations = min_p, min_fixations
        self.current: _Line | None = None
        self.previous: _Line | None = None  # the line before ``current``, if reading went straight on
        self.out: deque[dict] = deque(maxlen=KEEP)
        self.emitted = 0
        self.lines = 0

    def add(self, fixation_id: str, doc: str, block: str, line: int, p: float, place: Place | None) -> None:
        """A fixation the decoder put on ``line`` of ``block`` with probability ``p``; ``place``:
        that line's middle height and extent on the monitor (fractions), None when the page's
        position is only estimated."""
        key = (doc, block, line)
        current = self.current
        if current is not None and current.key == key:
            current.fixations.append((fixation_id, p, place))
            return
        if current is not None and key == (*current.key[:2], current.key[2] + 1):  # on to the next line, same block
            previous = self.previous
            if previous is not None and previous.key == (*current.key[:2], current.key[2] - 1):
                self._confirm(current)  # read between the line above and the line below
            self.previous = current
        else:
            self.previous = None  # a jump: reading order starts again
        self.current = _Line(key, [(fixation_id, p, place)])

    def off(self) -> None:
        """The eyes left the text: reading order starts again."""
        self.current = self.previous = None

    def _confirm(self, line: _Line) -> None:
        self.lines += 1
        if len(line.fixations) < self.min_fixations:
            return
        if sum(p for _, p, _ in line.fixations) / len(line.fixations) < self.min_p:
            return
        for fixation_id, _, place in line.fixations:
            if place is None:
                continue
            y, x0, x1 = place
            self.out.append({"id": fixation_id, "y": round(y, 5), "x0": round(x0, 5), "x1": round(x1, 5)})
            self.emitted += 1

    def drain(self) -> list[dict]:
        out = list(self.out)
        self.out.clear()
        return out
