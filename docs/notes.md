# Linked notes

DayCube writes what you attended to as plain Markdown in `vault/notes/`, a folder Obsidian opens
as a vault (graph view, backlinks, search). DayCube's own interface can then stay visual (places
and time in 3D, little text), and your notes stay files you own.

## What is written

| Note | One per | Holds |
| --- | --- | --- |
| `Daily/<date>.md` | day | what was on screen (with time), apps used (time per app and its main windows), read (with line coverage, in pages and in app windows), stared at, looked at, copied, searched (and the page each search led to), what is due, places visited |
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

## The graph: the viewer's home page

The viewer opens on every note in `vault/notes` and every `[[link]]` between them, as Obsidian's
graph view draws a vault (`GET /notes/graph`). On a wide screen the newest recorded day's note is
open beside it.

| Pane | Shows |
| --- | --- |
| Left | The notes folder (folders with how many notes they hold, days newest first, each note with its kind's colour). While searching: the results |
| Middle | The graph. A dot per note, sized by its links and coloured by kind (days, sources, courses, your notes, notes not written yet); the open note glows amber with its links. Hover a note to light it and its neighbours; names appear as you zoom in, the most linked first, never on top of each other |
| Right | The open note, rendered: `[[links]]` open the note they name (a link to a note not written yet is dimmed), the generated region is marked, front matter shows as properties, **Open in Obsidian** opens the file there; then what links to it (**Linked from**) and what it links to (**Links to**). A daily note steps to the day before or after, and a day with places has **3D** |

Opening a note anywhere (a dot, the list, a link, a search result) shows it on the right and brings
it into view on the graph; `?note=<path>` is its address, so back and forward work. Drag the
background to pan, scroll or pinch to zoom, drag a dot to pull it and its neighbours. The legend
hides or shows a kind of note; **Linked to open note** shows only the open note and its neighbours
(Obsidian's local graph). The note's edge drags wider or narrower. On a phone the list slides over
the graph and the note covers it.

Search sits in the top bar (`/` or `Ctrl K`): results update as you type, across every day, with the
time, kind and site words of [search.md](search.md#question-filters), and the notes they are in
light up on the graph. Enter or a click opens the result in its note (a page's source note, else
the day's daily note) with the line marked. **Days** opens the calendar (`?view=days`); a day there
opens in 3D, or, with only computer capture, as its note on the graph (`?date=<day>&view=notes`,
also where **Notes** in a day's 3D view goes).

Notes hold text from the web, so the viewer renders Markdown itself, to React elements: no HTML from
a note is ever inserted into the page, and links open only http(s), mailto and obsidian URLs.

**How it is drawn.** The layout is a live force simulation (`apps/viewer/src/lib/graphSim.ts`):
every note repels every other by Barnes–Hut (O(n log n) a tick, no repulsion past 120 + 14√n so lone
notes stay near the rest), links are springs weakened at busy notes as d3-force weakens them, and a
pull toward the middle keeps clusters together. It runs in a worker, a tick per frame, and stops at
rest; positions are kept per browser, so the graph opens as you left it and a new note appears next
to what it links to. The canvas draws only when something moved, batching dots by kind (ten fills)
and edges by state (three strokes). With 2,600 notes a full redraw takes about 4 ms and the layout
comes to rest about 6 s after a first load (`apps/viewer/e2e/knowledge.spec.ts`; the vault comes
from `scripts/build-notes-fixture.py`, a fictional term of linked notes).

**What the server does.** `GET /notes/graph` writes the daily note of any recorded day that has none
yet, then returns each note's path and kind, the links to notes not written yet (one per name,
matched as Obsidian matches them), and every link as `[from, to, count]`. It is built from the
`notes` and `note_links` tables in two queries and kept until a note changes (ETag, an empty 304
for a client holding the current copy); the page asks again every minute while it is visible. A
day's note is refreshed from its records when it opens (`GET /day/{date}/notes`), and today's every
minute. `GET /note?path=` returns one note with its links resolved and its backlinks (`note_links`
keeps every link of every note, generated or written, with the note it opens).

## When notes are written

- While the capture receiver runs, it refreshes today's notes (and yesterday's, until its last
  changes are in) every 10 minutes when the capture logs changed
  (`services/capture-win/capture/librarian.py`; `python -m capture run --no-notes` turns it off).
- On demand: `uv run daycube notes --date 2026-10-22` (or `--all`), or `POST /notes/{date}` with
  the vault token. `GET /notes` lists the notes as last read.

Code: `services/ingest/ingest/notes/` (Markdown, writing, `read.py` and `graph.py` for the viewer),
`services/ingest/ingest/store/notes.py` (reading back), `apps/viewer/src/components/knowledge/`
(the page: `KnowledgePage`, `GraphCanvas`, `FileTree`, `NotePanel`), `apps/viewer/src/lib/`
(`noteGraph.ts`, `graphSim.ts` and its worker, `markdown.tsx`). Tests:
`services/ingest/tests/test_notes.py`, `test_knowledge.py`, `apps/viewer/e2e/knowledge.spec.ts`.
