import array
import hashlib
import re
import shutil
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.ask.retrieve import STOP_WORDS
from ingest.config import PROJECT_ROOT, Settings
from ingest.store import LifeStore
from ingest.vault import Vault

sys.path.insert(0, str(PROJECT_ROOT / "services/capture-win"))

DAY = "2026-10-20"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    store = Vault(tmp_path / "vault")
    store.initialize()
    store.seed()
    return store


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None)
    with TestClient(create_app(settings)) as connection:
        yield connection


@pytest.fixture
def auth(vault: Vault) -> dict[str, str]:
    return {"Authorization": f"Bearer {vault.token}"}


@pytest.fixture
def store(vault: Vault) -> LifeStore:
    """Keyword search only, as in CI without the vector extra."""
    return LifeStore(vault)


class WordEmbedder:
    """Deterministic stand-in for bge-small: each word that is not a stop word, after a small
    synonym table, adds a signed hashed component. Texts that share (synonymous) words point the
    same way, which is enough to test storage, reuse, filters and fusion; it says nothing about
    model quality."""

    SYNONYMS = {"presenting": "show", "citation": "evidence", "panel": "card", "espresso": "coffee"}

    def __init__(self, name: str = "test/words-v1", dim: int = 64) -> None:
        self.name, self.dim = name, dim
        self.embedded: list[str] = []

    def _vector(self, text: str) -> bytes:
        values = [0.0] * self.dim
        for word in re.findall(r"[a-z]+", text.casefold()):
            if word in STOP_WORDS:
                continue
            word = self.SYNONYMS.get(word, word)
            h = hashlib.blake2b(word.encode(), digest_size=8).digest()
            values[h[0] % self.dim] += 1.0 if h[1] & 1 else -1.0
        norm = max(sum(v * v for v in values) ** 0.5, 1e-9)
        return array.array("b", (max(-127, min(127, round(127 * v / norm))) for v in values)).tobytes()

    def passages(self, texts: Sequence[str]) -> list[bytes]:
        self.embedded.extend(texts)
        return [self._vector(text) for text in texts]

    def query(self, text: str) -> bytes:
        return self._vector(text)


@pytest.fixture
def embedder() -> WordEmbedder:
    return WordEmbedder()


@pytest.fixture
def vector_store(vault: Vault, embedder: WordEmbedder) -> LifeStore:
    return LifeStore(vault, embedder)


def clone_day(vault: Vault, source: str, target: str) -> None:
    """A second valid day: the source day's files with every date rewritten."""
    destination = vault.day_path(target, must_exist=False)
    shutil.copytree(vault.day_path(source), destination, ignore=shutil.ignore_patterns("*.sqlite*", "index.*"))
    for path in destination.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".geojson"}:
            path.write_text(path.read_text(encoding="utf-8").replace(source, target), encoding="utf-8")


CAPTURE_DAY = "2026-10-22"


def capture_session(vault: Vault, day: str = CAPTURE_DAY) -> None:
    """A morning of computer capture as the receiver writes it (``services/capture-win``): a course
    page read with the eyes, a search, a click through to Wikipedia, a copy, an idle break and a
    video. Times are Toronto (-04:00)."""
    import json as _json

    def t(clock: str) -> str:
        return f"{day}T{clock}-04:00"

    learn = "https://learn.uwaterloo.ca/d2l/le/content/123/viewContent/9"
    google = "https://www.google.com/search?q=linear+probing+vs+chaining"
    wiki = "https://en.wikipedia.org/wiki/Hash_table"
    youtube = "https://www.youtube.com/watch?v=KyUTuwz_b7Q"
    browser = [
        {"id": "f1-0000001", "t": t("09:00:00"), "kind": "focus", "tab": 1, "window": 1, "url": learn,
         "title": "CS 136 Assignment 3 - Hash tables"},
        {"id": "s1-0000001", "t": t("09:05:00"), "kind": "search", "tab": 1, "window": 1, "url": google,
         "title": "linear probing vs chaining - Google Search", "engine": "google",
         "query": "linear probing vs chaining"},
        {"id": "p1-0000001", "t": t("09:05:00"), "kind": "page", "tab": 1, "window": 1, "url": google,
         "title": "linear probing vs chaining - Google Search", "transition": "generated", "qualifiers": []},
        {"id": "c1-0000001", "t": t("09:05:20"), "kind": "click", "tab": 1, "window": 1, "url": google,
         "title": "linear probing vs chaining - Google Search", "href": wiki, "text": "Hash table - Wikipedia"},
        {"id": "p2-0000001", "t": t("09:05:21"), "kind": "page", "tab": 1, "window": 1, "url": wiki,
         "title": "Hash table - Wikipedia", "transition": "link", "qualifiers": []},
        {"id": "x1-0000001", "t": t("09:10:00"), "kind": "selection", "tab": 1, "window": 1, "url": wiki,
         "title": "Hash table - Wikipedia", "text": "Linear probing", "copied": True},
        {"id": "i1-0000001", "t": t("09:12:00"), "kind": "idle", "state": "idle"},
        {"id": "i2-0000001", "t": t("09:20:00"), "kind": "idle", "state": "active"},
        {"id": "f2-0000001", "t": t("09:25:00"), "kind": "focus", "tab": 2, "window": 1, "url": youtube,
         "title": "Hash Tables - Computerphile - YouTube"},
        {"id": "m1-0000001", "t": t("09:25:10"), "kind": "media", "tab": 2, "window": 1, "url": youtube,
         "title": "Hash Tables - Computerphile - YouTube", "state": "playing", "position_s": 0, "duration_s": 600},
        {"id": "m2-0000001", "t": t("09:30:10"), "kind": "media", "tab": 2, "window": 1, "url": youtube,
         "title": "Hash Tables - Computerphile - YouTube", "state": "paused", "position_s": 300, "duration_s": 600},
        {"id": "f3-0000001", "t": t("09:31:00"), "kind": "focus", "tab": None},
    ]  # fmt: skip
    geometry = {"vx": 0, "vy": 87, "scale": 1.5, "inner_w": 1700, "inner_h": 860, "screen_w": 1707,
                "screen_h": 960, "primary": True, "exact": True, "scroll_y": 0, "scroll_h": 3000}  # fmt: skip
    lines = [
        "Open addressing stores every key in the table itself and probes",
        "for the next free slot; separate chaining keeps a list per bucket.",
    ]
    blocks = [{
        "id": "b-0000001", "t": t("09:00:05"), "kind": "blocks", "tab": 1, "window": 1, "url": learn,
        "title": "CS 136 Assignment 3 - Hash tables", "doc": "doc-learn-1", "geometry": geometry,
        "blocks": [
            {"id": "h1", "tag": "h1", "text": "Assignment 3: Hash tables", "words": 4, "rect": [40, 100, 600, 40]},
            {"id": "p1", "tag": "p", "text": "Due Friday October 30 at 11:59 PM. Submit on Marmoset.", "words": 10,
             "rect": [40, 160, 900, 24]},
            {"id": "p2", "tag": "p", "text": " ".join(lines), "words": 24, "rect": [40, 200, 900, 48],
             "lines": [{"rect": [40, 200, 880, 24], "text": lines[0]}, {"rect": [40, 224, 870, 24], "text": lines[1]}]},
        ],
    }]  # fmt: skip
    gaze = []
    for i in range(8):  # 4 s on the heading
        gaze.append({"id": f"g-h1-{i:04d}", "t": t(f"09:00:{10 + i // 2:02d}.{(i % 2) * 5}00"),
                     "t_end": t(f"09:00:{10 + i // 2:02d}.{(i % 2) * 5 + 4}90"), "x": 0.1, "y": 0.15, "samples": 15,
                     "tab": 1, "doc": "doc-learn-1", "page": [60, 110], "block": "h1", "p": 0.9})  # fmt: skip
    for i in range(4):  # 2 s on the due date: a dwell below, not a look
        gaze.append({"id": f"g-p1-{i:04d}", "t": t(f"09:00:{20 + i // 2:02d}.{(i % 2) * 5}00"),
                     "t_end": t(f"09:00:{20 + i // 2:02d}.{(i % 2) * 5 + 4}90"), "x": 0.1, "y": 0.2, "samples": 15,
                     "tab": 1, "doc": "doc-learn-1", "page": [60, 170], "block": "p1", "p": 0.8})  # fmt: skip
    attention = [
        {"id": "dw1-start", "kind": "dwell", "dwell": "dw1", "phase": "start", "t": t("09:00:20"),
         "t_end": t("09:00:32"), "dwell_s": 12.0, "reading_s": 2.4, "fixations": 30, "tab": 1, "url": learn,
         "title": "CS 136 Assignment 3 - Hash tables", "doc": "doc-learn-1", "block": "p1", "tag": "p",
         "text": "Due Friday October 30 at 11:59 PM. Submit on Marmoset."},
        {"id": "dw1-end", "kind": "dwell", "dwell": "dw1", "phase": "end", "t": t("09:00:20"),
         "t_end": t("09:00:36"), "dwell_s": 16.0, "reading_s": 2.4, "fixations": 40, "tab": 1, "url": learn,
         "title": "CS 136 Assignment 3 - Hash tables", "doc": "doc-learn-1", "block": "p1", "tag": "p",
         "text": "Due Friday October 30 at 11:59 PM. Submit on Marmoset."},
    ]  # fmt: skip
    reading = [{
        "id": "rd1-0000001-read", "kind": "read", "t": t("09:01:00"), "t_end": t("09:01:09"), "tab": 1, "url": learn,
        "title": "CS 136 Assignment 3 - Hash tables", "doc": "doc-learn-1", "block": "p2", "tag": "p",
        "line_count": 2, "lines": [{"line": 0, "text": lines[0], "seconds": 4.1, "passes": 1},
                                   {"line": 1, "text": lines[1], "seconds": 3.9, "passes": 1}],
        "coverage": 1.0, "path": [[0.0, 0], [4.2, 1]], "offset_px": 3.0, "p": 0.93,
    }]  # fmt: skip
    folder = vault.root / "capture" / day
    folder.mkdir(parents=True, exist_ok=True)
    for name, records in (("browser.jsonl", browser), ("blocks.jsonl", blocks), ("gaze.jsonl", gaze),
                          ("attention.jsonl", attention), ("reading.jsonl", reading)):  # fmt: skip
        with (folder / name).open("w", encoding="utf-8") as stream:
            for record in records:
                stream.write(_json.dumps(record) + "\n")
    with (folder / "browser.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("not json at all\n")  # a damaged line must not hide the rest
