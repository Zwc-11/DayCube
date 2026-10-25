"""Write a fictional vault of linked notes, for seeing and testing the graph at a real size.

A made-up term of a student's days, in the format DayCube writes (``notes/write.py``): a daily
note per day linking to the pages read and the courses seen, a source note per page (some read on
many days, most once), course notes listing their pages, and the student's own notes linking to
days, pages, each other and to notes not written yet. Deterministic for a seed. Nothing in it is
real: the pages are generic reference titles and the courses invented codes.

    uv run python scripts/build-notes-fixture.py <notes folder> [--days 45] [--sources 420] [--seed 7]
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

COURSES = ["CS 240", "CS 241", "MATH 239", "STAT 230", "ECON 101", "PHYS 121", "AFM 101", "CO 250"]
TOPICS = [
    "Hash table", "Binary heap", "Red-black tree", "Skip list", "Bloom filter", "Trie", "B-tree", "AVL tree",
    "Dijkstra's algorithm", "Graph coloring", "Max flow", "Linear programming", "Simplex method", "Duality",
    "Markov chain", "Bayes' theorem", "Central limit theorem", "Poisson process", "Brownian motion",
    "Black-Scholes model", "Monte Carlo method", "Gradient descent", "Backpropagation", "Attention mechanism",
    "Transformer architecture", "Gaussian splatting", "Neural radiance field", "Kalman filter", "SLAM",
    "Barnes-Hut simulation", "Force-directed graph drawing", "Fourier transform", "Eigenvalues",
    "Singular value decomposition", "Regular expression", "Context-free grammar", "LR parser", "Lambda calculus",
    "Supply and demand", "Opportunity cost", "Game theory", "Nash equilibrium", "Elasticity", "Inflation",
    "Double-entry bookkeeping", "Balance sheet", "Cash flow statement", "Present value", "Bond duration",
    "Newton's laws", "Kinematics", "Harmonic oscillator", "Momentum", "Work and energy",
]
SITES = [
    ("Wikipedia", "en.wikipedia.org/wiki/{slug}"),
    ("cp-algorithms", "cp-algorithms.com/{slug}"),
    ("Khan Academy", "www.khanacademy.org/{slug}"),
    ("MIT OpenCourseWare", "ocw.mit.edu/{slug}"),
    ("YouTube", "www.youtube.com/watch?v={slug}"),
    ("Stack Overflow", "stackoverflow.com/questions/{slug}"),
]
IDEAS = [
    "Revision plan", "Midterm prep", "Graph view ideas", "Reading list", "Questions for office hours",
    "Summer research", "Study group", "Interview prep", "Paper notes", "Project log", "Weekly review",
    "Things to try", "Formula sheet", "Open problems", "Notes on attention", "Notes on splats",
]
FOLDERS = ["Ideas", "Projects/DayCube", "Projects/Research", "Reading"]
MISSING = ["Ask the TA", "Cuckoo hashing", "Read later", "Write this up", "Proof sketch", "Office hours"]


def front(**fields: str) -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in fields.items()) + "---\n"


def link(path: str, label: str | None = None) -> str:
    target = path.removesuffix(".md")
    return f"[[{target}|{label}]]" if label else f"[[{target}]]"


def build(root: Path, days: int, sources: int, seed: int) -> int:
    rng = random.Random(seed)
    pages: list[tuple[str, str, str]] = []  # (title, url, site)
    seen: set[str] = set()
    while len(pages) < sources:
        topic = rng.choice(TOPICS)
        site, pattern = rng.choice(SITES)
        suffix = "" if rng.random() < 0.4 else rng.choice([" - examples", " (lecture)", " explained", " - part 2", " proof", " notes"])
        title = f"{topic}{suffix} - {site}"
        if title in seen:  # past the combinations there are: a later part of the same page
            title = f"{topic}{suffix} - part {len(pages)} - {site}"
        seen.add(title)
        slug = f"{topic.replace(' ', '_')}{len(pages)}"
        pages.append((title, "https://" + pattern.format(slug=slug), site))
    course_of = {i: rng.choice(COURSES) for i in range(len(pages)) if rng.random() < 0.3}
    written = 0

    def write(rel: str, text: str) -> None:
        nonlocal written
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written += 1

    # Pages are read with a long tail: a few every week, most once.
    weights = [1 / (1 + k) ** 0.75 for k in range(len(pages))]
    start = date(2026, 9, 1)
    read_on: dict[int, list[str]] = {}
    for d in range(days):
        day = (start + timedelta(days=d)).isoformat()
        picks = sorted({rng.choices(range(len(pages)), weights)[0] for _ in range(rng.randint(4, 22))})
        lines = [f"## On screen ({rng.randint(40, 300)} min)"]
        clock = 8 * 60 + rng.randint(0, 90)
        for p in picks:
            clock += rng.randint(3, 40)
            title = pages[p][0]
            lines.append(f"- {clock // 60:02d}:{clock % 60:02d} {link(f'Sources/{title}', title)} · {rng.randint(1, 30)} min")
            read_on.setdefault(p, []).append(day)
        lines += ["", "## Searched"]
        for p in picks[: rng.randint(1, 4)]:
            lines.append(f"- {rng.randint(9, 22):02d}:{rng.randint(0, 59):02d} “{pages[p][0].split(' - ')[0].lower()}” → {link(f'Sources/{pages[p][0]}', pages[p][0])}")
        due = [c for c in {course_of[p] for p in picks if p in course_of}]
        if due:
            lines += ["", "## Due"]
            lines += [f"- {link(f'Courses/{c}', c)} due {(start + timedelta(days=d + rng.randint(3, 14))).isoformat()} 23:59" for c in due]
        body = "<!-- daycube:start -->\n" + "\n".join(lines) + "\n<!-- daycube:end -->\n"
        write(f"Daily/{day}.md", front(daycube=f"day {day}", date=day, tags="[daily]") + body)

    for p, (title, url, site) in enumerate(pages):
        if p not in read_on:
            continue
        days_read = read_on[p]
        lines = [f"**Site:** {site} · **Seen:** {days_read[0]}" + (f" to {days_read[-1]}" if len(days_read) > 1 else ""), ""]
        if p in course_of:
            lines += ["## Courses", f"- {link(f'Courses/{course_of[p]}', course_of[p])}", ""]
        body = "<!-- daycube:start -->\n" + "\n".join(lines) + "\n<!-- daycube:end -->\n"
        write(f"Sources/{title}.md", front(daycube=f"page {url}", url=url, site=site, tags="[source]") + body)

    for course in COURSES:
        listed = [pages[p][0] for p, c in course_of.items() if c == course and p in read_on]
        lines = ["## Pages"] + [f"- {link(f'Sources/{t}', t)}" for t in sorted(listed)]
        body = "<!-- daycube:start -->\n" + "\n".join(lines) + "\n<!-- daycube:end -->\n"
        write(f"Courses/{course}.md", front(daycube=f"course {course}", tags="[course]") + body)

    own = [(rng.choice(FOLDERS), name) for name in IDEAS]
    read = sorted(read_on)
    for folder, name in own:
        targets = [link(f"Daily/{(start + timedelta(days=rng.randrange(days))).isoformat()}") for _ in range(rng.randint(0, 3))]
        targets += [link(f"Sources/{pages[rng.choice(read)][0]}") for _ in range(rng.randint(1, 5))]
        targets += [link(f"{f}/{n}") for f, n in rng.sample(own, rng.randint(0, 3)) if n != name]
        targets += [f"[[{rng.choice(MISSING)}]]" for _ in range(rng.randint(0, 2))]
        rng.shuffle(targets)
        text = f"# {name}\n\n" + "\n".join(f"- {t}" for t in targets) + "\n"
        write(f"{folder}/{name}.md", front(tags="[idea]") + text)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", type=Path, help="the notes folder to write into")
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--sources", type=int, default=420)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    print(f"{build(args.root, args.days, args.sources, args.seed)} notes in {args.root}")


if __name__ == "__main__":
    main()
