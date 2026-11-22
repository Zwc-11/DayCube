"""What is asked back each day (docs/recall.md): the morning's questions, the cue cards, and the
nightly round that writes the episodes they are drawn from.

Each morning ``QUESTIONS`` episodes are asked back, drawn first from 1, 3, 7, 21 and 60 days ago
(widening gaps keep a memory for the fewest recalls), choosing in each day the episode with the
most to lose: the long ones and what mattered, held attention or was new, weighted by how much
of it has faded. A
question gives the time and the kind of thing; the answer is shown only after the person has
tried, and their own verdict is what is logged. Cue cards (at most ``CARDS`` a day) show fading
episodes that mattered, with no question asked; opening one counts as a weaker recall.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..review import timeline
from ..store import LifeStore
from ..vault import Record, Vault, read_json, stored
from . import episodes, history

QUESTIONS, CARDS = 3, 3
GAPS = (1, 3, 7, 21, 60)  # days back, tried in this order
LOOK_BACK = 90  # days of episodes a question or card may come from
MIN_MINUTES = 10.0  # shorter episodes are not asked about
CARD_BELOW = 0.7  # a card shows an episode once less than this is left of it ...
CARD_MATTERED = 0.3  # ... and only one whose importance or feeling reached this
CATCH_UP_DAYS = 7  # the newest finished days, which the nightly round looks at again each night
ASK = {
    "desk": "about {minutes} minutes at your desk. What were you doing?",
    "talk": "a conversation of about {minutes} minutes. Where was it, and what was it about?",
    "place": "about {minutes} minutes away from your desk. Where were you?",
}


def _recent(vault: Vault, today: str, now: datetime) -> dict[str, list[Record]]:
    """Episodes with their strength for each of the LOOK_BACK days before ``today`` that has any."""
    first, out = date.fromisoformat(today), {}
    recalled, w = history.recalls(vault), history.weights(vault)
    for back in range(1, LOOK_BACK + 1):
        day = (first - timedelta(days=back)).isoformat()
        if found := episodes.read(vault, day):
            out[day] = [{**e, "strength": history.strength(e, recalled.get(e["id"], []), now, w)} for e in found]
    return out


def _worth(episode: Record) -> float:
    """What asking it back would save: how much it was (its length up to an hour, and its
    signals), times how much of it has gone."""
    s = episode["signals"]
    size = min(1.0, episode["minutes"] / 60)
    return (size + s["importance"] + s["attention"] + s["novelty"] + s["feeling"]) * (1 - episode["strength"]["left"])


def _askable(episode: Record) -> bool:
    return episode["minutes"] >= MIN_MINUTES and episode["kind"] in ASK


def _when(episode: Record) -> str:
    t = datetime.fromisoformat(episode["t"])
    return f"{t:%A} {t.day} {t:%B}, {t:%H:%M}"


def _choose(vault: Vault, today: str, now: datetime) -> list[Record]:
    recent = _recent(vault, today, now)
    first = date.fromisoformat(today)
    spaced = [(first - timedelta(days=gap)).isoformat() for gap in GAPS]
    out: list[Record] = []
    for day in [d for d in spaced if d in recent] + [d for d in recent if d not in spaced]:
        asked = [e for e in recent[day] if _askable(e)]
        if not asked or len(out) == QUESTIONS:
            continue
        e = max(asked, key=lambda e: (_worth(e), e["id"]))
        cue = f"{_when(e)}: " + ASK[e["kind"]].format(minutes=round(e["minutes"]))
        out.append({"id": f"q_{today.replace('-', '')}_{len(out) + 1}", "episode": e["id"], "day": day, "cue": cue,
                    "answer": e["title"], "highlights": e.get("highlights", []), "kind": e["kind"], "t": e["t"],
                    "until": e["until"], "left": e["strength"]["left"]})  # fmt: skip
    return out


def questions(vault: Vault, today: str, now: datetime) -> Record:
    """The questions of review day ``today``: chosen once, then kept with the answers given
    (``recall/questions/<date>.json``), so they are the same all day."""
    path = history.folder(vault) / "questions" / f"{today}.json"
    if path.is_file():
        return read_json(path)
    asked = {"date": today, "questions": _choose(vault, today, now), "answers": {}}
    if asked["questions"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        vault.write_json(path, asked)
    return asked


def waiting(vault: Vault, today: str, now: datetime) -> int:
    """How many of today's questions have no answer yet."""
    asked = questions(vault, today, now)
    return sum(q["id"] not in asked["answers"] for q in asked["questions"])


def answer(vault: Vault, today: str, question: str, outcome: str, now: datetime) -> Record:
    """The person's verdict on one of today's questions, logged as a recall; the first one counts."""
    if outcome not in history.GRADE:
        raise ValueError(f"An answer is one of {', '.join(history.GRADE)}")
    with vault.lock:
        asked = questions(vault, today, now)
        found = next((q for q in asked["questions"] if q["id"] == question), None)
        if found is None:
            raise FileNotFoundError(question)
        if question not in asked["answers"]:
            history.add(vault, found["episode"], found["day"], outcome, now)
            asked["answers"][question] = outcome
            vault.write_json(history.folder(vault) / "questions" / f"{today}.json", asked)
    return asked


def cards(vault: Vault, today: str, now: datetime) -> list[Record]:
    """Up to CARDS fading episodes that mattered and are not among today's questions."""
    asked = {q["episode"] for q in questions(vault, today, now)["questions"]}
    fading = [
        (day, e)
        for day, found in _recent(vault, today, now).items()
        for e in found
        if e["id"] not in asked and _askable(e) and e["strength"]["left"] < CARD_BELOW
        and max(e["signals"]["importance"], e["signals"]["feeling"]) >= CARD_MATTERED
    ]  # fmt: skip
    fading.sort(key=lambda found: (-_worth(found[1]), found[1]["id"]))
    return [{"episode": e["id"], "day": day, "title": e["title"], "kind": e["kind"], "when": _when(e), "t": e["t"],
             "until": e["until"], "left": e["strength"]["left"]} for day, e in fading[:CARDS]]  # fmt: skip


def opened(vault: Vault, day: str, episode: str, now: datetime) -> Record:
    """An episode opened from a card or a search: a recall that asked nothing."""
    if not any(e["id"] == episode for e in episodes.read(vault, day) or []):
        raise FileNotFoundError(episode)
    return history.add(vault, episode, day, "opened", now)


def forget(vault: Vault, store: LifeStore, days: list[str], zone: str) -> None:
    """After a span of review days ``days`` was deleted and their timelines written again: their
    episodes are made again from what is left, and every kept question about one of those days
    goes (a question quotes its episode)."""
    for day in days:
        path = vault.day_path(day, must_exist=False) / episodes.FILE
        if stored(path).exists():
            path.unlink()
            episodes.write(vault, store, day, zone)
    for path in (history.folder(vault) / "questions").glob("*.json"):
        if any(q["day"] in days for q in read_json(path)["questions"]):
            path.unlink()


def night(vault: Vault, store: LifeStore, zone: str, now: datetime) -> list[str]:
    """The nightly round: the episodes of each finished review day of the last LOOK_BACK that has
    a timeline (oldest first: a day's novelty is judged against the days before it), then the
    weights fitted again. A day older than CATCH_UP_DAYS is written once and then left alone.
    Returns the days whose episodes changed."""
    current = date.fromisoformat(timeline.review_day(now, ZoneInfo(zone)))
    written = []
    for back in range(LOOK_BACK, 0, -1):
        day = (current - timedelta(days=back)).isoformat()
        before = episodes.read(vault, day)
        if before is not None and back > CATCH_UP_DAYS:
            continue
        if episodes.write(vault, store, day, zone) != (before or []):
            written.append(day)
    history.refit(vault)
    return written
