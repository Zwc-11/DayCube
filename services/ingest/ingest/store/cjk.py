"""Chinese, Japanese and Korean text in the keyword index.

FTS5's tokenizer makes one token of a whole run of CJK characters, so a phrase inside a sentence
is never found. Units and note chunks that hold such text are therefore indexed a second time
with every CJK character as its own token (``spaced``), in ``units_cjk`` and ``note_chunks_cjk``.
A question's CJK runs are searched there as pairs of neighbouring characters (``pairs``), each a
two-token phrase: the usual way to search CJK text without a word segmenter. A record that holds
more of the question's pairs ranks higher, and one that holds the whole phrase holds them all.

The tables are contentless (the text stays in ``units`` and ``note_chunks``) and need SQLite
3.43 for deletes; on an older SQLite they are left out and CJK phrases are not found, as before.
"""

from __future__ import annotations

import re
import sqlite3

AVAILABLE = sqlite3.sqlite_version_info >= (3, 43, 0)
DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS units_cjk USING fts5(text, content='', contentless_delete=1);
CREATE VIRTUAL TABLE IF NOT EXISTS note_chunks_cjk USING fts5(text, content='', contentless_delete=1);
"""
# Kana, CJK ideographs (with extension A and the compatibility block) and Hangul syllables.
RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]+")


def spaced(text: str) -> str | None:
    """``text`` with each CJK character set apart as its own token; None when it holds none."""
    return RUN.sub(lambda run: f" {' '.join(run.group())} ", text) if RUN.search(text) else None


def pairs(run: str) -> list[str]:
    """A CJK run as its neighbouring pairs: "机器学习" -> 机器, 器学, 学习 (a lone character stays)."""
    return [run] if len(run) == 1 else [run[i : i + 2] for i in range(len(run) - 1)]


def split(word: str) -> list[str]:
    """A word as search terms: its CJK runs as pairs, what lies between them as it is."""
    out, at = [], 0
    for run in RUN.finditer(word):
        out += [word[at : run.start()], *pairs(run.group())]
        at = run.end()
    return [part for part in (*out, word[at:]) if part]


def expression(terms: list[str]) -> str:
    """The FTS5 expression for CJK terms: any of them, each as a phrase of its characters."""
    return " OR ".join(f'"{" ".join(term)}"' for term in terms if RUN.fullmatch(term))
