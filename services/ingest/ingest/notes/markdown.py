"""Obsidian-flavoured Markdown, as much of it as linked notes need: front matter, the region
DayCube generates, and ``[[wikilinks]]``.

A generated note keeps the person's own writing: DayCube only ever rewrites the text between
``<!-- daycube:start -->`` and ``<!-- daycube:end -->``. A note the person made with the same name
gets the region appended once and is otherwise left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

START, END = "<!-- daycube:start -->", "<!-- daycube:end -->"
REGION = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
# [[target]], [[target|alias]], [[target#heading]], [[target#^block|alias]]; not ![[embeds]].
WIKILINK = re.compile(r"(?<!!)\[\[([^\[\]|#\n]+)(?:#[^\[\]|\n]*)?(?:\|([^\[\]\n]*))?\]\]")
# Characters Obsidian (and Windows) do not allow in a file name.
UNSAFE = re.compile(r'[*"\\/<>:|?#^\[\]\x00-\x1f]')


def file_name(title: str, limit: int = 100) -> str:
    name = " ".join(UNSAFE.sub(" ", title).split()).strip(" .")
    return name[:limit].rstrip(" .") or "Untitled"


def front_matter(text: str) -> dict[str, str]:
    """``key: value`` lines of the front matter (values as written; enough for DayCube's keys)."""
    match = FRONT.match(text)
    if not match:
        return {}
    out = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "-")):
            out[key.strip()] = value.strip().strip('"')
    return out


def links(text: str) -> list[tuple[str, str | None]]:
    """``(target, alias)`` of each wikilink, in order."""
    return [(m.group(1).strip(), (m.group(2) or "").strip() or None) for m in WIKILINK.finditer(text)]


def wikilink(path: str, label: str | None = None) -> str:
    """A link to a note by its path in the notes folder (without ``.md``), shown as ``label``
    (default: the note's name, not its folder)."""
    target = path.removesuffix(".md")
    shown = label or target.rsplit("/", 1)[-1]
    return f"[[{target}]]" if shown == target else f"[[{target}|{shown}]]"


def user_text(text: str) -> str:
    """The person's own writing: front matter and the generated region removed."""
    return REGION.sub("", FRONT.sub("", text, count=1)).strip()


def merge(existing: str | None, generated: str, front: dict[str, str]) -> str:
    """The note with its generated region replaced (or appended, or a new note with front matter)."""
    region = f"{START}\n{generated.strip()}\n{END}"
    if existing is None:
        head = "".join(f"{key}: {value}\n" for key, value in front.items())
        return f"---\n{head}---\n\n{region}\n"
    if REGION.search(existing):
        return REGION.sub(lambda _: region, existing, count=1)
    return existing.rstrip() + "\n\n" + region + "\n"


@dataclass(frozen=True)
class Chunk:
    heading: str
    text: str


def chunks(text: str, limit: int = 1500) -> list[Chunk]:
    """The person's writing split at headings and blank lines into searchable pieces."""
    out: list[Chunk] = []
    heading = ""
    for block in re.split(r"\n\s*\n", user_text(text)):
        block = block.strip()
        if not block:
            continue
        first = block.splitlines()[0]
        if first.startswith("#"):
            heading = first.lstrip("#").strip()
            block = "\n".join(block.splitlines()[1:]).strip()
            if not block:
                continue
        while block:
            out.append(Chunk(heading, block[:limit]))
            block = block[limit:]
    return out
