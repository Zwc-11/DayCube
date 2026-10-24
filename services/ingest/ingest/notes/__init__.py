"""Linked notes: an Obsidian-compatible folder written from the vault, and read back into search.

``Notes`` (the writer) is imported on first use: it needs ``ingest.store``, which itself reads the
notes' Markdown with this package's ``markdown`` module."""

from .markdown import chunks, front_matter, links, merge, user_text, wikilink

__all__ = ["Notes", "chunks", "front_matter", "links", "merge", "user_text", "wikilink"]


def __getattr__(name: str) -> object:
    if name == "Notes":
        from .write import Notes

        return Notes
    raise AttributeError(name)
