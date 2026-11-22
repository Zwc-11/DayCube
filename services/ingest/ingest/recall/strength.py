"""How strong a memory is: a power curve of forgetting, set by what the episode was and raised by
each recall.

Retention after ``t`` days is ``R = (1 + (19/81) t / S) ** -p``. With ``p = 0.5`` the stability
``S`` is the number of days until 90% is left, and the curve has the long tail measured for human
forgetting (a power law, not an exponential; the same form FSRS fits to review logs).

An episode starts with ``S0 = s * exp(a A + b A E + c N + d I)``: attention ``A``, feeling ``E``
(which counts through attention: a feeling about something not attended leaves little), novelty
``N`` and importance ``I``, each between 0 and 1. A recall multiplies ``S`` by
``1 + g (1 - R) effort``: the more had faded, the more the recall gives (the spacing effect), and
answering a question (effort 1) gives more than opening the episode (``PASSIVE``). A question
that was not remembered halves ``S`` (``lapse``); seeing the answer starts the clock again.

The weights start at these defaults and are fitted to the person's own answers once there are
enough (``fit``); a fit is used only when it predicts held-out answers better than the defaults.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

FACTOR = 19 / 81
PASSIVE = 0.3  # the effort of a recall that asked nothing: the episode opened from a search or a card
EFFORT = {"remembered": 1.0, "partly": 0.5, "opened": PASSIVE}
FORGOT = "forgot"
FITTED = ("s", "a", "c", "d", "g")  # the weights a fit moves; b and p need more answers than v1 has
MIN_ANSWERS = 60  # answers before a fit is tried
HOLD_OUT = 0.25  # the newest share of answers a fit is judged on


@dataclass(frozen=True)
class Weights:
    s: float = 1.0  # days to 90% for an episode with no signal at all
    a: float = 1.0  # attention
    b: float = 0.5  # feeling, through attention
    c: float = 0.5  # novelty
    d: float = 1.0  # importance
    g: float = 2.0  # what a recall adds
    p: float = 0.5  # the curve's power
    lapse: float = 0.5  # what a forgotten question leaves of S


@dataclass(frozen=True)
class Signals:
    attention: float = 0.0
    feeling: float = 0.0
    novelty: float = 0.0
    importance: float = 0.0


def retention(days: float, stability: float, p: float = 0.5) -> float:
    """The share of an episode still there ``days`` after it was last made or recalled."""
    return (1 + FACTOR * max(days, 0.0) / stability) ** -p


def initial(signals: Signals, w: Weights = Weights()) -> float:  # noqa: B008 - an immutable default
    """An episode's first stability, in days."""
    x = w.a * signals.attention + w.b * signals.attention * signals.feeling
    return w.s * math.exp(x + w.c * signals.novelty + w.d * signals.importance)


def after(stability: float, retained: float, outcome: str, w: Weights = Weights()) -> float:  # noqa: B008
    """The stability after a recall made when ``retained`` was left; ``outcome`` is a key of EFFORT or FORGOT."""
    if outcome == FORGOT:
        return stability * w.lapse
    return stability * (1 + w.g * (1 - retained) * EFFORT[outcome])


@dataclass(frozen=True)
class State:
    stability: float  # days
    retention: float  # now
    since: float  # epoch seconds of the episode's end or its last recall
    recalls: int


def replay(
    s0: float,
    made: float,
    recalls: Sequence[tuple[float, str]],
    now: float,
    w: Weights = Weights(),  # noqa: B008
) -> State:
    """An episode's state at ``now``: its first stability carried through its recalls in time
    order. ``made`` and the recalls' times are epoch seconds; recalls after ``now`` are ignored."""
    stability, since, count = s0, made, 0
    for t, outcome in sorted(recalls):
        if t > now or t < since:
            continue
        stability = after(stability, retention((t - since) / 86400, stability, w.p), outcome, w)
        since, count = t, count + 1
    return State(stability, retention((now - since) / 86400, stability, w.p), since, count)


@dataclass(frozen=True)
class Answer:
    """One graded question, for fitting: what the episode was, its recalls before, and whether it came back."""

    signals: Signals
    made: float
    before: tuple[tuple[float, str], ...]
    t: float
    remembered: float  # 1 remembered, 0.5 partly, 0 forgot


def loss(answers: Sequence[Answer], w: Weights) -> float:
    """Mean cross-entropy between the predicted retention at each question and its answer."""
    total = 0.0
    for a in answers:
        r = min(max(replay(initial(a.signals, w), a.made, a.before, a.t, w).retention, 1e-4), 1 - 1e-4)
        total -= a.remembered * math.log(r) + (1 - a.remembered) * math.log(1 - r)
    return total / max(len(answers), 1)


def fit(answers: Sequence[Answer], start: Weights = Weights(), rounds: int = 40) -> Weights:  # noqa: B008
    """Weights that predict the person's answers better than ``start``, or ``start``.

    The oldest answers are fitted by coordinate search (each weight in FITTED scaled up or down,
    the step halving when nothing helps); the result is kept only if it also predicts the newest
    HOLD_OUT share better, so a fit to noise changes nothing."""
    answers = sorted(answers, key=lambda a: a.t)
    if len(answers) < MIN_ANSWERS:
        return start
    cut = int(len(answers) * (1 - HOLD_OUT))
    train, held = answers[:cut], answers[cut:]
    best, best_loss, step = start, loss(train, start), 0.5
    for _ in range(rounds):
        moved = False
        for name in FITTED:
            for scale in (1 + step, 1 / (1 + step)):
                trial = replace(best, **{name: getattr(best, name) * scale})
                trial_loss = loss(train, trial)
                if trial_loss < best_loss - 1e-9:
                    best, best_loss, moved = trial, trial_loss, True
        if not moved:
            step /= 2
            if step < 0.02:
                break
    return best if loss(held, best) < loss(held, start) else start
