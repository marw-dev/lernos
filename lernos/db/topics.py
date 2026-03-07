"""
LernOS — Topic CRUD
"""
from __future__ import annotations
import sqlite3
import struct
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


# ── State constants ────────────────────────────────────────────────────────────
STATE_NEW      = "NEW"
STATE_LEARNING = "LEARNING"
STATE_REVIEW   = "REVIEW"
STATE_MASTERED = "MASTERED"
STATE_FROZEN   = "FROZEN"

ALL_STATES = [STATE_NEW, STATE_LEARNING, STATE_REVIEW, STATE_MASTERED, STATE_FROZEN]

STATE_EMOJI = {
    STATE_NEW:      "🆕",
    STATE_LEARNING: "📖",
    STATE_REVIEW:   "🔄",
    STATE_MASTERED: "✅",
    STATE_FROZEN:   "❄️ ",
}

STATE_COLOR = {
    STATE_NEW:      "bright_black",
    STATE_LEARNING: "red",
    STATE_REVIEW:   "blue",
    STATE_MASTERED: "green",
    STATE_FROZEN:   "magenta",
}


@dataclass
class Topic:
    id:           int
    name:         str
    module:       str
    description:  str
    state:        str
    ef:           float
    interval_d:   int
    repetitions:  int
    due_date:     str
    frozen_until: Optional[str]
    embedding:    Optional[bytes]
    created_at:   str
    updated_at:   str

    @property
    def is_due(self) -> bool:
        return self.due_date <= date.today().isoformat()

    @property
    def days_until_due(self) -> int:
        d = date.fromisoformat(self.due_date)
        return (d - date.today()).days

    @property
    def embedding_vector(self) -> Optional[list[float]]:
        if self.embedding is None:
            return None
        n = len(self.embedding) // 4
        return list(struct.unpack(f"{n}f", self.embedding))

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Topic":
        return Topic(
            id=row["id"],
            name=row["name"],
            module=row["module"],
            description=row["description"],
            state=row["state"],
            ef=row["ef"],
            interval_d=row["interval_d"],
            repetitions=row["repetitions"],
            due_date=row["due_date"],
            frozen_until=row["frozen_until"],
            embedding=row["embedding"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class Edge:
    id:        int
    from_id:   int
    to_id:     int
    weight:    float
    confirmed: bool
    from_name: str = ""
    to_name:   str = ""

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Edge":
        return Edge(
            id=row["id"],
            from_id=row["from_id"],
            to_id=row["to_id"],
            weight=row["weight"],
            confirmed=bool(row["confirmed"]),
            from_name=row["from_name"] if "from_name" in row.keys() else "",
            to_name=row["to_name"] if "to_name" in row.keys() else "",
        )


# ── Topic CRUD ─────────────────────────────────────────────────────────────────

def create_topic(conn: sqlite3.Connection, name: str, module: str = "",
                 description: str = "") -> Topic:
    cur = conn.execute(
        "INSERT INTO topics (name, module, description) VALUES (?,?,?)",
        (name.strip(), module.strip(), description.strip())
    )
    conn.commit()
    return get_topic_by_id(conn, cur.lastrowid)


def get_topic_by_id(conn: sqlite3.Connection, topic_id: int) -> Optional[Topic]:
    row = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    return Topic.from_row(row) if row else None


def get_topic_by_name(conn: sqlite3.Connection, name: str) -> Optional[Topic]:
    row = conn.execute("SELECT * FROM topics WHERE name=?", (name,)).fetchone()
    return Topic.from_row(row) if row else None


def get_all_topics(conn: sqlite3.Connection,
                   state: Optional[str] = None,
                   module: Optional[str] = None) -> list[Topic]:
    sql = "SELECT * FROM topics WHERE 1=1"
    params: list = []
    if state:
        sql += " AND state=?"
        params.append(state)
    if module:
        sql += " AND module=?"
        params.append(module)
    sql += " ORDER BY due_date ASC, name ASC"
    return [Topic.from_row(r) for r in conn.execute(sql, params).fetchall()]


def get_all_topic_names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM topics ORDER BY name").fetchall()]


def get_due_topics(conn: sqlite3.Connection) -> list[Topic]:
    rows = conn.execute(
        """SELECT * FROM topics
           WHERE due_date <= date('now')
             AND state NOT IN ('FROZEN')
           ORDER BY
             CASE state
               WHEN 'LEARNING' THEN 0
               WHEN 'REVIEW'   THEN 1
               WHEN 'NEW'      THEN 2
               ELSE 3
             END,
             due_date ASC""",
    ).fetchall()
    return [Topic.from_row(r) for r in rows]


def update_topic_sm2(conn: sqlite3.Connection, topic_id: int,
                     state: str, ef: float, interval_d: int,
                     repetitions: int, due_date: str) -> None:
    conn.execute(
        """UPDATE topics
           SET state=?, ef=?, interval_d=?, repetitions=?,
               due_date=?, updated_at=datetime('now')
           WHERE id=?""",
        (state, ef, interval_d, repetitions, due_date, topic_id)
    )
    conn.commit()


def update_topic_embedding(conn: sqlite3.Connection, topic_id: int,
                           embedding: bytes) -> None:
    conn.execute(
        "UPDATE topics SET embedding=?, updated_at=datetime('now') WHERE id=?",
        (embedding, topic_id)
    )
    conn.commit()


def freeze_topic(conn: sqlite3.Connection, topic_id: int, days: int = 6) -> None:
    conn.execute(
        """UPDATE topics
           SET state='FROZEN',
               frozen_until=date('now', ?),
               updated_at=datetime('now')
           WHERE id=?""",
        (f"+{days} days", topic_id)
    )
    conn.commit()


def unfreeze_topic(conn: sqlite3.Connection, topic_id: int) -> None:
    conn.execute(
        """UPDATE topics
           SET state='REVIEW',
               frozen_until=NULL,
               due_date=date('now'),
               updated_at=datetime('now')
           WHERE id=?""",
        (topic_id,)
    )
    conn.commit()


def thaw_expired_frozen(conn: sqlite3.Connection) -> int:
    """Reactivate topics whose freeze period has ended. Returns count."""
    cur = conn.execute(
        """UPDATE topics
           SET state='REVIEW',
               frozen_until=NULL,
               due_date=date('now'),
               updated_at=datetime('now')
           WHERE state='FROZEN'
             AND frozen_until IS NOT NULL
             AND frozen_until <= date('now')"""
    )
    conn.commit()
    return cur.rowcount


def delete_topic(conn: sqlite3.Connection, topic_id: int) -> None:
    conn.execute("DELETE FROM topics WHERE id=?", (topic_id,))
    conn.commit()


# ── Edge CRUD ─────────────────────────────────────────────────────────────────

def update_topic(conn: sqlite3.Connection, topic_id: int,
                 name: str | None = None,
                 module: str | None = None,
                 description: str | None = None) -> bool:
    """
    Aktualisiert Name, Modul und/oder Beschreibung eines Topics.
    Gibt True zurück wenn mindestens ein Feld geändert wurde.
    SM-2-Daten (EF, Intervall, Zustand) bleiben unberührt.
    """
    updates = []
    params  = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if module is not None:
        updates.append("module = ?")
        params.append(module)
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if not updates:
        return False
    updates.append("updated_at = datetime('now')")
    params.append(topic_id)
    conn.execute(
        f"UPDATE topics SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()
    return True


def create_edge(conn: sqlite3.Connection, from_id: int, to_id: int,
                weight: float = 0.5, confirmed: bool = True) -> Edge:
    conn.execute(
        """INSERT OR REPLACE INTO edges (from_id, to_id, weight, confirmed)
           VALUES (?,?,?,?)""",
        (from_id, to_id, weight, int(confirmed))
    )
    conn.commit()
    row = conn.execute(
        """SELECT e.*, f.name as from_name, t.name as to_name
           FROM edges e
           JOIN topics f ON f.id = e.from_id
           JOIN topics t ON t.id = e.to_id
           WHERE e.from_id=? AND e.to_id=?""",
        (from_id, to_id)
    ).fetchone()
    return Edge.from_row(row)


def get_edges_for_topic(conn: sqlite3.Connection, topic_id: int) -> dict:
    """Returns {'outgoing': [...], 'incoming': [...]}"""
    out_rows = conn.execute(
        """SELECT e.*, f.name as from_name, t.name as to_name
           FROM edges e
           JOIN topics f ON f.id = e.from_id
           JOIN topics t ON t.id = e.to_id
           WHERE e.from_id=?""",
        (topic_id,)
    ).fetchall()
    in_rows = conn.execute(
        """SELECT e.*, f.name as from_name, t.name as to_name
           FROM edges e
           JOIN topics f ON f.id = e.from_id
           JOIN topics t ON t.id = e.to_id
           WHERE e.to_id=?""",
        (topic_id,)
    ).fetchall()
    return {
        "outgoing": [Edge.from_row(r) for r in out_rows],
        "incoming": [Edge.from_row(r) for r in in_rows],
    }


def get_all_edges(conn: sqlite3.Connection) -> list[Edge]:
    rows = conn.execute(
        """SELECT e.*, f.name as from_name, t.name as to_name
           FROM edges e
           JOIN topics f ON f.id = e.from_id
           JOIN topics t ON t.id = e.to_id"""
    ).fetchall()
    return [Edge.from_row(r) for r in rows]


def delete_edge(conn: sqlite3.Connection, from_id: int, to_id: int) -> None:
    conn.execute("DELETE FROM edges WHERE from_id=? AND to_id=?", (from_id, to_id))
    conn.commit()


# ── Session CRUD ──────────────────────────────────────────────────────────────

def log_session(conn: sqlite3.Connection, topic_id: int,
                grade: int, confidence: int, correct: int,
                old_state: str, new_state: str,
                old_ef: float, new_ef: float,
                old_interval: int, new_interval: int) -> None:
    conn.execute(
        """INSERT INTO sessions
           (topic_id, grade, confidence, correct,
            old_state, new_state, old_ef, new_ef, old_interval, new_interval)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (topic_id, grade, confidence, correct,
         old_state, new_state, old_ef, new_ef, old_interval, new_interval)
    )
    conn.commit()


# ── learning_resets updaten ───────────────────────────────────────────────────

def increment_learning_resets(conn: sqlite3.Connection, topic_id: int) -> None:
    """Erhöht den Ease-Hell-Zähler wenn Topic auf LEARNING zurückfällt."""
    conn.execute(
        "UPDATE topics SET learning_resets = learning_resets + 1 WHERE id=?",
        (topic_id,)
    )
    conn.commit()


def update_topic_sm2(conn: sqlite3.Connection, topic_id: int,
                     state: str, ef: float, interval_d: int,
                     repetitions: int, due_date: str,
                     learning_resets: int | None = None) -> None:
    if learning_resets is not None:
        conn.execute(
            """UPDATE topics
               SET state=?, ef=?, interval_d=?, repetitions=?,
                   due_date=?, learning_resets=?, updated_at=datetime('now')
               WHERE id=?""",
            (state, ef, interval_d, repetitions, due_date, learning_resets, topic_id)
        )
    else:
        conn.execute(
            """UPDATE topics
               SET state=?, ef=?, interval_d=?, repetitions=?,
                   due_date=?, updated_at=datetime('now')
               WHERE id=?""",
            (state, ef, interval_d, repetitions, due_date, topic_id)
        )
    conn.commit()


# ── Document CRUD ─────────────────────────────────────────────────────────────

@dataclass
class Document:
    id:           int
    topic_id:     int
    filename:     str
    filepath:     str
    file_size:    int
    page_count:   int
    text_excerpt: str
    full_text:    str
    added_at:     str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Document":
        return Document(
            id=row["id"], topic_id=row["topic_id"],
            filename=row["filename"], filepath=row["filepath"],
            file_size=row["file_size"], page_count=row["page_count"],
            text_excerpt=row["text_excerpt"], full_text=row["full_text"],
            added_at=row["added_at"],
        )


def add_document(conn: sqlite3.Connection, topic_id: int, filename: str,
                 filepath: str, file_size: int, page_count: int,
                 text_excerpt: str, full_text: str) -> Document:
    cur = conn.execute(
        """INSERT INTO documents
           (topic_id, filename, filepath, file_size, page_count, text_excerpt, full_text)
           VALUES (?,?,?,?,?,?,?)""",
        (topic_id, filename, filepath, file_size, page_count, text_excerpt, full_text)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM documents WHERE id=?", (cur.lastrowid,)).fetchone()
    return Document.from_row(row)


def get_documents_for_topic(conn: sqlite3.Connection, topic_id: int) -> list[Document]:
    rows = conn.execute(
        "SELECT * FROM documents WHERE topic_id=? ORDER BY added_at DESC",
        (topic_id,)
    ).fetchall()
    return [Document.from_row(r) for r in rows]


def get_document_by_id(conn: sqlite3.Connection, doc_id: int) -> Optional[Document]:
    row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return Document.from_row(row) if row else None


def delete_document(conn: sqlite3.Connection, doc_id: int) -> None:
    # Explizit Fragen löschen (Fallback falls FK-Cascade nicht greift)
    conn.execute("DELETE FROM generated_questions WHERE document_id=?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit()


# ── Generated Questions CRUD ──────────────────────────────────────────────────

@dataclass
class Question:
    id:          int
    topic_id:    int
    document_id: Optional[int]
    question:    str
    answer:      str
    difficulty:  int
    used_count:  int
    last_used:   Optional[str]
    created_at:  str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Question":
        return Question(
            id=row["id"], topic_id=row["topic_id"],
            document_id=row["document_id"],
            question=row["question"], answer=row["answer"],
            difficulty=row["difficulty"], used_count=row["used_count"],
            last_used=row["last_used"], created_at=row["created_at"],
        )


def add_question(conn: sqlite3.Connection, topic_id: int, question: str,
                 answer: str = "", difficulty: int = 3,
                 document_id: Optional[int] = None) -> Question:
    cur = conn.execute(
        """INSERT INTO generated_questions
           (topic_id, document_id, question, answer, difficulty)
           VALUES (?,?,?,?,?)""",
        (topic_id, document_id, question, answer, difficulty)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM generated_questions WHERE id=?", (cur.lastrowid,)).fetchone()
    return Question.from_row(row)


def get_questions_for_topic(conn: sqlite3.Connection, topic_id: int,
                            unused_first: bool = True) -> list[Question]:
    order = "used_count ASC, created_at ASC" if unused_first else "created_at DESC"
    rows = conn.execute(
        f"SELECT * FROM generated_questions WHERE topic_id=? ORDER BY {order}",
        (topic_id,)
    ).fetchall()
    return [Question.from_row(r) for r in rows]


def mark_question_used(conn: sqlite3.Connection, question_id: int) -> None:
    conn.execute(
        """UPDATE generated_questions
           SET used_count=used_count+1, last_used=datetime('now')
           WHERE id=?""",
        (question_id,)
    )
    conn.commit()


def delete_questions_for_topic(conn: sqlite3.Connection, topic_id: int) -> int:
    cur = conn.execute(
        "DELETE FROM generated_questions WHERE topic_id=?", (topic_id,)
    )
    conn.commit()
    return cur.rowcount
