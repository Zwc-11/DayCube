# Linked notes

DayCube writes what you attended to as plain Markdown in `vault/notes/`, a folder Obsidian opens
as a vault (graph view, backlinks, search). DayCube's own interface can then stay visual (places
and time in 3D, little text), and your notes stay files you own.

## What is written

| Note | One per | Holds |
| --- | --- | --- |
| `Daily/<date>.md` | day | what was on screen (with time), read (with line coverage), stared at, looked at, copied, searched (and the page each search led to), what is due, places visited |
| `Sources/<page title>.md` | page that held attention: read, stared at, looked at, copied from, reached from a search, or 2 minutes or more on screen | everything done on it across days, the searches that found it, the courses it names |
| `Courses/<code>.md` | course code seen (`CS 136`) | deadlines, the pages that name it |

Each line links to the note of the page or course it concerns (`[[Sources/Hash table -
Wikipedia|Hash table - Wikipedia]]`, `[[Courses/CS 136|CS 136]]`). Everything comes from the vault
database ([search.md](search.md): units, entities, links), so nothing is guessed: a deadline is a
"due October 30 at 11:59 PM" the eyes were on, a course a code in a title or in text read.

## Your writing is kept

- DayCube only rewrites the text between `<!-- daycube:start -->` and `<!-- daycube:end -->`.
  Write anywhere else in a note and it stays.
- A note made by you with the same name gets the region appended once and is otherwise untouched.
- Each generated note names its entity in its front matter (`daycube: page <url>`,
  `daycube: course CS 136`, `daycube: day 2026-10-22`), so a note you rename or move keeps
  receiving its updates, and links to it follow.

## Your notes are searched and linked

The folder is read back into the vault database whenever a note changes:

- Only your own writing is indexed (generated regions repeat records the database already
  holds), in pieces split at headings and blank lines. Ask finds it like any record, as
  `note.text`, when a question is not limited to a day or time.
- Your `[[links]]` join the knowledge graph. They resolve as Obsidian resolves them: a path names
  that file, a bare name the note with that file name (the shortest path wins). A link to a
  generated note connects to the page, course or day it stands for; a link to a note not written
  yet still makes a node, as in Obsidian. `.obsidian/` and other hidden folders are skipped.

## When notes are written

- While the capture receiver runs, it refreshes today's notes (and yesterday's, until its last
  changes are in) every 10 minutes when the capture logs changed
  (`services/capture-win/capture/librarian.py`; `python -m capture run --no-notes` turns it off).
- On demand: `uv run daycube notes --date 2026-10-22` (or `--all`), or `POST /notes/{date}` with
  the vault token. `GET /notes` lists the notes as last read.

Code: `services/ingest/ingest/notes/` (Markdown and writing), `services/ingest/ingest/store/notes.py`
(reading back). Tests: `services/ingest/tests/test_notes.py`.
