"""What you planned against what the day was, and when your long stretches tend to happen.

**Planned blocks** come from two places:

- the daily note's own text (outside the generated region): under a heading named Plan or Today,
  each line with a time range is a block, ``- 09:30–11:00 DayCube splats`` (``9:30-11``,
  ``14:00 - 15:30``, a ``[ ]`` checkbox in front, all accepted);
- calendar files in ``vault/calendar/*.ics`` (an export or a synced copy): each event of the day
  with a start and end time. Weekly and daily repeats (``RRULE`` with ``BYDAY``, ``INTERVAL``,
  ``UNTIL``, ``COUNT``) and ``EXDATE`` are followed, which covers a term's class schedule; all-day
  events are not blocks.

A block **went as planned** when at least 60% of it was desk time on something its words name
(its project, category, or the window's title), **partly** from 20%, **went elsewhere** when it
was mostly desk time on something else (which is named), and was **missed** otherwise.

**Best hours**: of the last 14 review days, the two hours in which the desk stretches of 25 min or
more most often ran. Tomorrow's list suggests keeping them for the nearest deadline or the day's
main project.
"""

from __future__ import annotations

import re
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..time import instant
from ..vault import Record, Vault, read_lines, read_text, stored

CALENDAR = "calendar"
DONE_SHARE, PARTLY_SHARE, ELSEWHERE_SHARE = 0.6, 0.2, 0.3
HISTORY_DAYS = 14
FOCUS_S = 25 * 60.0
MIN_STRETCHES = 3  # long stretches in the history before best hours are suggested
HEADINGS = re.compile(r"^#{1,6}\s*(plan|today|today's plan|planned)\b", re.IGNORECASE)
HEADING = re.compile(r"^#{1,6}\s")
RANGE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\[[ xX]\]\s*)?(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*(?:–|—|-|to)\s*"
    r"(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?:[:·|,-]\s*)?(?P<text>.+?)\s*$"
)
STOP = frozenset({"the", "and", "for", "with", "on", "at", "to", "of", "a", "an", "in", "my", "do", "work", "time"})
WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


@dataclass(frozen=True, slots=True)
class Block:
    t0: float
    t1: float
    text: str
    source: str  # note, calendar


# -- the daily note ----------------------------------------------------------------------------


def _own_text(text: str) -> str:
    """The note without its generated region, which DayCube rewrites."""
    from ..notes.markdown import END, START

    while START in text and END in text.split(START, 1)[1]:
        head, rest = text.split(START, 1)
        text = head + rest.split(END, 1)[1]
    return text


def note_blocks(text: str, day: str, tz: ZoneInfo) -> list[Block]:
    """The time ranges under a Plan or Today heading. A range past midnight (``23:00-01:00``) ends
    the next day; hours before 04:00 are the next calendar day, as the review day is."""
    blocks = []
    under = False
    base = date.fromisoformat(day)
    for line in _own_text(text).splitlines():
        if HEADING.match(line):
            under = bool(HEADINGS.match(line))
            continue
        if not under:
            continue
        found = RANGE.match(line)
        if not found:
            continue
        h1, h2 = int(found["h1"]), int(found["h2"])
        m1, m2 = int(found["m1"] or 0), int(found["m2"] or 0)
        if h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59:
            continue

        def at(h: int, m: int) -> datetime:
            shift = 1 if h < 4 else 0
            return datetime.combine(base + timedelta(days=shift), time(h % 24, m), tz)

        start, end = at(h1, m1), at(h2, m2)
        if end <= start:
            end += timedelta(days=1)
        if end - start <= timedelta(hours=12):
            blocks.append(Block(start.timestamp(), end.timestamp(), found["text"].strip(" .;"), "note"))
    return blocks


# -- calendar files ----------------------------------------------------------------------------


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _when(value: str, params: dict[str, str], tz: ZoneInfo) -> datetime | None:
    """A DTSTART/DTEND/EXDATE value; None for a date without a time (all day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return None
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
        moment = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    zone = tz
    if "TZID" in params:
        try:
            zone = ZoneInfo(params["TZID"].strip('"'))
        except (ZoneInfoNotFoundError, ValueError):
            zone = tz
    return moment.replace(tzinfo=zone)


def _property(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *rest = head.split(";")
    params = {}
    for item in rest:
        key, _, val = item.partition("=")
        params[key.upper()] = val
    return name.upper(), params, value


def _text(value: str) -> str:
    return value.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def ics_blocks(text: str, start: datetime, stop: datetime, tz: ZoneInfo) -> list[Block]:
    """Events of a calendar file that overlap [start, stop), repeats followed."""
    blocks = []
    event: dict | None = None
    for line in _unfold(text):
        if line.upper() == "BEGIN:VEVENT":
            event = {"exdates": set()}
            continue
        if line.upper() == "END:VEVENT":
            if event is not None:
                blocks += _occurrences(event, start, stop)
            event = None
            continue
        if event is None:
            continue
        name, params, value = _property(line)
        if name in ("DTSTART", "DTEND"):
            event[name] = _when(value, params, tz)
            event[name + "_all_day"] = event[name] is None
        elif name == "SUMMARY":
            event["summary"] = " ".join(_text(value).split())
        elif name == "RRULE":
            event["rrule"] = {k.upper(): v for k, _, v in (p.partition("=") for p in value.split(";"))}
        elif name == "EXDATE":
            for item in value.split(","):
                moment = _when(item, params, tz)
                if moment is not None:
                    event["exdates"].add(moment.astimezone(ZoneInfo("UTC")).replace(tzinfo=None))
        elif name == "STATUS" and value.strip().upper() == "CANCELLED":
            event["cancelled"] = True
    return blocks


def _occurrences(event: dict, start: datetime, stop: datetime) -> list[Block]:
    first, end = event.get("DTSTART"), event.get("DTEND")
    if first is None or end is None or event.get("cancelled") or end <= first:
        return []
    length = end - first
    summary = event.get("summary") or "(no title)"
    rule = event.get("rrule")
    out = []

    def add(moment: datetime) -> None:
        if moment.astimezone(ZoneInfo("UTC")).replace(tzinfo=None) in event["exdates"]:
            return
        if moment < stop and moment + length > start:
            out.append(Block(moment.timestamp(), (moment + length).timestamp(), summary, "calendar"))

    if not rule:
        add(first)
        return out
    freq = rule.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY"):
        add(first)  # monthly and yearly repeats: the first only
        return out
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = None
    if rule.get("UNTIL"):
        until = _when(rule["UNTIL"], {}, first.tzinfo) or datetime.strptime(rule["UNTIL"][:8], "%Y%m%d").replace(
            hour=23, minute=59, tzinfo=first.tzinfo
        )
    days = sorted(WEEKDAYS[d[-2:]] for d in rule.get("BYDAY", "").split(",") if d[-2:] in WEEKDAYS)
    made = 0
    local = first.replace(tzinfo=None)
    week0 = local.date() - timedelta(days=local.weekday())
    day = local.date()
    while True:
        if freq == "DAILY":
            ok = (day - local.date()).days % interval == 0
        else:
            weeks = ((day - timedelta(days=day.weekday())) - week0).days // 7
            ok = weeks % interval == 0 and (day.weekday() in days if days else day.weekday() == local.weekday())
        if ok:
            moment = datetime.combine(day, local.time(), first.tzinfo)
            if until is not None and moment > until:
                break
            made += 1
            if count is not None and made > count:
                break
            add(moment)
        if moment_past(day, stop) or (day - local.date()).days > 3660:
            break
        day += timedelta(days=1)
    return out


def moment_past(day: date, stop: datetime) -> bool:
    return day > stop.date() + timedelta(days=1)


def calendar_blocks(vault: Vault, start: datetime, stop: datetime, tz: ZoneInfo) -> tuple[list[Block], list[str]]:
    folder = vault.root / CALENDAR
    blocks, errors = [], []
    if folder.is_dir():
        for path in sorted(folder.glob("*.ics")):
            try:
                blocks += ics_blocks(read_text(path), start, stop, tz)
            except (OSError, ValueError, KeyError) as error:
                errors.append(f"{CALENDAR}/{path.name} left out: {type(error).__name__}")
    return blocks, errors


def planned(vault: Vault, note: Path | None, day: str, start: datetime, stop: datetime) -> tuple[list[Block], list[str]]:
    """The day's blocks from its daily note and the calendar files, in time order, in the window."""
    tz = start.tzinfo if isinstance(start.tzinfo, ZoneInfo) else ZoneInfo(str(start.tzinfo))
    blocks: list[Block] = []
    if note is not None and note.exists():
        with suppress(OSError):
            blocks += note_blocks(read_text(note), day, tz)
    found, errors = calendar_blocks(vault, start, stop, tz)
    blocks += found
    a, b = start.timestamp(), stop.timestamp()
    inside = {(x.t0, x.t1, x.text.casefold()): x for x in blocks if x.t0 < b and x.t1 > a}
    return sorted(inside.values(), key=lambda x: (x.t0, x.t1)), errors


# -- planned against actual ----------------------------------------------------------------------


def words(text: str) -> set[str]:
    """What a block's text can be matched by: its words of 3 letters or more and its course codes."""
    lowered = text.casefold()
    out = {w for w in re.findall(r"[^\W\d_][\w+#.-]{2,}", lowered) if w not in STOP}
    out |= {re.sub(r"\s+", " ", c) for c in re.findall(r"[a-z]{2,6}\s?\d{3}[a-z]?", lowered)}
    return out


def _matches(terms: set[str], segment: Record) -> bool:
    haystack = " ".join(str(segment.get(k) or "") for k in ("project", "category", "doing")).casefold()
    return any(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", haystack) for t in terms)


@dataclass
class Outcome:
    block: Block
    status: str  # done, partly, elsewhere, missed
    share: float  # of the block, on what it named
    instead: str | None  # what the desk time went to, when elsewhere
    instead_s: float
    segments: list[int]


def compare(blocks: list[Block], segments: list[Record]) -> list[Outcome]:
    spans = [(instant(s["t"]).timestamp(), instant(s["until"]).timestamp()) for s in segments]
    out = []
    for block in blocks:
        terms = words(block.text)
        length = block.t1 - block.t0
        matched = desk = 0.0
        other: dict[str, float] = defaultdict(float)
        cited = []
        for n, (segment, (a, b)) in enumerate(zip(segments, spans, strict=True)):
            seconds = max(0.0, min(b, block.t1) - max(a, block.t0))
            if seconds <= 0:
                continue
            cited.append(n)
            if segment["where"] != "desk":
                continue
            desk += seconds
            if terms and _matches(terms, segment):
                matched += seconds
            else:
                other[segment["project"] or segment["category"] or segment["doing"]] += seconds
        share = matched / length if length else 0.0
        top = max(other, key=lambda k: other[k]) if other else None
        if share >= DONE_SHARE:
            status = "done"
        elif share >= PARTLY_SHARE:
            status = "partly"
        elif desk / length >= ELSEWHERE_SHARE if length else False:
            status = "elsewhere"
        else:
            status = "missed"
        out.append(Outcome(block, status, share, top if status == "elsewhere" else None,
                           other.get(top, 0.0) if top else 0.0, cited))  # fmt: skip
    return out


# -- best hours ----------------------------------------------------------------------------------


def best_hours(vault: Vault, day: str, tz: ZoneInfo, days: int = HISTORY_DAYS) -> dict | None:
    """The two-hour window (on the half hour, 06:00 to 24:00) the long desk stretches of the last
    ``days`` review days (up to and including ``day``) overlapped most, with what it rests on."""
    seconds = defaultdict(float)  # half-hour slot (0..47) -> seconds of long stretches in it
    stretches: list[tuple[str, int, Record]] = []
    first = date.fromisoformat(day)
    for back in range(days):
        name = (first - timedelta(days=back)).isoformat()
        path = vault.day_path(name, must_exist=False) / "timeline.jsonl"
        if not stored(path).exists():
            continue
        try:
            segments = read_lines(path)
        except (OSError, ValueError):
            continue
        for n, segment in enumerate(segments):
            if segment.get("where") != "desk":
                continue
            t0, t1 = instant(segment["t"]).astimezone(tz), instant(segment["until"]).astimezone(tz)
            if (t1 - t0).total_seconds() < FOCUS_S:
                continue
            stretches.append((name, n, segment))
            cursor = t0
            while cursor < t1:
                slot_end = cursor.replace(minute=30 if cursor.minute < 30 else 0, second=0, microsecond=0)
                if cursor.minute >= 30:
                    slot_end += timedelta(hours=1)
                piece_end = min(slot_end, t1)
                seconds[cursor.hour * 2 + (cursor.minute >= 30)] += (piece_end - cursor).total_seconds()
                cursor = piece_end
    if len(stretches) < MIN_STRETCHES:
        return None
    best = max(range(12, 45), key=lambda s: (sum(seconds[s + k] for k in range(4)), -s))  # 06:00 .. 22:00 starts
    total = sum(seconds[best + k] for k in range(4))
    if total <= 0:
        return None

    def label(slot: int) -> str:
        return f"{slot // 2 % 24:02d}:{'30' if slot % 2 else '00'}"

    inside = []
    for name, n, segment in stretches:  # the longest stretches that ran in the window, newest days first
        t0, t1 = instant(segment["t"]).astimezone(tz), instant(segment["until"]).astimezone(tz)
        a = datetime.combine(t0.date(), time(best // 2, 30 * (best % 2)), tz)
        if t0 < a + timedelta(hours=2) and t1 > a:
            inside.append((name, n, segment))
    return {"from": label(best), "to": label(best + 4), "stretches": len(stretches), "days": days,
            "seconds": total, "examples": inside[:3]}  # fmt: skip
