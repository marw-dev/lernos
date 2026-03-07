"""
LernOS — Click-Autovervollständigung für Topic-Namen

BUG FIX v1.5:
  Öffnet DB direkt read-only per SQLite URI (mode=ro) statt über den
  normalen Startup-Pfad, der _rolling_backup() aufruft —
  beim ersten Tab-Druck des Tages wurde so im Hintergrund ein Backup erstellt,
  was zu spürbarem Lag führte. get_connection() öffnet ohne diesen Overhead.
"""
from __future__ import annotations
from click.shell_completion import CompletionItem


def _open_readonly() -> object | None:
    """
    Öffnet die DB read-only ohne Backup/Migrate.
    Gibt None zurück wenn DB nicht existiert oder Fehler auftritt.
    """
    try:
        from lernos.db.schema import get_db_path
        import sqlite3, os
        path = get_db_path()
        if not os.path.exists(path):
            return None
        # URI-Modus: mode=ro → kein Schreiben, kein WAL-Journal angelegt
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def complete_topic_names(ctx, param, incomplete: str) -> list[CompletionItem]:
    """Completion für alle Topic-Namen (case-insensitive Präfix)."""
    try:
        conn = _open_readonly()
        if conn is None:
            return []
        q = incomplete.lower()
        rows = conn.execute(
            "SELECT name FROM topics ORDER BY name"
        ).fetchall()
        conn.close()
        return [
            CompletionItem(r["name"])
            for r in rows
            if r["name"].lower().startswith(q)
        ]
    except Exception:
        return []


def complete_due_topic_names(ctx, param, incomplete: str) -> list[CompletionItem]:
    """Completion nur für heute fällige Topics — sinnvoll für `lernos review`."""
    try:
        conn = _open_readonly()
        if conn is None:
            return []
        from datetime import date
        today = date.today().isoformat()
        q     = incomplete.lower()
        rows  = conn.execute(
            "SELECT name, state, ef FROM topics WHERE due_date <= ? ORDER BY name",
            (today,)
        ).fetchall()
        conn.close()
        return [
            CompletionItem(r["name"], help=f"{r['state']} · EF:{r['ef']:.2f}")
            for r in rows
            if r["name"].lower().startswith(q)
        ]
    except Exception:
        return []


def complete_module_names(ctx, param, incomplete: str) -> list[CompletionItem]:
    """Completion für Modul-Namen."""
    try:
        conn = _open_readonly()
        if conn is None:
            return []
        q    = incomplete.lower()
        rows = conn.execute(
            "SELECT DISTINCT module FROM topics"
            " WHERE module IS NOT NULL AND module != ''"
            " ORDER BY module"
        ).fetchall()
        conn.close()
        return [
            CompletionItem(r["module"])
            for r in rows
            if r["module"].lower().startswith(q)
        ]
    except Exception:
        return []
