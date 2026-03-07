"""
LernOS — `lernos edit`

Ändert Name, Modul und/oder Beschreibung eines Topics ohne den Lernfortschritt
(EF, Intervall, Wiederholungen, Zustand) zu berühren.

Interaktiver Modus: Alle Felder werden als vorausgefüllte Prompts angezeigt.
Direktmodus: --name / --module / --desc überspringen die Prompts.
"""
from __future__ import annotations
import sys
import click

from lernos import ui
from lernos.completion_helpers import complete_topic_names, complete_due_topic_names, complete_module_names


@click.command("edit")
@click.argument("topic_name", shell_complete=complete_topic_names)
@click.option("--name",   "-n", default=None, help="Neuer Name")
@click.option("--module", "-m", default=None, help="Neues Modul")
@click.option("--desc",   "-d", default=None, help="Neue Beschreibung")
@click.option("--yes",    "-y", is_flag=True,
              help="Änderungen ohne Bestätigung sofort speichern")
def cmd_edit(topic_name: str, name: str | None, module: str | None,
             desc: str | None, yes: bool):
    """
    Topic-Metadaten bearbeiten (Name, Modul, Beschreibung).

    \b
    Lernfortschritt (EF, Intervall, Wiederholungen, Zustand) bleibt erhalten.

    \b
    Beispiele:
      lernos edit "Grenzwrte"                    # Interaktiv alle Felder
      lernos edit "Grenzwrte" --name "Grenzwerte"
      lernos edit "Analysis" --module "Analysis II" --yes
    """
    from lernos.db.schema import startup
    from lernos.db.topics import update_topic, get_topic_by_name
    from lernos.fuzzy.resolve import resolve_topic

    conn  = startup()
    topic = resolve_topic(conn, topic_name)

    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)

    ui.header(f"✏️  Bearbeiten: {topic.name}", f"ID={topic.id}  |  {topic.state}")

    # Wenn kein Direktflag gesetzt: interaktiver Modus (vorausgefüllte Prompts)
    interactive = (name is None and module is None and desc is None)

    if interactive:
        ui.info("ENTER = Wert beibehalten  |  Text eintippen = überschreiben")
        new_name   = ui.prompt("Name",        topic.name)
        new_module = ui.prompt("Modul",       topic.module or "")
        new_desc   = ui.prompt("Beschreibung", topic.description or "")
    else:
        new_name   = name   if name   is not None else topic.name
        new_module = module if module is not None else topic.module
        new_desc   = desc   if desc   is not None else topic.description

    # Diff anzeigen
    changes = []
    if new_name   != topic.name:
        changes.append(("Name",        topic.name,        new_name))
    if new_module != (topic.module or ""):
        changes.append(("Modul",       topic.module or "—", new_module or "—"))
    if new_desc   != (topic.description or ""):
        changes.append(("Beschreibung",
                        (topic.description or "")[:60] or "—",
                        (new_desc or "")[:60] or "—"))

    if not changes:
        ui.info("Keine Änderungen.")
        return

    print()
    ui.section("Änderungen")
    for field, old_val, new_val in changes:
        print(f"  {ui.c(field + ':', ui.DIM):<20} "
              f"{ui.c(str(old_val), ui.BRIGHT_RED)}  →  "
              f"{ui.c(str(new_val), ui.BRIGHT_GREEN)}")
    print()

    if not yes:
        if not ui.confirm("Speichern?", default=True):
            ui.info("Abgebrochen.")
            return

    # Name-Konflikt prüfen
    if new_name != topic.name:
        existing = get_topic_by_name(conn, new_name)
        if existing and existing.id != topic.id:
            ui.error(f"Topic '{new_name}' existiert bereits (ID={existing.id}).")
            sys.exit(1)

    update_topic(
        conn, topic.id,
        name        = new_name   if new_name   != topic.name        else None,
        module      = new_module if new_module != (topic.module or "") else None,
        description = new_desc   if new_desc   != (topic.description or "") else None,
    )

    ui.success(f"Topic aktualisiert.")
    if new_name != topic.name:
        ui.info(f"Name: '{topic.name}' → '{new_name}'")
    if new_module != (topic.module or ""):
        ui.info(f"Modul: '{topic.module or '—'}' → '{new_module or '—'}'")


@click.command("edit-batch")
@click.option("--module-old", "-mo", default="",
              help="Quelmodul: alle Topics aus diesem Modul umbenennen")
@click.option("--module-new", "-mn", default="",
              help="Zielmodul: Topics in dieses Modul verschieben")
@click.option("--state",  "-s", default="",
              help="Nur Topics mit diesem Zustand (NEW/LEARNING/REVIEW/MASTERED)")
@click.option("--rename-module", is_flag=True, default=False,
              help="Modul selbst umbenennen (--module-old=alt --module-new=neu)")
@click.option("--yes", "-y", is_flag=True)
def cmd_edit_batch(module_old: str, module_new: str, state: str,
                   rename_module: bool, yes: bool):
    """
    Massenbearbeitung: Topics eines Moduls in ein anderes verschieben.

    \b
    Beispiele:
      # Alle Topics von "Analysis" nach "Analysis II" verschieben
      lernos edit-batch --module-old "Analysis" --module-new "Analysis II"

      # Nur MASTERED-Topics verschieben
      lernos edit-batch --module-old "Analysis" --module-new "Archiv" --state MASTERED

      # Modul umbenennen (alle Topics gleichzeitig)
      lernos edit-batch --module-old "Mathe" --module-new "Mathematik" --rename-module

      # Alle Topics eines Moduls löschen? → nutze delete mit --module Filter
    """
    from lernos.db.schema import startup
    from lernos.db.topics import get_all_topics, update_topic

    if not module_old:
        ui.error("--module-old ist erforderlich.")
        import sys; sys.exit(1)
    if not module_new:
        ui.error("--module-new ist erforderlich.")
        import sys; sys.exit(1)
    if module_old == module_new:
        ui.error("Quell- und Zielmodul sind identisch.")
        import sys; sys.exit(1)

    conn   = startup()
    topics = get_all_topics(conn, module=module_old)

    if state:
        topics = [t for t in topics if t.state == state.upper()]

    if not topics:
        ui.info(f"Keine Topics in Modul '{module_old}'"
                + (f" mit Zustand {state.upper()}" if state else "") + ".")
        return

    ui.header(
        f"📦 Batch-Edit: {len(topics)} Topics",
        f"Modul: '{module_old}' → '{module_new}'"
        + (f"  |  Zustand-Filter: {state.upper()}" if state else "")
    )

    # Vorschau (max. 10 anzeigen)
    for t in topics[:10]:
        print(f"  {ui.c('•', ui.BRIGHT_CYAN)} {t.name}  "
              f"{ui.c(f'[{t.state}]', ui.DIM)}")
    if len(topics) > 10:
        print(f"  {ui.c(f'... und {len(topics)-10} weitere', ui.DIM)}")
    print()

    if not (yes or ui._yes_all or ui.confirm(
            f"Alle {len(topics)} Topics nach '{module_new}' verschieben?",
            default=True)):
        ui.info("Abgebrochen.")
        return

    updated = 0
    for t in topics:
        if update_topic(conn, t.id, module=module_new):
            updated += 1

    ui.success(f"{updated} Topics nach '{module_new}' verschoben.")

    if rename_module and updated > 0:
        # Prüfen ob noch Topics im alten Modul verbleiben
        remaining = get_all_topics(conn, module=module_old)
        if remaining:
            ui.info(f"Noch {len(remaining)} Topics verbleiben in '{module_old}' "
                    f"(z.B. anderer Zustand-Filter).")
        else:
            ui.success(f"Modul '{module_old}' ist jetzt leer.")
