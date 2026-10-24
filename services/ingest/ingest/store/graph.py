"""Reading the knowledge graph out of the vault database (written by ``LifeStore._graph``)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from ..vault import Record


def _window(since: float | None, until: float | None) -> tuple[str, list]:
    clauses, values = [], []
    if since is not None:
        clauses.append("u.ts >= ?")
        values.append(since)
    if until is not None:
        clauses.append("u.ts < ?")
        values.append(until)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", values


def graph_view(
    db: sqlite3.Connection,
    *,
    since: float | None = None,
    until: float | None = None,
    kinds: Sequence[str] | None = None,
    limit: int = 200,
) -> Record:
    where, values = _window(since, until)
    if kinds:
        where += (" AND " if where else "WHERE ") + f"e.kind IN ({','.join('?' for _ in kinds)})"
        values += list(kinds)
    # In a window, start from its units (index on ts) and keep that order with CROSS JOIN: left to
    # the planner, a one-day graph at 90 days walked every entity's mentions (300 ms).
    joins = (
        "FROM units u CROSS JOIN mentions m ON m.unit_id = u.id CROSS JOIN entities e ON e.id = m.entity_id"
        if since is not None or until is not None
        else "FROM entities e JOIN mentions m ON m.entity_id = e.id JOIN units u ON u.id = m.unit_id"
    )
    rows = db.execute(
        "SELECT e.id, e.kind, e.key, e.name, count(DISTINCT m.unit_id) AS mentions, min(u.ts) AS first, max(u.ts) AS last "
        f"{joins} {where} GROUP BY e.id ORDER BY mentions DESC, last DESC LIMIT ?",
        (*values, limit),
    ).fetchall()
    nodes = [dict(row) for row in rows]
    if since is None and until is None and len(nodes) < limit:
        # The person's notes and what they link to: no records mention a note, its links are its weight.
        have = {node["id"] for node in nodes}
        kind_filter = f" AND e.kind IN ({','.join('?' for _ in kinds)})" if kinds else ""
        for row in db.execute(
            "SELECT e.id, e.kind, e.key, e.name, 0 AS mentions, NULL AS first, NULL AS last, count(*) AS degree "
            "FROM links l JOIN entities e ON e.id IN (l.src, l.dst) WHERE l.day = ''"
            f"{kind_filter} GROUP BY e.id ORDER BY degree DESC LIMIT ?",
            (*(kinds or ()), limit),
        ):
            if row["id"] not in have and len(nodes) < limit:
                node = dict(row)
                node.pop("degree")
                nodes.append(node)
    ids = [node["id"] for node in nodes]
    edges: list[Record] = []
    if ids:
        marks = ",".join("?" for _ in ids)
        edges = [
            dict(row)
            for row in db.execute(
                "SELECT src AS source, dst AS target, kind, count(*) AS weight FROM links "
                f"WHERE src IN ({marks}) AND dst IN ({marks}) GROUP BY src, dst, kind ORDER BY weight DESC",
                (*ids, *ids),
            )
        ]
    return {"nodes": nodes, "edges": edges}


def entity_view(db: sqlite3.Connection, entity_id: int, *, limit: int = 50) -> Record | None:
    row = db.execute("SELECT id, kind, key, name FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        return None
    if row["kind"] == "place":  # every record at the place, not only its visits
        mentions = [
            dict(m)
            for m in db.execute(
                "SELECT u.id AS unit_id, u.day, u.t, u.t_end, u.type, u.text, u.evidence_id, 'at' AS role "
                "FROM units u WHERE u.place = ? ORDER BY u.ts DESC LIMIT ?",
                (row["key"], limit),
            )
        ]
    else:
        mentions = [
            dict(m)
            for m in db.execute(
                "SELECT u.id AS unit_id, u.day, u.t, u.t_end, u.type, u.text, u.evidence_id, m.role "
                "FROM mentions m JOIN units u ON u.id = m.unit_id WHERE m.entity_id = ? ORDER BY u.ts DESC LIMIT ?",
                (entity_id, limit),
            )
        ]
    neighbours = [
        dict(n)
        for n in db.execute(
            "SELECT e.id, e.kind, e.name, l.kind AS link, 'out' AS direction, count(*) AS weight "
            "FROM links l JOIN entities e ON e.id = l.dst WHERE l.src = ? GROUP BY e.id, l.kind "
            "UNION ALL "
            "SELECT e.id, e.kind, e.name, l.kind AS link, 'in' AS direction, count(*) AS weight "
            "FROM links l JOIN entities e ON e.id = l.src WHERE l.dst = ? GROUP BY e.id, l.kind "
            "ORDER BY weight DESC",
            (entity_id, entity_id),
        )
    ]
    count = db.execute("SELECT count(DISTINCT unit_id) FROM mentions WHERE entity_id = ?", (entity_id,)).fetchone()[0]
    return {**dict(row), "mention_count": count, "mentions": mentions, "neighbours": neighbours}
