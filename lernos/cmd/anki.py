"""
LernOS — Anki (.apkg) Import

.apkg ist eine SQLite-DB in einem ZIP-Archiv.
Extrahiert: Karten (Vorder-/Rückseite), Decks (als Module), Tags.
Ignoriert: Medien-Dateien (Bilder, Audio), komplexe Cloze-Felder.

Nutzt keine externen Anki-Bibliotheken — nur stdlib (zipfile, sqlite3).
"""
from __future__ import annotations
import os
import re
import sys
import sqlite3
import tempfile
import zipfile
import json
import click

from lernos import ui


def _strip_html(text: str) -> str:
    """Entfernt HTML-Tags und dekodiert gängige Entities."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    text = re.sub(r"\{\{c\d+::(.*?)(?:::[^}]*)?\}\}", r"\1", text)  # Cloze-Felder
    return text.strip()


def _extract_anki2(apkg_path: str) -> list[dict]:
    """
    Extrahiert Karten aus einem .apkg-Archiv.
    Gibt Liste von {front, back, deck, tags} zurück.
    """
    cards = []
    with tempfile.TemporaryDirectory() as tmpdir:
        # .apkg entpacken
        with zipfile.ZipFile(apkg_path, "r") as zf:
            zf.extractall(tmpdir)

        # Anki DB finden (collection.anki2 oder collection.anki21)
        db_path = None
        for name in ["collection.anki21", "collection.anki2", "collection.sqlite"]:
            candidate = os.path.join(tmpdir, name)
            if os.path.exists(candidate):
                db_path = candidate
                break

        if not db_path:
            raise FileNotFoundError(
                "Keine Anki-Datenbank im .apkg-Archiv gefunden. "
                "Ist die Datei ein gültiges Anki-Deck?"
            )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Deck-Namen laden
        decks_json = conn.execute("SELECT decks FROM col").fetchone()
        deck_map   = {}
        if decks_json:
            try:
                decks = json.loads(decks_json["decks"])
                deck_map = {str(v["id"]): v["name"] for v in decks.values()}
            except Exception:
                pass

        # Notizen laden (jede Note = eine Lernkarte, front/back = erste zwei Felder)
        notes = conn.execute(
            "SELECT id, flds, tags FROM notes"
        ).fetchall()

        # Cards → Notes-Mapping (für Deck-Zuordnung)
        card_deck = {}
        try:
            cards_rows = conn.execute("SELECT nid, did FROM cards").fetchall()
            for cr in cards_rows:
                card_deck[cr["nid"]] = str(cr["did"])
        except Exception:
            pass

        for note in notes:
            fields = note["flds"].split("\x1f")  # Anki-Trennzeichen
            if len(fields) < 1:
                continue
            front = _strip_html(fields[0]) if len(fields) > 0 else ""
            back  = _strip_html(fields[1]) if len(fields) > 1 else ""
            if not front:
                continue

            did      = card_deck.get(note["id"], "0")
            raw_deck = deck_map.get(did, "Anki-Import")
            # Subdeck-Name (letzter Teil von "Mathe::Analysis::Grenzwerte")
            deck_name = raw_deck.split("::")[-1] if "::" in raw_deck else raw_deck
            full_deck = raw_deck  # für Modul

            tags = [t.strip() for t in note["tags"].split() if t.strip()]

            cards.append({
                "front":    front[:500],
                "back":     back[:1000],
                "deck":     deck_name,
                "full_deck": full_deck,
                "tags":     tags,
            })

        conn.close()
    return cards


@click.command("import-anki")
@click.argument("apkg_path")
@click.option("--module", "-m", default="", help="Überschreibe Deck-Name als Modul")
@click.option("--limit", default=0, help="Max. Karten importieren (0 = alle)")
@click.option("--dry-run", is_flag=True, help="Zeige Vorschau ohne zu importieren")
def cmd_import_anki(apkg_path: str, module: str, limit: int, dry_run: bool):
    """
    Anki-Deck (.apkg) importieren.

    \b
    - Karten werden als Topics importiert (Front = Name, Back = Beschreibung)
    - Deck-Namen werden als Module verwendet
    - Bestehende Topics werden übersprungen (kein Überschreiben)
    - Medien (Bilder, Audio) werden ignoriert (nur Text)
    - Cloze-Felder werden in Klartext konvertiert

    \b
    Beispiel:
      lernos import-anki Mathe.apkg
      lernos import-anki Biologie.apkg --module "Biologie II"
      lernos import-anki deck.apkg --dry-run
    """
    if not os.path.exists(apkg_path):
        ui.error(f"Datei nicht gefunden: {apkg_path}")
        sys.exit(1)
    if not apkg_path.lower().endswith(".apkg"):
        ui.warn("Datei hat keine .apkg-Endung — versuche trotzdem zu lesen.")

    ui.header("📦 Anki-Import", os.path.basename(apkg_path))
    ui.info("Extrahiere Karten aus .apkg-Archiv...")

    try:
        cards = _extract_anki2(apkg_path)
    except FileNotFoundError as e:
        ui.error(str(e))
        sys.exit(1)
    except zipfile.BadZipFile:
        ui.error(f"'{apkg_path}' ist kein gültiges ZIP/APKG-Archiv.")
        sys.exit(1)
    except Exception as e:
        ui.error(f"Fehler beim Lesen des Decks: {e}")
        sys.exit(1)

    if not cards:
        ui.warn("Keine Karten gefunden.")
        return

    if limit and 0 < limit < len(cards):
        cards = cards[:limit]

    # Deck-Statistik
    decks = {}
    for c in cards:
        decks.setdefault(c["deck"], 0)
        decks[c["deck"]] += 1

    ui.success(f"{len(cards)} Karten in {len(decks)} Deck(s) gefunden:")
    for deck_name, count in sorted(decks.items()):
        print(f"  {ui.c('•', ui.BRIGHT_CYAN)} {deck_name}: {count} Karten")

    if dry_run:
        print()
        ui.section("Vorschau (erste 5 Karten):")
        for c in cards[:5]:
            mod = module or c["deck"]
            print(f"\n  {ui.c('Vorne:', ui.BOLD)} {c['front'][:80]}")
            print(f"  {ui.c('Hinten:', ui.DIM)} {c['back'][:80]}")
            print(f"  {ui.c('Modul:', ui.DIM)} {mod}")
            if c["tags"]:
                print(f"  {ui.c('Tags:', ui.DIM)} {', '.join(c['tags'][:5])}")
        ui.info("Dry-Run: Nichts importiert. Führe ohne --dry-run aus.")
        return

    if not ui.confirm(f"Alle {len(cards)} Karten importieren?", True):
        return

    from lernos.db.schema import startup
    from lernos.db.topics import create_topic, get_topic_by_name

    conn = startup()
    added = 0; skipped = 0; errors = 0

    for c in cards:
        mod   = module or c["deck"]
        name  = c["front"]
        desc  = c["back"]

        if not name:
            skipped += 1
            continue
        if get_topic_by_name(conn, name):
            skipped += 1
            continue

        try:
            create_topic(conn, name, mod, desc)
            added += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                ui.warn(f"Fehler bei '{name[:40]}': {e}")

    print()
    ui.success(f"Import abgeschlossen: {added} hinzugefügt, {skipped} übersprungen"
               + (f", {errors} Fehler" if errors else ""))

    if added:
        ui.info("Embeddings & Kanten nachträglich holen:")
        print(f"  {ui.c('lernos add <name> --module <modul>', ui.BRIGHT_CYAN)}  (für einzelne Topics)")
        ui.info("Alle neuen Topics reviewen:")
        print(f"  {ui.c('lernos review --all', ui.BRIGHT_CYAN)}")
