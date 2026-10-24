"""What a question asks for besides its words: when, which kind of record, and on which site.

"What did I read about hash tables yesterday afternoon on Wikipedia" is a search for "hash tables"
in reads, dwells and looks, between noon and 5 pm yesterday, on en.wikipedia.org. The words that
set those filters are taken out of the search, so a question made only of them ("what did I search
yesterday") lists the records instead of matching the word "search". No language model: dates,
weekdays, parts of the day, kinds and sites are read with patterns, and anything not recognised
stays in the search text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")  # fmt: skip
PARTS = {"morning": (5, 12), "afternoon": (12, 17), "evening": (17, 24), "night": (18, 24), "tonight": (18, 24)}
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10}

READ = ("web.read", "web.dwell", "web.look", "ocr.block", "screen.read", "screen.page")
KINDS: list[tuple[str, tuple[str, ...], str]] = [
    (r"\b(?:search(?:ed|es|ing)?|googled|looked up)\b(?: for)?", ("web.search",), "searches"),
    (r"\b(?:read|reading)\b", READ, "reading"),
    (r"\b(?:stared at|stuck on|stayed on|dwell(?:ed)?)\b", ("web.dwell",), "long looks"),
    (r"\b(?:clicked|click(?:s)?)\b(?: on)?", ("web.click",), "clicks"),
    (r"\b(?:copied|copy|selected)\b", ("web.selection",), "copies"),
    (r"\b(?:watched|watching|videos?|listened to)\b", ("web.media", "web.visit"), "media"),
    (r"\b(?:visited|opened|browsed)\b", ("web.visit", "place.visit", "screen.page", "screen.app"), "visits"),
    (r"\b(?:apps?|programs?|worked in)\b", ("screen.app",), "apps"),
    (r"\b(?:said|talked about|discussed|mentioned|conversation)\b", ("said.quote", "moment.audio"), "conversations"),
]
SITES = {
    "youtube": "youtube.com", "wikipedia": "wikipedia.org", "github": "github.com", "reddit": "reddit.com",
    "stack overflow": "stackoverflow.com", "stackoverflow": "stackoverflow.com", "d2l": "learn.uwaterloo.ca",
    "learn": "learn.uwaterloo.ca", "piazza": "piazza.com", "google docs": "docs.google.com", "gmail": "mail.google.com",
    "twitter": "x.com", "x": "x.com", "arxiv": "arxiv.org", "chatgpt": "chatgpt.com", "claude": "claude.ai",
    "linkedin": "linkedin.com", "notion": "notion.so", "leetcode": "leetcode.com", "canvas": "instructure.com",
    # Apps, read from the screen: their program files.
    "vs code": "code.exe", "vscode": "code.exe", "visual studio code": "code.exe", "microsoft word": "winword.exe",
    "ms word": "winword.exe", "excel": "excel.exe", "powerpoint": "powerpnt.exe", "obsidian": "obsidian.exe",
    "slack": "slack.exe", "discord": "discord.exe", "the terminal": "windowsterminal.exe", "acrobat": "acrobat.exe",
    "sumatra": "sumatrapdf.exe", "sumatrapdf": "sumatrapdf.exe", "zotero": "zotero.exe",
    "the claude app": "claude.exe", "file explorer": "explorer.exe",
}  # fmt: skip


@dataclass
class Query:
    text: str  # what is left to search for
    since: float | None = None  # epoch seconds
    until: float | None = None
    types: tuple[str, ...] | None = None
    site: str | None = None  # a domain, matched as itself or any subdomain
    labels: list[str] = field(default_factory=list)  # what the filters were read as, for the person

    @property
    def filtered(self) -> bool:
        return self.since is not None or self.until is not None or self.types is not None or self.site is not None


def _midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), zone)


def parse(question: str, now: datetime) -> Query:
    """``now``: an aware time in the person's zone."""
    try:
        return _parse(question, now)
    except OverflowError as error:
        raise ValueError("The requested time range is outside the supported calendar") from error


def _parse(question: str, now: datetime) -> Query:
    zone = now.tzinfo if isinstance(now.tzinfo, ZoneInfo) else ZoneInfo("UTC")
    today = now.date()
    text = " " + question.strip() + " "
    query = Query("")
    window: tuple[datetime, datetime] | None = None
    label = None

    def take(pattern: str) -> re.Match | None:
        nonlocal text
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            text = text[: match.start()] + " " + text[match.end() :]
        return match

    def day_window(day: date) -> tuple[datetime, datetime]:
        return _midnight(day, zone), _midnight(day + timedelta(days=1), zone)

    # Ranges first, then single days, then parts of a day.
    if m := take(r"\b(?:in the )?(?:past|last) (\d+|a|an|one|two|three|four|five|six|seven|ten) (hour|day|week)s?\b"):
        count = int(m[1]) if m[1].isdigit() else NUMBERS[m[1].lower()]
        span = timedelta(**{f"{m[2].lower()}s": count})
        window, label = (now - span, now), f"past {count} {m[2].lower()}{'s' if count > 1 else ''}"
    elif m := take(r"\b(this|last) (week|month)\b"):
        which, unit = m[1].lower(), m[2].lower()
        if unit == "week":
            start = today - timedelta(days=today.weekday())
            if which == "last":
                window = (_midnight(start - timedelta(days=7), zone), _midnight(start, zone))
            else:
                window = (_midnight(start, zone), now)
        else:
            first = today.replace(day=1)
            if which == "last":
                previous = (first - timedelta(days=1)).replace(day=1)
                window = (_midnight(previous, zone), _midnight(first, zone))
            else:
                window = (_midnight(first, zone), now)
        label = f"{which} {unit}"
    elif m := take(r"\bsince (" + "|".join(WEEKDAYS) + r"|yesterday)\b"):
        name = m[1].lower()
        start = today - timedelta(days=1) if name == "yesterday" else _last(today, WEEKDAYS.index(name), True)
        window, label = (_midnight(start, zone), now), f"since {name}"
    elif m := take(r"\blast night\b"):
        yesterday = today - timedelta(days=1)
        window = (_midnight(yesterday, zone) + timedelta(hours=18), _midnight(today, zone) + timedelta(hours=5))
        label = "last night"
    else:
        day = None
        if m := take(r"\b(today|tonight)\b"):
            day, label = today, m[1].lower()
            if label == "tonight":
                start, end = PARTS["tonight"]
                window = (
                    _midnight(today, zone) + timedelta(hours=start),
                    _midnight(today, zone) + timedelta(hours=end),
                )
                day = None
        elif take(r"\byesterday\b"):
            day, label = today - timedelta(days=1), "yesterday"
        elif m := take(r"\b(\d+|a|one|two|three|four|five|six|seven) days? ago\b"):
            count = int(m[1]) if m[1].isdigit() else NUMBERS[m[1].lower()]
            day, label = today - timedelta(days=count), f"{count} day{'s' if count > 1 else ''} ago"
        elif m := take(r"\b(?:on |last )?(" + "|".join(WEEKDAYS) + r")\b"):
            day = _last(today, WEEKDAYS.index(m[1].lower()), "last" not in m[0].lower())
            label = day.strftime("%A %b %d").replace(" 0", " ")
        elif m := take(r"\b(\d{4})-(\d{2})-(\d{2})\b"):
            day = date(int(m[1]), int(m[2]), int(m[3]))
            label = day.isoformat()
        elif m := take(
            r"\b(?:on )?(?:(\d{1,2})(?:st|nd|rd|th)? (" + "|".join(x[:3] for x in MONTHS) + r")[a-z]*\.?"
            r"|(" + "|".join(x[:3] for x in MONTHS) + r")[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?)(?:,? (\d{4}))?\b"
        ):
            month = [x[:3] for x in MONTHS].index((m[2] or m[3]).lower()[:3]) + 1
            number = int(m[1] or m[4])
            year = int(m[5]) if m[5] else today.year
            try:
                day = date(year, month, number)
            except ValueError:
                day = None
            if day is not None and not m[5] and day > today:
                day = date(year - 1, month, number)
            label = day.isoformat() if day else None
        if day is not None:
            window = day_window(day)
            if part := take(r"\b(?:in the |this |that )?(morning|afternoon|evening|night)\b"):
                start, end = PARTS[part[1].lower()]
                window = (window[0] + timedelta(hours=start), window[0] + timedelta(hours=end))
                label = f"{label} {part[1].lower()}"
        elif part := take(r"\bthis (morning|afternoon|evening)\b"):
            start, end = PARTS[part[1].lower()]
            window = (_midnight(today, zone) + timedelta(hours=start), _midnight(today, zone) + timedelta(hours=end))
            label = f"this {part[1].lower()}"
    if window is not None:
        query.since, query.until = window[0].timestamp(), window[1].timestamp()
        query.labels.append(label or "")
    # Kinds of record: the first that matches.
    for pattern, types, name in KINDS:
        if take(pattern):
            query.types = types
            query.labels.append(name)
            break
    # A site: "on YouTube", "in D2L", "on example.com".
    names = sorted(SITES, key=len, reverse=True)
    if m := take(r"\b(?:on|in|from|at) (" + "|".join(re.escape(n) for n in names) + r")\b"):
        query.site = SITES[m[1].lower()]
        query.labels.append(query.site)
    elif m := take(r"\b(?:on|in|from|at) ((?:[a-z0-9-]+\.)+[a-z]{2,})\b"):
        query.site = m[1].lower().removeprefix("www.")
        query.labels.append(query.site)
    query.text = " ".join(text.split())
    return query


def _last(today: date, weekday: int, include_today: bool) -> date:
    """The most recent ``weekday`` on or before today (strictly before when not ``include_today``)."""
    back = (today.weekday() - weekday) % 7
    if back == 0 and not include_today:
        back = 7
    return today - timedelta(days=back)
