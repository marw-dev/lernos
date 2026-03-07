"""
LernOS — Graph-Algorithmen: Topologische Sortierung (Kahn's Algorithmus)
"""
from __future__ import annotations
import sqlite3
from collections import deque
from typing import Optional

from lernos.db.topics import Topic, get_all_topics, get_all_edges


def topo_sort(conn: sqlite3.Connection,
              module: Optional[str] = None) -> tuple[list[Topic], bool]:
    """
    Topologische Sortierung des Wissensgraphen.
    Gibt (sortierte Topics, hatte_zykel) zurück.
    Bei Zykeln werden verbleibende Topics am Ende angehängt.
    """
    topics = get_all_topics(conn, module=module)
    all_edges = get_all_edges(conn)

    topic_ids = {t.id for t in topics}
    topic_map = {t.id: t for t in topics}

    # In-Degree berechnen + Adjazenzliste
    in_degree: dict[int, int] = {t.id: 0 for t in topics}
    adj: dict[int, list[int]] = {t.id: [] for t in topics}

    for edge in all_edges:
        if edge.from_id in topic_ids and edge.to_id in topic_ids:
            in_degree[edge.to_id] += 1
            adj[edge.from_id].append(edge.to_id)

    # Kahn's BFS
    queue: deque[int] = deque(
        tid for tid, deg in in_degree.items() if deg == 0
    )
    sorted_ids: list[int] = []

    while queue:
        curr = queue.popleft()
        sorted_ids.append(curr)
        for nxt in adj[curr]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    had_cycle = len(sorted_ids) < len(topics)

    # Verbleibende Topics bei Zykeln anhängen
    if had_cycle:
        remaining = [t.id for t in topics if t.id not in set(sorted_ids)]
        sorted_ids.extend(remaining)

    return [topic_map[tid] for tid in sorted_ids], had_cycle


def get_prerequisites(conn: sqlite3.Connection, topic_id: int) -> list[Topic]:
    """Alle direkten Voraussetzungen eines Topics (eingehende Kanten)."""
    rows = conn.execute(
        """SELECT t.* FROM edges e
           JOIN topics t ON t.id = e.from_id
           WHERE e.to_id = ?
           ORDER BY e.weight DESC""",
        (topic_id,)
    ).fetchall()
    return [Topic.from_row(r) for r in rows]


def get_dependents(conn: sqlite3.Connection, topic_id: int) -> list[tuple[Topic, float]]:
    """Alle Topics die von diesem Topic abhängen (ausgehende Kanten)."""
    rows = conn.execute(
        """SELECT t.*, e.weight FROM edges e
           JOIN topics t ON t.id = e.to_id
           WHERE e.from_id = ?
           ORDER BY e.weight DESC""",
        (topic_id,)
    ).fetchall()
    return [(Topic.from_row(r), r["weight"]) for r in rows]


def build_exam_plan(conn: sqlite3.Connection,
                    module: Optional[str] = None,
                    days: int = 14) -> list[dict]:
    """
    Erstellt einen priorisierten Lernplan für die Prüfungsphase.
    Kombiniert topologische Sortierung mit SM-2 Priorität.
    """
    sorted_topics, had_cycle = topo_sort(conn, module=module)

    PRIORITY = {
        "LEARNING": 0,
        "NEW":      1,
        "REVIEW":   2,
        "MASTERED": 3,
        "FROZEN":   4,
    }

    STATE_LABEL = {
        "LEARNING": "SEHR HOCH",
        "NEW":      "HOCH",
        "REVIEW":   "MITTEL",
        "MASTERED": "NIEDRIG",
        "FROZEN":   "PAUSIERT",
    }

    plan = []
    for i, topic in enumerate(sorted_topics):
        plan.append({
            "pos":      i + 1,
            "topic":    topic,
            "priority": PRIORITY.get(topic.state, 5),
            "label":    STATE_LABEL.get(topic.state, "-"),
        })

    # Innerhalb gleicher Position nach Priorität sortieren
    plan.sort(key=lambda x: (x["priority"], x["pos"]))

    return plan
