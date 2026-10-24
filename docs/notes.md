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
- Your `[[links]]` join the knowledge graph (only those in your own writing: the generated
  regions' links restate what the graph already holds). They resolve as Obsidian resolves them: a path names
  that file, a bare name the note with that file name (the shortest path wins). A link to a
  generated note connects to the page, course or day it stands for; a link to a note not written
  yet still makes a node, as in Obsidian. `.obsidian/` and other hidden folders are skipped.

## Reading a day in the viewer

Click a day on the calendar. A day with only computer capture opens as its notes; a day with places
opens in 3D, and **Notes** in the top bar switches to its notes (**3D** switches back).

| Pane | Shows |
| --- | --- |
| Left | The day's notes: its daily note, the sources and courses it links to, and your own notes that link to it. While searching: the results |
| Middle | The note, rendered: `[[links]]` open the note they name (a link to a note not written yet is dimmed), the generated region is marked, front matter shows as properties, and **Open in Obsidian** opens the file there |
| Right | The day's local graph (click a node with a note to open it), the note's backlinks and outgoing links |

Search sits in the top bar (`/` or `Ctrl K`): results update as you type, for this day or all days,
with the time, kind and site words of [search.md](search.md#question-filters). Enter or a click opens
the result in its note (a page's source note, else the day's daily note) with the line marked.

Notes hold text from the web, so the viewer renders Markdown itself, to React elements: no HTML from
a note is ever inserted into the page, and links open only http(s), mailto and obsidian URLs.

Opening a day writes its notes if its records changed since they were last written (`GET
/day/{date}/notes`), so a day is never older than its capture logs; today's page refreshes every
minute. `GET /note?path=` returns one note with its links resolved and its backlinks
(`note_links` keeps every link of every note, generated or written, with the note it opens).

## When notes are written

- While the capture receiver runs, it refreshes today's notes (and yesterday's, until its last
  changes are in) every 10 minutes when the capture logs changed
  (`services/capture-win/capture/librarian.py`; `python -m capture run --no-notes` turns it off).
- On demand: `uv run daycube notes --date 2026-10-22` (or `--all`), or `POST /notes/{date}` with
  the vault token. `GET /notes` lists the notes as last read.

Code: `services/ingest/ingest/notes/` (Markdown, writing, and `read.py` for the viewer),
`services/ingest/ingest/store/notes.py` (reading back), `apps/viewer/src/components/knowledge/`
and `apps/viewer/src/lib/markdown.tsx` (the page). Tests: `services/ingest/tests/test_notes.py`,
`test_knowledge.py`, `apps/viewer/e2e/knowledge.spec.ts`.
