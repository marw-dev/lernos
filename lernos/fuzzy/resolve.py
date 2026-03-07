"""
LernOS — Fuzzy Topic-Namensauflösung
Findet Topics auch bei Tippfehlern oder Abkürzungen.
"""
from __future__ import annotations
import sqlite3
from typing import Optional

from lernos.db.topics import Topic, get_topic_by_name, get_all_topic_names


def fuzzy_score(query: str, target: str) -> int:
    """
    Einfacher Fuzzy-Score ohne externe Bibliothek.
    Höher = besser. Priorisiert:
    - Exakter Match: 1000
    - Präfix: 500
    - Enthält: 300
    - Alle Buchstaben in Reihenfolge (subsequence): 0-100
    """
    q = query.lower().strip()
    t = target.lower().strip()

    if q == t:
        return 1000
    if t.startswith(q):
        return 500 + max(0, 50 - len(t))
    if q in t:
        return 300 + max(0, 30 - len(t))

    # Subsequence-Score
    score = 0
    qi = 0
    consecutive = 0
    for ti, ch in enumerate(t):
        if qi < len(q) and ch == q[qi]:
            qi += 1
            consecutive += 1
            score += 1 + consecutive * 2
        else:
            consecutive = 0

    if qi < len(q):
        return 0   # Nicht alle Buchstaben gefunden

    return min(score, 99)


def resolve_topic(conn: sqlite3.Connection,
                  query: str,
                  threshold: int = 20) -> Optional[Topic]:
    """
    Löst einen Topic-Namen auf — auch bei Tippfehlern.
    Gibt das beste Match zurück oder None wenn kein gutes Match.
    """
    # Exakter Match zuerst
    exact = get_topic_by_name(conn, query)
    if exact:
        return exact

    names = get_all_topic_names(conn)
    if not names:
        return None

    scored = [(name, fuzzy_score(query, name)) for name in names]
    scored.sort(key=lambda x: x[1], reverse=True)

    best_name, best_score = scored[0]
    if best_score >= threshold:
        return get_topic_by_name(conn, best_name)

    return None


def get_candidates(query: str, names: list[str],
                   top_k: int = 5, threshold: int = 10) -> list[tuple[str, int]]:
    """Gibt Top-K Kandidaten mit Scores zurück."""
    scored = [(name, fuzzy_score(query, name)) for name in names]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [(n, s) for n, s in scored[:top_k] if s >= threshold]
