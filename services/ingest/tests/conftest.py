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
