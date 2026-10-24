"""Questions' time, kind and site words become filters, and leave the rest to search for."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import CAPTURE_DAY, capture_session
from fastapi.testclient import TestClient
from ingest.ask.query import READ, parse
from ingest.ask.retrieve import retrieve
from ingest.ask.service import ask
from ingest.store import LifeStore
from ingest.vault import Vault

TORONTO = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 23, 15, 30, tzinfo=TORONTO)  # a Friday afternoon


@pytest.mark.parametrize("question", ["past 999999999999 days", "999999999999 days ago", "on 9999-12-31"])
def test_out_of_calendar_ranges_fail_explicitly(question: str) -> None:
    with pytest.raises(ValueError, match="outside the supported calendar"):
        parse(question, NOW)


def test_calendar_overflow_returns_a_client_error(client: TestClient) -> None:
    response = client.get("/search", params={"q": "past 999999999999 days"})
    assert response.status_code == 400
    assert "supported calendar" in response.json()["detail"]


def local(value: float) -> str:
    return datetime.fromtimestamp(value, TORONTO).strftime("%Y-%m-%d %H:%M")


@pytest.mark.parametrize(
    ("question", "since", "until", "label"),
    [
        ("what did I read yesterday", "2026-10-22 00:00", "2026-10-23 00:00", "yesterday"),
        ("hash tables yesterday afternoon", "2026-10-22 12:00", "2026-10-22 17:00", "yesterday afternoon"),
        ("what was I doing this morning", "2026-10-23 05:00", "2026-10-23 12:00", "this morning"),
        ("notes from today", "2026-10-23 00:00", "2026-10-24 00:00", "today"),
        ("on Monday", "2026-10-19 00:00", "2026-10-20 00:00", "Monday Oct 19"),
        ("on Friday", "2026-10-23 00:00", "2026-10-24 00:00", "Friday Oct 23"),  # today is Friday
        ("last Friday", "2026-10-16 00:00", "2026-10-17 00:00", "Friday Oct 16"),
        ("in the past 3 days", "2026-10-20 15:30", "2026-10-23 15:30", "past 3 days"),
        ("last week", "2026-10-12 00:00", "2026-10-19 00:00", "last week"),
        ("this week", "2026-10-19 00:00", "2026-10-23 15:30", "this week"),
        ("since Tuesday", "2026-10-20 00:00", "2026-10-23 15:30", "since tuesday"),
        ("on October 22", "2026-10-22 00:00", "2026-10-23 00:00", "2026-10-22"),
        ("Dec 3", "2025-12-03 00:00", "2025-12-04 00:00", "2025-12-03"),  # in the future this year: last year
        ("2026-10-01", "2026-10-01 00:00", "2026-10-02 00:00", "2026-10-01"),
        ("last night", "2026-10-22 18:00", "2026-10-23 05:00", "last night"),
        ("2 days ago", "2026-10-21 00:00", "2026-10-22 00:00", "2 days ago"),
        ("last month", "2026-09-01 00:00", "2026-10-01 00:00", "last month"),
    ],
)
def test_time_words(question: str, since: str, until: str, label: str) -> None:
    q = parse(question, NOW)
    assert (local(q.since), local(q.until), q.labels[0]) == (since, until, label)


def test_kinds_sites_and_what_is_left() -> None:
    q = parse("What did I read about hash tables yesterday on Wikipedia?", NOW)
    assert q.types == READ and q.site == "wikipedia.org" and q.text == "What did I about hash tables ?"
    q = parse("what did I search for in D2L", NOW)
    assert q.types == ("web.search",) and q.site == "learn.uwaterloo.ca" and q.text == "what did I"
    assert parse("videos on youtube last week", NOW).site == "youtube.com"
    assert parse("anything on docs.python.org", NOW).site == "docs.python.org"
    plain = parse("When did we decide the demo order?", NOW)
    assert not plain.filtered and plain.text == "When did we decide the demo order?"
    assert parse("What did I learn about graphs", NOW).site is None  # "learn" is D2L only after on/in


def test_filters_narrow_retrieval(vault: Vault) -> None:
    capture_session(vault)
    store = LifeStore(vault)
    now = datetime(2026, 10, 23, 10, 0, tzinfo=TORONTO)
    # All filters, no words: a listing of yesterday's searches.
    listed = retrieve(store, "what did I search yesterday", now=now)
    assert [r["type"] for r in listed] == ["web.search"] and listed[0]["matched"] == ["listing"]
    # Words within a kind: the read, not the search that shares "chaining".
    found = retrieve(store, "what did I read about chaining yesterday", now=now)
    assert found[0]["type"] == "web.read" and {r["type"] for r in found} <= set(READ)
    # A site: only what happened on Wikipedia.
    on_wiki = retrieve(store, "linear probing on wikipedia", now=now)
    assert on_wiki and all("wikipedia" in r["text"] for r in on_wiki)
    # Another day: nothing.
    assert retrieve(store, "what did I search on Monday", now=now) == []


def test_ask_reports_its_filters_and_a_miss(vault: Vault) -> None:
    capture_session(vault)
    store = LifeStore(vault)
    now = datetime(2026, 10, 23, 10, 0, tzinfo=TORONTO)
    answer = ask(store, None, "what did I copy yesterday", now=now)
    assert answer["filters"]["labels"] == ["yesterday", "copies"] and answer["filters"]["search"] == "what did I"
    assert "Linear probing" in answer["sentences"][0]["text"] and answer["message"] is None
    miss = ask(store, None, "what did I read about quantum yesterday", now=now)
    assert miss["message"] == "Nothing matched “quantum”; showing yesterday, reading instead."
    assert miss["day"] == CAPTURE_DAY
