"""Every recall made, and what follows from them: each episode's strength now and the curve's
weights (docs/recall.md).

``recall/log.jsonl`` gets a line each time an episode is recalled: a question answered
(``remembered``, ``partly`` or ``forgot``) or the episode opened from a card (``opened``). An
episode's strength at any time is its first stability (from its signals and the weights in use)
carried through those lines (strength.py), so nothing else is stored. The weights start at the
defaults; once there are enough graded answers they are fitted to them, and the fit is kept
(``recall/weights.json``) only when it predicts the newest answers better.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ..vault import Record, Vault, read_json, read_lines, stored
from . import episodes
from .strength import EFFORT, FORGOT, MIN_ANSWERS, Answer, Signals, Weights, fit, initial, replay

FOLDER = "recall"
LOG = "log.jsonl"
WEIGHTS = "weights.json"
OUTCOMES = (*EFFORT, FORGOT)
GRADE = {"remembered": 1.0, "partly": 0.5, FORGOT: 0.0}  # what a question's answer counts as


def folder(vault: Vault) -> Path:
    return vault.root / FOLDER


def _lines(vault: Vault) -> list[Record]:
    path = folder(vault) / LOG
    return read_lines(path) if stored(path).exists() else []


def add(vault: Vault, episode: str, day: str, outcome: str, t: datetime) -> Record:
    """Keep one recall of ``episode`` (of review day ``day``) made at ``t``."""
    if outcome not in OUTCOMES:
        raise ValueError(f"A recall is one of {', '.join(OUTCOMES)}")
    record = {"t": t.isoformat(timespec="seconds"), "episode": episode, "day": day, "outcome": outcome}
    folder(vault).mkdir(parents=True, exist_ok=True)
    vault.append_lines(folder(vault) / LOG, [record])
    return record


def recalls(vault: Vault) -> dict[str, list[tuple[float, str]]]:
    """Every recall by episode: (epoch seconds, outcome)."""
    out: dict[str, list[tuple[float, str]]] = {}
    for r in _lines(vault):
        out.setdefault(r["episode"], []).append((datetime.fromisoformat(r["t"]).timestamp(), r["outcome"]))
    return out


def weights(vault: Vault) -> Weights:
    """The weights in use: the last fit that was kept, else the defaults."""
    path = folder(vault) / WEIGHTS
    return Weights(**read_json(path)["weights"]) if path.is_file() else Weights()


def _made(episode: Record) -> float:
    return datetime.fromisoformat(episode["until"]).timestamp()


def strength(episode: Record, recalled: list[tuple[float, str]], now: datetime, w: Weights) -> Record:
    """An episode's strength at ``now``: days to 90%, the share left, and its recalls so far."""
    state = replay(initial(Signals(**episode["signals"]), w), _made(episode), recalled, now.timestamp(), w)
    return {"stability_days": round(state.stability, 2), "left": round(state.retention, 3), "recalls": state.recalls}


def with_strength(vault: Vault, found: list[Record], now: datetime) -> list[Record]:
    """Episodes ``found``, each with its ``strength`` at ``now``."""
    recalled, w = recalls(vault), weights(vault)
    return [{**e, "strength": strength(e, recalled.get(e["id"], []), now, w)} for e in found]


def refit(vault: Vault) -> Weights:
    """Fit the weights to every graded answer so far; returns the weights now in use."""
    by_day: dict[str, dict[str, Record]] = {}
    graded = []
    before: dict[str, list[tuple[float, str]]] = {}
    for r in _lines(vault):
        t = datetime.fromisoformat(r["t"]).timestamp()
        if r["outcome"] in GRADE:
            if r["day"] not in by_day:
                by_day[r["day"]] = {e["id"]: e for e in episodes.read(vault, r["day"]) or []}
            if e := by_day[r["day"]].get(r["episode"]):
                history = tuple(before.get(r["episode"], ()))
                graded.append(Answer(Signals(**e["signals"]), _made(e), history, t, GRADE[r["outcome"]]))
        before.setdefault(r["episode"], []).append((t, r["outcome"]))
    current = weights(vault)
    fitted = fit(graded, current)
    if fitted != current:
        vault.write_json(folder(vault) / WEIGHTS, {"weights": asdict(fitted), "answers": len(graded)})
    return fitted


def status(vault: Vault) -> Record:
    """Where things stand: recalls by outcome, the share of graded questions that came back, and
    the weights in use (``fitted`` once a fit to the person's answers was kept)."""
    counts = dict.fromkeys(OUTCOMES, 0)
    for r in _lines(vault):
        counts[r["outcome"]] += 1
    graded = sum(counts[o] for o in GRADE)
    return {
        "recalls": counts,
        "graded": graded,
        "came_back": round(sum(counts[o] * GRADE[o] for o in GRADE) / graded, 3) if graded else None,
        "fit_from": MIN_ANSWERS,
        "fitted": (folder(vault) / WEIGHTS).is_file(),
        "weights": asdict(weights(vault)),
    }
