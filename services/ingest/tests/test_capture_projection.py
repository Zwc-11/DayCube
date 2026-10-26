"""Capture text caching keeps sparse snapshots, midnight inheritance and file changes exact."""

import json
import os
from pathlib import Path

import pytest
import zstandard
from ingest.cache import SegmentedLRU
from ingest.capture import read_capture, synthesize
from ingest.vault import Vault, text_lines

DAY, PREVIOUS = "2026-10-25", "2026-10-24"


def block(text: str | None, title: str = "", url: str = "") -> dict:
    return {"doc": "document", "title": title, "url": url, "blocks": [{"id": "p", "tag": "p", "text": text}]}


def path(vault: Vault, day: str = DAY) -> Path:
    return vault.root / "capture" / day / "blocks.jsonl"


def test_sparse_snapshots_inherit_title_and_text_across_midnight(vault: Vault) -> None:
    vault.write_lines(path(vault, PREVIOUS), [block("Earlier text", "Original", "old")])
    vault.write_lines(path(vault), [block(None, url="new"), block("New text", "Renamed"), block(None, url="last")])
    for _ in range(2):
        result = read_capture(vault, DAY)
        assert result.docs == {"document": ("last", "Renamed")}
        assert result.texts == {("document", "p"): ("p", "New text")}
    vault.write_lines(path(vault), [block(None, url="next")])
    assert read_capture(vault, DAY).docs == {"document": ("next", "Original")}


def test_unchanged_files_are_not_reparsed_and_results_do_not_share_maps(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault.write_lines(path(vault, PREVIOUS), [block("Previous", "Title")])
    vault.write_lines(path(vault), [block(None)])
    result = read_capture(vault, DAY)
    result.docs.clear()
    result.texts.clear()
    original = synthesize._iter_records
    calls = []

    def counted(source: Path):
        calls.append(source)
        yield from original(source)

    monkeypatch.setattr(synthesize, "_iter_records", counted)
    result = read_capture(vault, DAY)
    assert result.texts[("document", "p")] == ("p", "Previous")
    assert path(vault) not in calls and path(vault, PREVIOUS) not in calls
    vault.append_lines(path(vault), [block("Appended")])
    assert read_capture(vault, DAY).texts[("document", "p")] == ("p", "Appended")
    assert path(vault) in calls and path(vault, PREVIOUS) not in calls


def test_same_size_write_uses_the_vault_write_version(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    vault.write_lines(path(vault), [block("A")])
    monkeypatch.setattr("ingest.vault.file_stamp", lambda _: (1, 100, 2))
    monkeypatch.setattr(synthesize, "file_stamp", lambda _: (1, 100, 2))
    assert read_capture(vault, DAY).texts[("document", "p")][1] == "A"
    vault.write_lines(path(vault), [block("B")])
    assert read_capture(vault, DAY).texts[("document", "p")][1] == "B"


def test_external_replacement_and_file_removal_invalidate(vault: Vault) -> None:
    vault.write_lines(path(vault), [block("A")])
    assert read_capture(vault, DAY).texts
    replacement = path(vault).with_suffix(".new")
    replacement.write_text(json.dumps(block("B")) + "\n", encoding="utf-8")
    os.replace(replacement, path(vault))
    assert read_capture(vault, DAY).texts[("document", "p")][1] == "B"
    path(vault).unlink()
    assert read_capture(vault, DAY).texts == {}


def test_compacted_capture_uses_its_physical_stamp_and_streams_unicode(vault: Vault) -> None:
    logical = path(vault)
    logical.parent.mkdir(parents=True)
    packed = logical.with_suffix(".jsonl.zst")

    def write(text: str) -> None:
        packed.write_bytes(zstandard.ZstdCompressor().compress(json.dumps(block(text)).encode()))

    write("中文 first")
    assert read_capture(vault, DAY).texts[("document", "p")][1] == "中文 first"
    write("中文 second")
    assert read_capture(vault, DAY).texts[("document", "p")][1] == "中文 second"
    vault.write_lines(logical, [block("Plain takes precedence")])
    assert read_capture(vault, DAY).texts[("document", "p")][1] == "Plain takes precedence"


def test_corrupt_records_and_valid_unterminated_tail_keep_existing_behavior(vault: Vault) -> None:
    logical = path(vault)
    logical.parent.mkdir(parents=True)
    logical.write_text(json.dumps(block("First")) + '\n{"broken":\n42\n' + json.dumps(block("Last")), encoding="utf-8")
    assert read_capture(vault, DAY).texts[("document", "p")][1] == "Last"
    assert len(list(text_lines(logical))) == 4


def test_truncated_compacted_capture_remains_an_explicit_read_failure(vault: Vault) -> None:
    logical = path(vault)
    logical.parent.mkdir(parents=True)
    packed = logical.with_suffix(".jsonl.zst")
    encoded = zstandard.ZstdCompressor().compress(json.dumps(block("Frame integrity")).encode())
    packed.write_bytes(encoded[:-3])
    with pytest.raises(zstandard.ZstdError):
        read_capture(vault, DAY)


def test_a_partly_consumed_plain_stream_releases_its_file_when_closed(vault: Vault) -> None:
    logical = path(vault)
    vault.write_lines(logical, [block("First"), block("Second")])
    lines = text_lines(logical)
    assert json.loads(next(lines))["blocks"][0]["text"] == "First"
    lines.close()
    logical.unlink()  # Windows refuses this while a normal Python reader still owns the file.


def test_oversized_projection_is_not_retained(vault: Vault) -> None:
    vault.capture_texts = SegmentedLRU(256)
    vault.write_lines(path(vault), [block("Text" * 1000)])
    for _ in range(2):
        assert read_capture(vault, DAY).texts[("document", "p")][1] == "Text" * 1000
        assert vault.capture_texts.size <= 256 and len(vault.capture_texts) == 0


def test_a_file_changed_during_projection_is_not_cached_under_an_old_stamp(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault.write_lines(path(vault), [block("Before")])
    original = synthesize._iter_records
    once = [False]

    def appending(source: Path):
        yield from original(source)
        if source == path(vault) and not once[0]:
            once[0] = True
            vault.append_lines(source, [block("After")])

    monkeypatch.setattr(synthesize, "_iter_records", appending)
    assert read_capture(vault, DAY).texts[("document", "p")][1] == "Before"
    assert vault.capture_texts.get(path(vault)) is None
    assert read_capture(vault, DAY).texts[("document", "p")][1] == "After"
