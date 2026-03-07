"""
LernOS — Kaskadierende Wiederholung (gestaffelt, gegen "Cascade Hell")

Fix: Kaskaden sind NUR EINE Ebene tief.
Wenn A fehlschlägt → nur direkte Kinder B auf REVIEW.
C und D werden erst getriggert wenn B beim nächsten Review ebenfalls fehlschlägt.
"""
from __future__ import annotations
import sqlite3
from lernos.db.topics import STATE_LEARNING, STATE_FROZEN, STATE_NEW

CASCADE_WEIGHT_SOFT = 0.6   # → REVIEW
CASCADE_WEIGHT_HARD = 0.8   # → LEARNING (wenn MASTERED)


def cascade_review(conn: sqlite3.Connection, topic_id: int,
                   depth: int = 1) -> list[dict]:
    """
    Gestaffelte kaskadierende Wiederholung — maximal eine Ebene tief.
    depth=1 bedeutet: nur direkte Kinder. Nie rekursiv.
    """
    affected = []

    rows = conn.execute(
        """SELECT t.id, t.name, t.state, e.weight
           FROM edges e
           JOIN topics t ON t.id = e.to_id
           WHERE e.from_id = ?
             AND e.weight >= ?
             AND t.state NOT IN (?, ?)""",
        (topic_id, CASCADE_WEIGHT_SOFT, STATE_FROZEN, STATE_NEW)
    ).fetchall()

    for row in rows:
        dep_id    = row["id"]
        dep_name  = row["name"]
        dep_state = row["state"]
        weight    = row["weight"]

        # Harte Kaskade nur für MASTERED mit sehr hohem Gewicht
        if weight >= CASCADE_WEIGHT_HARD and dep_state == "MASTERED":
            target_state = STATE_LEARNING
        else:
            target_state = "REVIEW"

        conn.execute(
            """UPDATE topics
               SET state=?, due_date=date('now'), updated_at=datetime('now')
               WHERE id=?""",
            (target_state, dep_id)
        )
        affected.append({
            "id":     dep_id,
            "name":   dep_name,
            "old":    dep_state,
            "new":    target_state,
            "weight": weight,
        })

    if affected:
        conn.commit()

    return affected
