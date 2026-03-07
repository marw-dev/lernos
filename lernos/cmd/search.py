"""
LernOS — `lernos search`
Durchsucht Topics, Beschreibungen, generierte Fragen und PDF-Volltext.
Gibt gerankte Ergebnisse mit Kontext-Snippets zurück.
"""
from __future__ import annotations
import re
import sys
import click

from lernos import ui
from lernos.completion_helpers import complete_topic_names, complete_due_topic_names, complete_module_names


def _snippet(text: str, query: str, window: int = 80) -> str:
    """Gibt einen Kontext-Ausschnitt um das erste Treffer-Vorkommen zurück."""
    if not text:
        return ""
    idx = text.lower().find(query.lower())
    if idx == -1:
        # Fallback: erstes Wort des Queries suchen
        first_word = query.split()[0] if query.split() else query
        idx = text.lower().find(first_word.lower())
    if idx == -1:
        return text[:window] + "…"
    start = max(0, idx - window // 2)
    end   = min(len(text), idx + window // 2)
    snip  = text[start:end].strip()
    if start > 0:
        snip = "…" + snip
    if end < len(text):
        snip = snip + "…"
    # Query-Treffer hervorheben
    highlighted = re.sub(
        f"({re.escape(query)})",
        lambda m: ui.c(m.group(), ui.BRIGHT_YELLOW, ui.BOLD),
        snip, flags=re.IGNORECASE
    )
    return highlighted


@click.command("search")
@click.argument("query", shell_complete=complete_topic_names)
@click.option("--module", "-m", default="", help="Suche auf Modul begrenzen")
@click.option("--in-pdfs", is_flag=True, help="Auch PDF-Volltexte durchsuchen (langsam)")
@click.option("--in-questions", is_flag=True, default=True, help="Fragen durchsuchen (Standard: an)")
@click.option("--limit", "-l", default=20, help="Max. Ergebnisse (Standard: 20)")
def cmd_search(query: str, module: str, in_pdfs: bool, in_questions: bool, limit: int):
    """
    Globale Volltextsuche über Topics, Fragen und (optional) PDFs.

    \b
    Durchsucht:
      - Topic-Namen und Beschreibungen
      - Generierte Fragen und Musterantworten
      - PDF-Volltexte (mit --in-pdfs)

    \b
    Beispiele:
      lernos search "Kettenregel"
      lernos search "Ableitung" --module "Analysis I"
      lernos search "Eigenwert" --in-pdfs
    """
    from lernos.db.schema import startup
    from lernos.db.topics import get_all_topics

    if len(query.strip()) < 2:
        ui.error("Suchbegriff muss mindestens 2 Zeichen lang sein.")
        sys.exit(1)

    conn    = startup()
    results = []
    q_lower = query.lower()

    # ── 1. Topics (Name + Beschreibung) ──────────────────────────────────────
    topics = get_all_topics(conn, module=module or None)
    for t in topics:
        score    = 0
        matches  = []
        name_hit = q_lower in t.name.lower()
        desc_hit = t.description and q_lower in t.description.lower()

        if name_hit:
            score += 100
            matches.append(("Name", ui.c(t.name, ui.BRIGHT_YELLOW, ui.BOLD)))
        if desc_hit:
            score += 50
            matches.append(("Beschreibung", _snippet(t.description, query)))

        if score > 0:
            results.append({
                "score":  score,
                "type":   "topic",
                "topic":  t,
                "source": "Topic",
                "matches": matches,
            })

    # ── 2. Generierte Fragen ──────────────────────────────────────────────────
    if in_questions:
        rows = conn.execute(
            """SELECT gq.*, t.name as topic_name, t.module, t.state, t.id as t_id
               FROM generated_questions gq
               JOIN topics t ON t.id = gq.topic_id
               WHERE (lower(gq.question) LIKE ? OR lower(gq.answer) LIKE ?)"""
            + (f" AND t.module = ?" if module else ""),
            (f"%{q_lower}%", f"%{q_lower}%") + ((module,) if module else ()),
        ).fetchall()

        for row in rows:
            q_hit = q_lower in row["question"].lower()
            a_hit = q_lower in (row["answer"] or "").lower()
            score = (80 if q_hit else 0) + (40 if a_hit else 0)
            matches = []
            if q_hit:
                matches.append(("Frage", _snippet(row["question"], query)))
            if a_hit:
                matches.append(("Antwort", _snippet(row["answer"], query)))

            # Zu bestehendem Topic-Ergebnis zusammenführen falls vorhanden
            existing = next((r for r in results
                             if r["type"] == "topic" and r["topic"].id == row["t_id"]), None)
            if existing:
                existing["score"]  += score
                existing["matches"] += matches
            else:
                results.append({
                    "score":  score,
                    "type":   "question",
                    "source": "Frage",
                    "topic_name": row["topic_name"],
                    "module":     row["module"],
                    "state":      row["state"],
                    "t_id":       row["t_id"],
                    "matches":    matches,
                })

    # ── 3. PDF-Volltexte ─────────────────────────────────────────────────────
    if in_pdfs:
        rows = conn.execute(
            """SELECT d.*, t.name as topic_name, t.module, t.state, t.id as t_id
               FROM documents d
               JOIN topics t ON t.id = d.topic_id
               WHERE lower(d.full_text) LIKE ?"""
            + (f" AND t.module = ?" if module else ""),
            (f"%{q_lower}%",) + ((module,) if module else ()),
        ).fetchall()

        for row in rows:
            score = 30
            matches = [("PDF: " + row["filename"], _snippet(row["full_text"], query, window=120))]
            existing = next((r for r in results
                             if r["type"] == "topic" and r["topic"].id == row["t_id"]), None)
            if existing:
                existing["score"]  += score
                existing["matches"] += matches
            else:
                results.append({
                    "score":  score,
                    "type":   "pdf",
                    "source": "PDF",
                    "topic_name": row["topic_name"],
                    "module":     row["module"],
                    "state":      row["state"],
                    "t_id":       row["t_id"],
                    "matches":    matches,
                })

    # ── Ausgabe ───────────────────────────────────────────────────────────────
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:limit]

    state_colors = {
        "NEW": ui.BRIGHT_BLACK, "LEARNING": ui.BRIGHT_RED,
        "REVIEW": ui.BRIGHT_BLUE, "MASTERED": ui.BRIGHT_GREEN,
        "FROZEN": ui.BRIGHT_MAGENTA,
    }

    total_hits = len(results)
    pdf_note   = " (inkl. PDFs)" if in_pdfs else ""
    ui.header(f"🔍 Suche: '{query}'", f"{total_hits} Ergebnis(se){pdf_note}")

    if not results:
        ui.info("Keine Treffer gefunden.")
        if not in_pdfs:
            ui.info("Auch PDFs durchsuchen? Füge --in-pdfs hinzu.")
        return

    for r in results:
        topic_name = r.get("topic_name") or (r["topic"].name if "topic" in r else "?")
        t_module   = r.get("module") or (r["topic"].module if "topic" in r else "")
        t_state    = r.get("state") or (r["topic"].state if "topic" in r else "")
        t_id       = r.get("t_id") or (r["topic"].id if "topic" in r else 0)
        col        = state_colors.get(t_state, ui.WHITE)

        print()
        print(f"  {ui.c(topic_name, ui.BOLD, ui.BRIGHT_WHITE)}  "
              f"{ui.c(f'[{t_state}]', col)}  "
              f"{ui.c(t_module, ui.DIM)}  "
              f"  {ui.c('Relevanz:' + str(r['score']), ui.DIM)}")

        for source, snippet in r["matches"][:3]:
            print(f"  {ui.c(source + ':', ui.BRIGHT_CYAN)}  {snippet}")

        print(f"  {ui.c(f'→ lernos review \"{topic_name}\"', ui.DIM)}")

    print()
    if total_hits == limit:
        ui.info(f"Ergebnisse begrenzt auf {limit}. Mehr mit --limit.")
