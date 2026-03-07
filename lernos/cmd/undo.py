"""
LernOS — `lernos undo`

Macht die letzte Review-Bewertung eines Topics rückgängig.

Gespeichert in der sessions-Tabelle:
  old_state, old_ef, old_interval → werden wiederhergestellt
  old_state war LEARNING + neue state NEW → learning_resets wird dekrementiert

Schutzmechanismus:
  - Undo ist nur für die letzte Session möglich (kein Multi-Level-Undo)
  - Prüft ob Session jünger als 1h ist (konfigurierbarer Safety-Guard)
  - Zeigt vorher/nachher-Diff und erfordert Bestätigung
"""
from __future__ import annotations
import sys
import click

from lernos import ui


@click.command("undo")
@click.argument("topic_name", required=False,
                shell_complete=lambda c,p,i: __import__(
                    'lernos.completion_helpers', fromlist=['complete_topic_names']
                ).complete_topic_names(c,p,i))
@click.option("--yes", "-y", is_flag=True, help="Ohne Bestätigung")
@click.option("--max-age", default=60,
              help="Max. Minuten seit der Bewertung (Standard: 60). 0 = kein Limit.")
def cmd_undo(topic_name: str | None, yes: bool, max_age: int):
    """
    Letzte Review-Bewertung rückgängig machen.

    \b
    Stellt EF, Intervall und Zustand auf den Stand vor der letzten Session zurück.
    Die Session-Zeile wird aus der Historie gelöscht.

    \b
    Sicherheitsregeln:
      - Nur die allerjüngste Session ist rückgängig machbar
      - Standard: nur wenn Session < 60 Minuten alt (--max-age 0 = kein Limit)

    \b
    Beispiele:
      lernos undo                    # Letzte Review-Session (beliebiges Topic)
      lernos undo "Kettenregel"      # Letzte Session für dieses Topic
      lernos undo --max-age 0        # Auch ältere Sessions rückgängig machen
    """
    from lernos.db.schema import startup
    from lernos.fuzzy.resolve import resolve_topic

    conn = startup()

    # Letzte Session finden
    if topic_name:
        topic = resolve_topic(conn, topic_name)
        if not topic:
            ui.error(f"Topic '{topic_name}' nicht gefunden.")
            sys.exit(1)
        session = conn.execute(
            """SELECT s.*, t.name as topic_name
               FROM sessions s
               JOIN topics t ON t.id = s.topic_id
               WHERE s.topic_id = ?
               ORDER BY s.id DESC LIMIT 1""",
            (topic.id,)
        ).fetchone()
    else:
        session = conn.execute(
            """SELECT s.*, t.name as topic_name
               FROM sessions s
               JOIN topics t ON t.id = s.topic_id
               ORDER BY s.id DESC LIMIT 1"""
        ).fetchone()

    if not session:
        ui.error("Keine Review-Session gefunden." +
                 (f" Für '{topic_name}'." if topic_name else ""))
        sys.exit(1)

    # Alter prüfen
    if max_age > 0:
        import sqlite3 as _sq
        age_minutes = conn.execute(
            "SELECT CAST((julianday('now') - julianday(reviewed_at)) * 1440 AS INTEGER)"
            " FROM sessions WHERE id=?",
            (session["id"],)
        ).fetchone()[0]

        if age_minutes > max_age:
            ui.error(
                f"Session ist {age_minutes} Minuten alt — "
                f"Sicherheitslimit von {max_age} Minuten überschritten."
            )
            ui.info(f"Nutze --max-age {age_minutes + 5} um trotzdem rückgängig zu machen.")
            sys.exit(1)

    # Diff anzeigen
    t_name    = session["topic_name"]
    reviewed  = session["reviewed_at"]
    grade     = session["grade"]
    old_state = session["old_state"]
    new_state = session["new_state"]
    old_ef    = session["old_ef"]
    new_ef    = session["new_ef"]
    old_ivl   = session["old_interval"]
    new_ivl   = session["new_interval"]

    state_colors = {
        "NEW": ui.BRIGHT_BLACK, "LEARNING": ui.BRIGHT_RED,
        "REVIEW": ui.BRIGHT_BLUE, "MASTERED": ui.BRIGHT_GREEN,
        "FROZEN": ui.BRIGHT_MAGENTA,
    }

    ui.header(f"↩️  Undo: {t_name}", f"Session vom {reviewed[:16]}")
    print()

    _row("Bewertung (Grade)",
         str(grade),
         "—",
         ui.BRIGHT_YELLOW, ui.DIM)
    _row("Zustand",
         ui.c(new_state, state_colors.get(new_state, ui.WHITE)),
         ui.c(old_state, state_colors.get(old_state, ui.WHITE)),
         state_colors.get(new_state, ui.WHITE),
         state_colors.get(old_state, ui.WHITE))
    _row("EF",
         f"{new_ef:.3f}",
         f"{old_ef:.3f}",
         ui.BRIGHT_CYAN, ui.BRIGHT_CYAN)
    _row("Intervall",
         f"{new_ivl}d",
         f"{old_ivl}d",
         ui.BRIGHT_CYAN, ui.BRIGHT_CYAN)

    print()

    if not (yes or ui._yes_all or ui.confirm("Rückgängig machen?", default=True)):
        ui.info("Abgebrochen.")
        return

    # Wiederherstellung
    from datetime import date as _date, timedelta
    new_due = (_date.today() + timedelta(days=old_ivl)).isoformat()

    conn.execute(
        """UPDATE topics
           SET state=?, ef=?, interval_d=?, repetitions=MAX(0, repetitions-1),
               due_date=?, updated_at=datetime('now')
           WHERE id=?""",
        (old_state, old_ef, old_ivl, new_due, session["topic_id"])
    )

    # learning_resets dekrementieren wenn Undo aus LEARNING→NEW/REVIEW
    if new_state == "LEARNING" and old_state != "LEARNING":
        conn.execute(
            "UPDATE topics SET learning_resets=MAX(0, learning_resets-1) WHERE id=?",
            (session["topic_id"],)
        )

    # Session-Eintrag löschen
    conn.execute("DELETE FROM sessions WHERE id=?", (session["id"],))
    conn.commit()

    ui.success(f"Session rückgängig gemacht: {t_name}")
    ui.info(f"Zustand: {new_state} → {old_state}  |  "
            f"EF: {new_ef:.3f} → {old_ef:.3f}  |  "
            f"Fällig in: {old_ivl}d")


def _row(label: str, from_val: str, to_val: str,
         from_col: str = "", to_col: str = "") -> None:
    l = ui.c(f"{label}:", ui.DIM)
    f = ui.c(from_val, from_col) if from_col else from_val
    t = ui.c(to_val,   to_col)   if to_col   else to_val
    print(f"  {l:<30} {f}  →  {t}")
