"""Reading the window under the gaze in any app (capture/screen.py), against a simulated Windows
and OCR: which window, what is sent to the receiver, how reading, scrolling, privacy and time per
app come out, and how the vault turns it into units, notes and the graph."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from capture.api import create_capture_app
from capture.screen import (
    SCROLL_ORIGIN,
    Known,
    OcrLine,
    ScreenReader,
    Window,
    app_url,
    blocks_of,
    changed,
    identify,
    normal,
    thumbnail,
    trigrams,
    why_not,
)
from fastapi.testclient import TestClient
from ingest.capture import capture_units, read_capture
from ingest.config import Settings
from ingest.graph.extract import extract
from ingest.notes.write import Notes
from ingest.store import LifeStore
from ingest.vault import Vault

MONITOR = (0, 0, 1920, 1080)
PDF = Window(0x1234, "sumatrapdf.exe", "paper.pdf - SumatraPDF", (100, 50, 1200, 1000), "SUMATRA_PDF_FRAME")
OPERA = Window(0x2222, "opera.exe", "Hash table - Wikipedia - Opera", (0, 0, 1920, 1040), "Chrome_WidgetWin_1")
TEXTS = ["People remember places better", "than lists, and a walk through a", "street is an index."]
STATE = {"tracking": True, "calibrated": True, "error_px": 30.0, "monitor": [1920, 1080]}


def page_lines(shift: float = 0.0) -> list[OcrLine]:
    """A heading, a three-line paragraph and a two-line one, in window image pixels."""
    lines = [OcrLine("Chapter 3 Memory", (60, 100 - shift, 400, 34), 3)]
    for i, text in enumerate(TEXTS):
        lines.append(OcrLine(text, (60, 200 + 28.8 * i - shift, 700 if i < 2 else 300, 20), len(text.split())))
    lines += [
        OcrLine("Method of loci is old.", (60, 400 - shift, 500, 20), 5),
        OcrLine("Orators used it.", (60, 428.8 - shift, 400, 20), 3),
    ]
    return lines


class FakeSystem:
    def __init__(self) -> None:
        self.windows: list[Window] = [PDF]  # topmost first
        self.front: Window | None = PDF
        self.idle = 0.0
        self.version = 0  # what the windows show; a new version is new pixels

    def monitor(self) -> tuple[int, int, int, int]:
        return MONITOR

    def window_at(self, x: int, y: int) -> Window | None:
        for window in self.windows:
            left, top, width, height = window.rect
            if left <= x < left + width and top <= y < top + height:
                return window
        return None

    def foreground(self) -> Window | None:
        return self.front

    def capture(self, rect: tuple[int, int, int, int]) -> np.ndarray:
        rng = np.random.default_rng(self.version)
        return rng.integers(0, 256, (rect[3], rect[2], 4), dtype=np.uint8)

    def idle_s(self) -> float:
        return self.idle


class FakeOcr:
    name = "fake"

    def __init__(self) -> None:
        self.lines = page_lines()
        self.calls = 0

    def read(self, image: np.ndarray, key: str | None = None) -> list[OcrLine]:
        assert image.shape == (1000, 1200, 4)
        self.calls += 1
        return list(self.lines)


class Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


def logged(vault: Vault, name: str) -> list[dict]:
    records = []
    for path in sorted((vault.root / "capture").glob(f"*/{name}")):
        with path.open(encoding="utf-8") as stream:
            records.extend(json.loads(line) for line in stream)
    return records


def reader_for(vault: Vault, receiver: object) -> tuple[ScreenReader, FakeSystem, FakeOcr, Clock]:
    system, ocr, clock = FakeSystem(), FakeOcr(), Clock()
    reader = ScreenReader(receiver, system, ocr, vault.root / "capture" / "screen.json", clock=clock)
    receiver.screen = reader  # as `python -m capture run` does; stepped by the test, not its thread
    return reader, system, ocr, clock


def look(client: TestClient, auth: dict[str, str], x: float, y: float) -> None:
    """The live gaze point at monitor pixel (x, y), as the tracker streams it."""
    point = {"t": datetime.now(UTC).isoformat(), "x": x / 1920, "y": y / 1080}
    assert client.post("/gaze/live", json=point, headers=auth).status_code == 200


def fixation(t: datetime, x: float, y: float, seconds: float = 0.25) -> dict:
    return {
        "id": uuid.uuid4().hex, "t": t.isoformat(), "t_end": (t + timedelta(seconds=seconds)).isoformat(),
        "x": x / 1920, "y": y / 1080, "samples": 8,
    }  # fmt: skip


# -- pieces ----------------------------------------------------------------------------------------


def test_ocr_lines_become_paragraphs_with_ids_from_their_text() -> None:
    blocks = blocks_of(page_lines())
    assert [b["text"] for b in blocks] == [
        "Chapter 3 Memory", " ".join(TEXTS), "Method of loci is old. Orators used it.",
    ]  # fmt: skip
    assert [b["tag"] for b in blocks] == ["h", "p", "p"]
    assert blocks[1]["rect"] == [60, 200, 700, 77.6] and len(blocks[1]["lines"]) == 3
    assert blocks[1]["words"] == sum(len(t.split()) for t in TEXTS)
    # Scrolled by two lines and read again a little differently (spaces, one letter): the
    # paragraphs are recognised and the scroll measured.
    known = {
        f"k{i}": Known(normal(b["text"]), trigrams(normal(b["text"])), b["rect"][0], 1000 + b["rect"][1], 1, 1)
        for i, b in enumerate(blocks)
    }
    noisy = page_lines(shift=57.6)
    noisy[2] = OcrLine("than lists,and a walk thruogh a", noisy[2].rect, 7)
    noisy[0] = OcrLine("Chapter 3  Memory", noisy[0].rect, 3)
    ids, scroll = identify(blocks_of(noisy), known, 1000, 2)
    assert ids == ["k0", "k1", "k2"] and scroll == 1057.6
    ids, scroll = identify(
        blocks_of(page_lines()) + [{"text": "Something else entirely", "rect": [0, 900, 9, 9]}], known, 1000, 2
    )
    assert ids == ["k0", "k1", "k2", None] and scroll == 1000
    # Two columns side by side stay two paragraphs; text repeated in a window gets its own id.
    columns = blocks_of([
        OcrLine("left column one", (0, 0, 400, 20), 3), OcrLine("right column one", (600, 0, 400, 20), 3),
        OcrLine("left column two", (0, 28, 400, 20), 3), OcrLine("right column two", (600, 28, 400, 20), 3),
        OcrLine("OK", (0, 300, 40, 20), 1), OcrLine("OK", (0, 500, 40, 20), 1),
    ])  # fmt: skip
    assert [b["text"] for b in columns][:2] == ["left column one left column two", "right column one right column two"]
    assert columns[2]["id"] != columns[3]["id"] and columns[3]["id"].endswith("-2")
    edited = blocks_of(page_lines())
    edited[2]["rect"][1] += 40  # moved on its own (a line inserted above it): an edit, not a scroll
    edited[1]["rect"][1] += 13
    assert identify(edited, known, 1000, 2)[1] is None


def test_which_windows_are_never_read() -> None:
    assert why_not(PDF) is None
    assert why_not(OPERA) == "browser"
    assert why_not(Window(1, "1password.exe", "1Password", (0, 0, 900, 700))) == "excluded"
    assert why_not(Window(1, "lockapp.exe", "Windows Default Lock Screen", (0, 0, 1920, 1080))) == "locked"
    assert why_not(Window(1, "explorer.exe", "", (0, 1040, 1920, 40), "Shell_TrayWnd")) == "shell"
    assert why_not(Window(1, "notepad.exe", "notes.txt - InPrivate", (0, 0, 900, 700))) == "private"
    assert why_not(Window(1, "slack.exe", "general", (0, 0, 900, 700)), frozenset({"slack.exe"})) == "excluded"
    assert why_not(Window(1, "python.exe", "DayCube gaze ring", (0, 0, 1920, 1080))) == "daycube"
    assert app_url("code.exe", "main.py - daycube") == "app://code.exe/main.py%20-%20daycube"


def test_a_blinking_cursor_is_no_change_but_a_scroll_is() -> None:
    page = np.full((400, 800, 4), 255, dtype=np.uint8)
    for row in range(20, 380, 24):  # lines of "text"
        page[row : row + 12, 40:760:3] = 0
    cursor = page.copy()
    cursor[100:116, 300:302] = 0
    scrolled_page = np.roll(page, -24 * 3 + 5, axis=0)
    assert not changed(thumbnail(page), thumbnail(cursor))
    assert changed(thumbnail(page), thumbnail(scrolled_page))
    assert changed(None, thumbnail(page))


# -- through the receiver ---------------------------------------------------------------------------


def test_reading_a_pdf_in_another_app_is_followed_line_by_line(vault: Vault, auth: dict[str, str]) -> None:
    """The window under the gaze is read, sent as a page, and fixations on it are decoded into lines
    and a read paragraph exactly as on a web page; its text is in blocks.jsonl and the app in front
    in windows.jsonl."""
    app = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True)
    with TestClient(app) as client:
        receiver = app.state.receiver
        reader, system, ocr, clock = reader_for(vault, receiver)
        assert client.post("/gaze", json={"state": STATE, "fixations": []}, headers=auth).status_code == 200
        reader.step()
        assert ocr.calls == 0 and reader.skipped == "no gaze"  # nothing is read without the eyes on it
        look(client, auth, 100 + 400, 50 + 220)
        reader.step()
        assert ocr.calls == 1 and reader.state()["reading"] == {"app": "sumatrapdf.exe", "title": PDF.title}
        (snapshot,) = logged(vault, "blocks.jsonl")
        assert snapshot["url"] == "app://sumatrapdf.exe/paper.pdf%20-%20SumatraPDF" and snapshot["doc"].startswith(
            "scr-"
        )
        assert snapshot["tab"] == snapshot["window"] == -0x1234
        assert snapshot["geometry"] | {} == {
            "vx": 100, "vy": 50, "scale": 1.0, "inner_w": 1200, "inner_h": 1000, "screen_w": 1920, "screen_h": 1080,
            "primary": True, "exact": True, "scroll_y": SCROLL_ORIGIN, "scroll_h": SCROLL_ORIGIN + 1000,
        }  # fmt: skip
        paragraph = snapshot["blocks"][1]
        assert paragraph["text"] == " ".join(TEXTS)
        assert paragraph["lines"][0]["rect"] == [60, SCROLL_ORIGIN + 200, 700, 20]  # page coordinates
        (front,) = logged(vault, "windows.jsonl")
        assert front["kind"] == "window" and front["app"] == "sumatrapdf.exe" and front["title"] == PDF.title

        # Nothing changed on screen: no second OCR, only a heartbeat (live, not stored).
        clock.offset = 2.5
        reader.step()
        assert ocr.calls == 1 and len(logged(vault, "blocks.jsonl")) == 1

        # The paragraph read line by line, then the eyes go on to the next one.
        fixations, t = [], datetime.now(UTC) - timedelta(seconds=7)
        for line in page_lines()[1:4]:
            left, top, width, height = line.rect
            for k in range(6):
                fixations.append(fixation(t, 100 + left + width * (k + 0.5) / 6, 50 + top + height / 2))
                t += timedelta(seconds=0.3)
        for k in range(3):
            fixations.append(fixation(t, 100 + 60 + 120 * (k + 0.5), 50 + 410))
            t += timedelta(seconds=0.3)
        assert client.post("/gaze", json={"state": STATE, "fixations": fixations}, headers=auth).status_code == 200
        status = client.get("/status").json()
        assert status["line_decoding"]["on_line"] >= 18
        assert status["line_decoding"]["page"]["host"] == "sumatrapdf.exe"
        assert status["screen"]["reads_10min"] == 1 and status["screen"]["apps_10min"] == ["sumatrapdf.exe"]
        assert "screen" in status["capabilities"] and status["screen"]["engine"] == "fake"
    (read,) = logged(vault, "reading.jsonl")
    assert read["url"] == snapshot["url"] and read["title"] == PDF.title and read["block"] == paragraph["id"]
    assert [entry["text"] for entry in read["lines"]] == TEXTS and read["coverage"] == 1.0
    placed = [f for f in logged(vault, "gaze.jsonl") if f.get("doc")]
    assert len(placed) == 21 and {f["doc"] for f in placed} == {snapshot["doc"]}


def test_a_scroll_keeps_paragraphs_and_their_place_and_sends_new_text_only(vault: Vault, auth: dict[str, str]) -> None:
    app = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True)
    with TestClient(app) as client:
        receiver = app.state.receiver
        reader, system, ocr, clock = reader_for(vault, receiver)
        client.post("/gaze", json={"state": STATE, "fixations": []}, headers=auth)
        look(client, auth, 500, 300)
        reader.step()
        first = logged(vault, "blocks.jsonl")[-1]
        # Scrolled down two lines: a new paragraph comes in at the bottom.
        system.version += 1
        ocr.lines = page_lines(shift=57.6) + [OcrLine("A new paragraph at the end.", (60, 700, 600, 20), 6)]
        clock.offset = 1.1
        look(client, auth, 500, 300)
        reader.step()
        assert ocr.calls == 2
        second = logged(vault, "blocks.jsonl")[-1]
        assert second["doc"] == first["doc"] and second["geometry"]["scroll_y"] == SCROLL_ORIGIN + 57.6
        by_id = {b["id"]: b for b in second["blocks"]}
        old = [by_id[b["id"]] for b in first["blocks"]]
        assert all(b["text"] is None and b["lines"] is None for b in old)  # same text, same place on the page
        assert [b["rect"][1] for b in old] == [b["rect"][1] - 57.6 for b in first["blocks"]]  # moved on screen
        (new,) = [b for b in second["blocks"] if b["id"] not in {x["id"] for x in first["blocks"]}]
        assert new["text"] == "A new paragraph at the end." and new["lines"][0]["rect"][1] == SCROLL_ORIGIN + 57.6 + 700
        # The receiver still has each paragraph's lines where they were on the page.
        layout = receiver.attention.layouts[(first["doc"], first["blocks"][1]["id"])]
        assert [line.y for line in layout][0] == SCROLL_ORIGIN + 200
        # A read finding nothing new (a video, an animation) slows the next ones down.
        system.version += 1
        clock.offset = 2.2
        look(client, auth, 500, 300)
        reader.step()
        assert ocr.calls == 3 and reader.current.backoff == 2.0
        system.version += 1
        clock.offset = 3.3
        reader.step()
        assert ocr.calls == 3  # changed, but within the slower pace


def test_browsers_private_windows_and_pauses_are_never_read(vault: Vault, auth: dict[str, str]) -> None:
    app = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True)
    with TestClient(app) as client:
        receiver = app.state.receiver
        reader, system, ocr, clock = reader_for(vault, receiver)
        client.post("/gaze", json={"state": STATE, "fixations": []}, headers=auth)
        look(client, auth, 500, 300)
        reader.step()
        assert ocr.calls == 1
        # Opera comes in front of the PDF: its page is the extension's.
        system.windows, system.front = [OPERA, PDF], OPERA
        reader.step()
        assert ocr.calls == 1 and reader.skipped == "browser" and reader.current is None
        assert logged(vault, "windows.jsonl")[-1] == {**logged(vault, "windows.jsonl")[-1], "app": "opera.exe",
                                                      "browser": True}  # fmt: skip
        assert "title" not in logged(vault, "windows.jsonl")[-1]
        # A password manager's window: neither its text nor its title.
        vault_window = Window(0x3333, "bitwarden.exe", "My vault - Bitwarden", (0, 0, 1000, 800))
        system.windows, system.front = [vault_window], vault_window
        reader.step()
        assert ocr.calls == 1 and reader.skipped == "excluded"
        assert (
            logged(vault, "windows.jsonl")[-1]["private"] is True and "title" not in logged(vault, "windows.jsonl")[-1]
        )
        # An app excluded by the person, and reading turned off.
        system.windows, system.front = [PDF], PDF
        (vault.root / "capture" / "screen.json").write_text('{"exclude_apps": ["SumatraPDF.exe"]}', encoding="utf-8")
        reader.step()
        assert ocr.calls == 1 and reader.skipped == "excluded"
        (vault.root / "capture" / "screen.json").write_text('{"enabled": false}', encoding="utf-8")
        reader.step()
        assert ocr.calls == 1 and reader.skipped == "off" and logged(vault, "windows.jsonl")[-1]["why"] == "off"
        (vault.root / "capture" / "screen.json").unlink()
        # Paused: nothing read, nothing logged; the pause closes the app in front.
        client.post("/pause", headers=auth)
        assert logged(vault, "windows.jsonl")[-1]["why"] == "paused"
        before = len(logged(vault, "windows.jsonl"))
        system.front = Window(0x4444, "notepad.exe", "todo.txt - Notepad", (0, 0, 900, 700))
        reader.step()
        assert ocr.calls == 1 and reader.skipped == "paused"
        assert len(logged(vault, "windows.jsonl")) == before  # the reader logs nothing while paused
        assert receiver.gaze_now(10) is None


def test_away_and_back(vault: Vault, auth: dict[str, str]) -> None:
    app = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True)
    with TestClient(app):
        receiver = app.state.receiver
        reader, system, ocr, clock = reader_for(vault, receiver)
        reader.step()  # no gaze (no tracker): the app in front is still logged
        system.idle = 400.0
        reader.step()
        reader.step()
        system.idle = 0.0
        reader.step()
    kinds = [(r["kind"], r.get("why")) for r in logged(vault, "windows.jsonl")]
    assert kinds == [("window", None), ("away", "idle"), ("window", None)]
    away = logged(vault, "windows.jsonl")[1]
    assert (datetime.fromisoformat(away["t"]) - datetime.fromisoformat(away["since"])).total_seconds() == pytest.approx(
        400
    )


def test_the_window_read_wins_over_the_browser_page_beneath_it(vault: Vault, auth: dict[str, str]) -> None:
    """Opera's page fills the screen and the PDF window sits on top of it: a fixation inside the PDF
    belongs to the PDF. Fixations from before the eyes left it, which arrive late, still do."""
    geometry = {
        "vx": 0.0, "vy": 80.0, "scale": 1.0, "inner_w": 1920.0, "inner_h": 960.0, "screen_w": 1920.0,
        "screen_h": 1080.0, "primary": True, "exact": True, "scroll_y": 0.0, "scroll_h": 4000.0,
    }  # fmt: skip
    tab = {"tab": 7, "window": 1, "url": "https://example.com/paper", "title": "A paper"}
    page = {
        "id": uuid.uuid4().hex, "t": datetime.now(UTC).isoformat(), "kind": "blocks", "doc": "doc-1",
        "geometry": geometry, "blocks": [{"id": "b1", "tag": "p", "text": "Web text", "rect": [0, 0, 1900, 900]}],
        **tab,
    }  # fmt: skip
    app = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True)
    with TestClient(app) as client:
        receiver = app.state.receiver
        reader, system, ocr, clock = reader_for(vault, receiver)
        client.post("/events", json=[page, {**page, "id": uuid.uuid4().hex, "kind": "focus", "tab": 7, "window": 1,
                                            "url": tab["url"], "title": tab["title"]}], headers=auth)  # fmt: skip
        client.post("/gaze", json={"state": STATE, "fixations": []}, headers=auth)
        look(client, auth, 500, 300)
        reader.step()
        doc = reader.current.doc
        t = datetime.now(UTC) - timedelta(seconds=1)
        reader.leave(clock())  # the eyes leave the PDF now; this fixation from a second ago arrives after
        client.post("/gaze", json={"state": STATE, "fixations": [fixation(t, 500, 300)]}, headers=auth)
        later = fixation(datetime.now(UTC) + timedelta(seconds=0.5), 500, 300)
        client.post("/gaze", json={"state": STATE, "fixations": [later]}, headers=auth)
    docs = [f.get("doc") for f in logged(vault, "gaze.jsonl")]
    assert docs == [doc, "doc-1"]


def test_dwells_in_apps_run_the_hook_but_are_not_sent_to_the_extension(vault: Vault) -> None:
    receiver = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True).state.receiver
    event = {"tab": -5, "doc": "scr-abc", "block": "o1", "dwell_s": 9.0, "reading_s": 2.0, "url": "app://x.exe/y",
             "title": "y", "text": "words"}  # fmt: skip
    receiver._signal(event)
    assert receiver.signals == []
    receiver._signal({**event, "doc": "doc-1", "tab": 7, "url": "https://example.com/"})
    assert [s["doc"] for s in receiver.signals] == ["doc-1"]


# -- in the vault ------------------------------------------------------------------------------------


def write_app_day(vault: Vault, day: str) -> None:
    folder = vault.root / "capture" / day
    folder.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(f"{day}T14:00:00+00:00")

    def at(minutes: float) -> str:
        return (start + timedelta(minutes=minutes)).isoformat()

    windows = [
        {"id": "w1", "t": at(0), "kind": "window", "app": "sumatrapdf.exe", "title": PDF.title,
         "url": app_url("sumatrapdf.exe", PDF.title)},
        {"id": "w2", "t": at(12), "kind": "window", "app": "opera.exe", "browser": True},
        {"id": "w3", "t": at(20), "kind": "window", "app": "code.exe", "title": "screen.py - daycube - Visual Studio Code",
         "url": app_url("code.exe", "screen.py - daycube - Visual Studio Code")},
        {"id": "w4", "t": at(50), "kind": "away", "why": "idle", "since": at(45)},
        {"id": "w5", "t": at(60), "kind": "window", "app": "bitwarden.exe", "private": True},
        {"id": "w6", "t": at(61), "kind": "away", "why": "paused"},
    ]  # fmt: skip
    read = {
        "id": "r1-read", "kind": "read", "t": at(3), "t_end": at(4), "tab": -0x1234, "doc": "scr-1",
        "url": app_url("sumatrapdf.exe", PDF.title), "title": PDF.title, "block": "o1", "tag": "p",
        "line_count": 3, "coverage": 1.0, "p": 0.9, "lines": [{"line": i, "text": t} for i, t in enumerate(TEXTS)],
    }  # fmt: skip
    (folder / "windows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in windows), encoding="utf-8")
    (folder / "reading.jsonl").write_text(json.dumps(read) + "\n", encoding="utf-8")


def test_time_in_apps_and_text_read_in_them_become_units_notes_and_graph(vault: Vault, tmp_path: Path) -> None:
    day = "2026-10-21"
    write_app_day(vault, day)
    capture = read_capture(vault, day)
    spans = [(a.app, a.title, round(a.seconds / 60)) for a in capture.apps]
    assert spans == [
        ("sumatrapdf.exe", PDF.title, 12),  # until the browser came in front
        ("code.exe", "screen.py - daycube - Visual Studio Code", 25),  # until the last input before going away
        ("bitwarden.exe", "", 1),  # private: the app, never its window
    ]
    units = capture_units(vault, day, capture)
    texts = {u["type"]: [] for u in units}
    for u in units:
        texts[u["type"]].append(u["text"])
    assert texts["screen.app"] == [
        f"Used SumatraPDF for 12 min: {PDF.title}",
        "Used Visual Studio Code for 25 min: screen.py - daycube - Visual Studio Code",
        "Used bitwarden for 1 min",
    ]
    assert texts["web.read"] == [f"Read in {PDF.title} (SumatraPDF): " + " ".join(TEXTS)]
    graph = extract(day, units, capture)
    sites = {entity[1]: entity[2] for _, entity, _ in graph.mentions if entity[0] == "site"}
    assert sites == {"sumatrapdf.exe": "SumatraPDF", "code.exe": "Visual Studio Code", "bitwarden.exe": "bitwarden"}
    pages = {entity[1] for _, entity, _ in graph.mentions if entity[0] == "page"}
    assert pages == {
        app_url("sumatrapdf.exe", PDF.title),
        app_url("code.exe", "screen.py - daycube - Visual Studio Code"),
    }

    store = LifeStore(vault)
    daily = Path(Notes(store).write_day(day)[-1]).read_text(encoding="utf-8")
    assert "## Apps (38 min)" in daily
    assert "- Visual Studio Code · 25 min: " in daily and "- SumatraPDF · 12 min: " in daily
    assert "- bitwarden · 1 min\n" in daily
    assert "## Read\n" in daily and " ".join(TEXTS)[:40] in daily

    def texts_of(ids: list[int]) -> list[tuple[str, str]]:
        with store.connect() as db:
            return [tuple(db.execute("SELECT type, text FROM units WHERE id = ?", (i,)).fetchone()) for i in ids]

    assert texts_of(store.keyword('"walk" "street"', k=5)) == [("web.read", texts["web.read"][0])]
    in_code = texts_of(store.listing(site="code.exe", k=5))
    assert [kind for kind, _ in in_code] == ["screen.app"]


def test_questions_about_apps_filter_to_them() -> None:
    from ingest.ask.query import parse

    query = parse("which apps did I use yesterday", datetime(2026, 10, 22, 12, tzinfo=UTC))
    assert query.types == ("screen.app",)
    query = parse("what did I read in VS Code", datetime(2026, 10, 22, 12, tzinfo=UTC))
    assert query.site == "code.exe" and "web.read" in query.types


def test_the_doctor_says_whether_and_what_the_screen_reader_reads() -> None:
    from capture.doctor import _screen

    running = {"running": True, "engine": "windows", "language": "zh-Hans-CN + en-US", "enabled": True, "reading": {"app": "code.exe", "title": "x"},
               "skipped": None, "away": False, "reads_10min": 14, "apps_10min": ["code.exe", "sumatrapdf.exe"],
               "ocr_ms": 182.0, "error": None}  # fmt: skip
    ((verdict, name, detail),) = _screen({"screen": running})
    assert (verdict, name) == ("pass", "screen reading")
    assert detail == (
        "windows OCR (zh-Hans-CN + en-US), 14 reads in 10 min of code.exe, sumatrapdf.exe, 182 ms each; "
        "reading code.exe now"
    )
    ((verdict, _, detail),) = _screen({"screen": {**running, "reading": None, "skipped": "browser"}})
    assert detail.endswith("not reading now: browser")
    ((verdict, _, detail),) = _screen(
        {"screen": {"running": False, "error": "no OCR: windows: RuntimeError: no OCR language"}}
    )
    assert verdict == "warn" and "uv sync --project services/capture-win" in detail
    ((verdict, _, detail),) = _screen({"screen": {"running": False, "reason": "started with --no-screen"}})
    assert verdict == "info"
    ((verdict, _, detail),) = _screen({"screen": {**running, "enabled": False}})
    assert verdict == "info" and "screen on" in detail
    ((verdict, _, detail),) = _screen({})
    assert verdict == "warn" and "older" in detail


def test_screen_command_turns_reading_off_and_excludes_apps(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from capture.__main__ import main

    monkeypatch.setenv("DAYCUBE_VAULT_ROOT", str(vault.root))
    main(["screen", "off", "--exclude", "Slack", "--exclude", "discord.exe"])
    saved = json.loads((vault.root / "capture" / "screen.json").read_text(encoding="utf-8"))
    assert saved == {"enabled": False, "exclude_apps": ["discord.exe", "slack.exe"]}
    main(["screen", "on", "--include", "slack.exe"])
    saved = json.loads((vault.root / "capture" / "screen.json").read_text(encoding="utf-8"))
    assert saved == {"enabled": True, "exclude_apps": ["discord.exe"]}
    assert (
        capsys.readouterr().out.splitlines()[-1] == "Reading the window under your gaze: on. Never read: discord.exe."
    )


def test_a_retitled_window_stays_one_page_until_its_content_is_replaced(vault: Vault, auth: dict[str, str]) -> None:
    app = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True)
    with TestClient(app) as client:
        receiver = app.state.receiver
        reader, system, ocr, clock = reader_for(vault, receiver)
        client.post("/gaze", json={"state": STATE, "fixations": []}, headers=auth)
        look(client, auth, 500, 300)
        reader.step()
        first = logged(vault, "blocks.jsonl")[-1]
        # "● paper.pdf": retitled, same text on screen: the same page under its new title.
        retitled = Window(PDF.hwnd, PDF.app, "● " + PDF.title, PDF.rect, PDF.cls)
        system.windows, system.front = [retitled], retitled
        system.version += 1
        clock.offset = 1.1
        look(client, auth, 500, 300)
        reader.step()
        second = logged(vault, "blocks.jsonl")[-1]
        assert second["doc"] == first["doc"] and second["title"] == retitled.title
        assert second["url"] == app_url(PDF.app, retitled.title)
        assert all(b["text"] is None for b in second["blocks"])
        # Another file in the same window: nothing matches, so a new page from the top.
        ocr.lines = [OcrLine("A different document altogether", (60, 200, 600, 20), 4)]
        system.version += 1
        clock.offset = 3.5  # the last read found nothing new, so the next waits 2 s
        look(client, auth, 500, 300)
        reader.step()
        third = logged(vault, "blocks.jsonl")[-1]
        assert third["doc"] != first["doc"] and third["geometry"]["scroll_y"] == SCROLL_ORIGIN
        assert third["blocks"][0]["text"] == "A different document altogether"
        assert reader.current.doc == third["doc"] and reader.current.generation == 1


# -- the right recogniser for the script on screen ---------------------------------------------------


def test_a_chinese_windows_reads_english_windows_with_the_english_recogniser() -> None:
    from capture.screen import Scripts, join_words, tidy

    english = [
        OcrLine("Windows PowerShell", (0, 0, 300, 20), 2),
        OcrLine("PS E:\\DAYCUBE> uv sync", (0, 30, 400, 20), 3),
    ]
    misread = [OcrLine("Windows PowerSheII", (0, 0, 300, 20), 2), OcrLine("卧", (500, 0, 20, 20), 1),
               OcrLine("PS E ： \\DAYCUBE> uv sync", (0, 30, 400, 20), 3)]  # fmt: skip
    chinese = [OcrLine("今天的作业在周五之前提交", (0, 0, 300, 20), 12)]
    calls = {"cjk": 0, "latin": 0}
    screen = {"text": misread, "latin": english}

    def cjk(image: np.ndarray) -> list[OcrLine]:
        calls["cjk"] += 1
        return screen["text"]

    def latin(image: np.ndarray) -> list[OcrLine]:
        calls["latin"] += 1
        return screen["latin"]

    scripts = Scripts(cjk, latin)
    image = np.zeros((10, 10, 4), np.uint8)
    assert scripts.read(image, "terminal") == english and scripts.modes["terminal"][0] == "latin"
    for _ in range(4):  # judged: only the English recogniser until the next check
        assert scripts.read(image, "terminal") == english
    assert calls == {"cjk": 1, "latin": 5}
    scripts.read(image, "terminal")
    assert calls == {"cjk": 2, "latin": 6}  # the fifth read later: checked again
    # A Chinese page: the Chinese recogniser alone.
    screen["text"] = chinese
    assert scripts.read(image, "wechat") == chinese and scripts.modes["wechat"][0] == "cjk"
    # A Chinese interface around English text: each line from the recogniser of its script.
    screen["text"] = [OcrLine("文件 编辑 选择 查看 帮助", (0, 0, 300, 20), 5)] + misread[2:] * 3
    screen["latin"] = [
        OcrLine("x1x2 garbled", (0, 0, 300, 20), 2),
        OcrLine("PS E:\\DAYCUBE> uv sync", (0, 30, 400, 20), 3),
    ]
    lines = scripts.read(image, "editor")
    assert scripts.modes["editor"][0] == "both"
    assert [line.text for line in lines] == ["文件 编辑 选择 查看 帮助", "PS E:\\DAYCUBE> uv sync"]
    # Only one recogniser installed: it reads everything.
    assert Scripts(None, latin).read(image, "x") == screen["latin"]
    # Words: no spaces inside Chinese, full-width punctuation in Latin text as ASCII.
    assert join_words(["今", "天", "OCR", "works", "很", "好"]) == "今天 OCR works 很好"
    assert tidy("E ： \\DAYCUBE 10 ． 0 ． 28000") == "E : \\DAYCUBE 10 . 0 . 28000"
    assert tidy("今天，明天。") == "今天，明天。"


# -- restarting on new code ---------------------------------------------------------------------------


def test_restart_stops_the_receiver_and_its_tracker_but_never_itself() -> None:
    from capture.restart import ours, restart

    found = [
        {"pid": 10, "parent": 1, "command": r'"E:\DAYCUBE\services\capture-win\.venv\Scripts\pythonw.exe" -m capture run --receive --gaze'},
        {"pid": 11, "parent": 10, "command": r'"C:\uv\python\cpython-3.11\pythonw.exe" -m capture run --receive --gaze'},
        {"pid": 12, "parent": 11, "command": r'"E:\DAYCUBE\services\gaze\.venv\Scripts\pythonw.exe" -m gaze track --vault E:\DAYCUBE\vault --receiver http://127.0.0.1:7700 --supervised'},
        {"pid": 20, "parent": 2, "command": "python -m capture restart"},  # the venv launcher of this process
        {"pid": 21, "parent": 20, "command": "python -m capture restart"},  # this process
        {"pid": 30, "parent": 3, "command": "python -m gaze calibrate"},
        {"pid": 31, "parent": 3, "command": "python -m capture doctor"},
        {"pid": 32, "parent": 3, "command": "python -m capture --receive"},  # started the older way
        {"pid": 33, "parent": 3, "command": "python -m capture_tools run"},
    ]  # fmt: skip
    assert ours(found, 21) == [10, 11, 12, 32]

    answers = iter([{"code": "old"}, {"code": "old"}, None, None, None, {"code": "new"}])
    ended, started = [], []
    stopped, status = restart(
        ["pythonw.exe", "-m", "capture", "run"], lambda: next(answers), started.append,
        find=lambda: found, end=ended.append, sleep=lambda _: None, me=21,
    )  # fmt: skip
    assert stopped == [10, 11, 12, 32] and ended == [[10, 11, 12, 32]]
    assert started == [["pythonw.exe", "-m", "capture", "run"]] and status == {"code": "new"}


def test_the_code_stamp_follows_the_files(tmp_path: Path) -> None:
    from capture.restart import code_stamp

    (tmp_path / "api.py").write_bytes(b"x = 1\n")
    first = code_stamp(tmp_path)
    (tmp_path / "api.py").write_bytes(b"x = 1\r\n")  # the same file checked out on Windows
    assert code_stamp(tmp_path) == first
    (tmp_path / "api.py").write_bytes(b"x = 2\n")
    assert code_stamp(tmp_path) != first


def test_the_receiver_reports_its_code_and_doctor_compares_it(vault: Vault) -> None:
    from capture import doctor
    from capture.restart import code_stamp

    receiver = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True).state.receiver
    assert receiver.status()["code"] == code_stamp()
    results = {name: verdict for verdict, name, _ in doctor.checks({"paused": False, "code": "0ld"}, vault.root,
                                                                    __import__("zoneinfo").ZoneInfo("UTC"),
                                                                    datetime.now(UTC), None)}  # fmt: skip
    assert results["receiver code"] == doctor.FAIL
