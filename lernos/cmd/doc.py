"""
LernOS — app doc
PDF-Dokumente mit Topics verknüpfen + KI-Fragen generieren.

Befehle:
  lernos doc attach <topic> <pdf>   PDF anhängen + Fragen generieren
  lernos doc list <topic>           Dokumente eines Topics anzeigen
  lernos doc questions <topic>      Generierte Fragen anzeigen/verwalten
  lernos doc review <topic>         Fragen-basierte Review-Session
  lernos doc remove <doc_id>        Dokument entfernen
"""
from __future__ import annotations
import os
import shutil
import sys
import click

from lernos import ui
from lernos.db.topics import (
    get_documents_for_topic, get_questions_for_topic,
    add_document, add_question, delete_document, delete_questions_for_topic,
    mark_question_used, get_document_by_id,
)
from lernos.fuzzy.resolve import resolve_topic


@click.group("doc")
def cmd_doc():
    """PDF-Dokumente mit Topics verknüpfen und KI-Fragen generieren."""



def _count_pdf_pages(pdf_path: str) -> int:
    """Zählt PDF-Seiten ohne Text-Extraktion — für rein bildbasierte PDFs."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception:
        pass
    try:
        # Fallback: rohe Bytesuche nach /Type /Page
        with open(pdf_path, "rb") as f:
            data = f.read()
        return data.count(b"/Type /Page")
    except Exception:
        return 0

@cmd_doc.command("attach")
@click.argument("topic_name")
@click.argument("pdf_path")
@click.option("--count", "-n", default=5, help="Anzahl zu generierender Fragen")
@click.option("--model", default="phi3", help="Ollama-Text-Modell")
@click.option("--no-questions", is_flag=True, help="Keine Fragen generieren")
@click.option("--vision", is_flag=True, help="Vision-Modell für Fragen (llava/llama3.2-vision)")
@click.option("--vision-model", default=None, help="Spezifisches Vision-Modell (Standard: auto)")
@click.option("--dpi", default=96, type=click.Choice(["72","96","150"]),
              help="Rendering-DPI für Vision-Modus (Standard: 96)")
def doc_attach(topic_name: str, pdf_path: str, count: int,
               model: str, no_questions: bool,
               vision: bool, vision_model: str, dpi: str):
    """PDF anhängen, Text extrahieren, Fragen generieren."""
    from lernos.db.schema import startup, get_docs_dir
    from lernos.pdf.reader import extract_pdf
    from lernos.pdf.questions import generate_questions
    from lernos.ollama.embed import is_ollama_running

    conn = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)

    if not os.path.exists(pdf_path):
        ui.error(f"Datei nicht gefunden: {pdf_path}")
        sys.exit(1)

    if not pdf_path.lower().endswith(".pdf"):
        ui.warn("Warnung: Datei scheint keine PDF zu sein. Fortfahren?")
        if not ui.confirm("Fortfahren?", False):
            return

    ui.header(f"📎 PDF anhängen: {os.path.basename(pdf_path)}", f"Topic: {topic.name}")

    # 1. PDF extrahieren
    # Im Vision-Modus ist leerer Text kein Fehler — die PDF wird als Bilder
    # analysiert. PDFEmptyError wird in diesem Fall abgefangen und ignoriert.
    ui.info("Extrahiere Text aus PDF...")
    from lernos.pdf.reader import PDFPasswordError, PDFEmptyError, PDFCorruptError
    try:
        info = extract_pdf(pdf_path)
    except PDFPasswordError as e:
        ui.error(str(e))
        ui.info("Tipp: PDF entsperren mit:  qpdf --decrypt input.pdf output.pdf")
        sys.exit(1)
    except PDFEmptyError as e:
        if not vision:
            # Nur ohne Vision ist leere PDF ein fataler Fehler
            ui.error(str(e))
            if "gescanntes Bild" in str(e) or "OCR" in str(e):
                ui.info("Tipp: Mit --vision direkt als Bild analysieren:")
                ui.info(f"  lernos doc attach \"{topic.name}\" {os.path.basename(pdf_path)} --vision")
            sys.exit(1)
        # Vision-Modus: PDFEmptyError tolerieren — Fallback auf leeres PDFInfo
        ui.warn("Kein extrahierbarer Text — Vision-Modus analysiert Seiten direkt als Bilder.")
        from lernos.pdf.reader import PDFInfo
        info = PDFInfo(
            filepath=os.path.abspath(pdf_path),
            filename=os.path.basename(pdf_path),
            page_count=_count_pdf_pages(pdf_path),
            file_size=os.path.getsize(pdf_path),
            full_text="",
            text_excerpt="",
            pages=[],
            is_presentation=True,   # Bild-PDFs sind meistens Präsentationen
            warnings=["⚠ Kein Textinhalt — Vision-Modus aktiv."],
        )
    except PDFCorruptError as e:
        ui.error(str(e))
        sys.exit(1)
    except Exception as e:
        ui.error(f"Unerwarteter PDF-Fehler: {e}")
        sys.exit(1)

    size_kb = info.file_size // 1024
    pres_hint = "  📊 Präsentation" if info.is_presentation else ""
    ui.success(f"{info.page_count} Seiten · {size_kb} KB · {len(info.full_text)} Zeichen{pres_hint}")

    # Warnungen aus der Extraktion anzeigen (Formeln, Bilder, wenig Text)
    for w in info.warnings:
        ui.warn(w)

    # Vorschau
    if info.text_excerpt:
        ui.section("Text-Vorschau")
        print(f"  {ui.c(info.text_excerpt[:300], ui.DIM)}")
        print()

    # 2. Datei in Docs-Verzeichnis kopieren
    docs_dir  = get_docs_dir()
    dest_name = f"{topic.id}_{os.path.basename(pdf_path)}"
    dest_path = os.path.join(docs_dir, dest_name)
    shutil.copy2(pdf_path, dest_path)

    # 3. In DB speichern
    doc = add_document(
        conn, topic.id,
        filename=info.filename,
        filepath=dest_path,
        file_size=info.file_size,
        page_count=info.page_count,
        text_excerpt=info.text_excerpt,
        full_text=info.full_text,
    )
    ui.success(f"Dokument gespeichert (ID={doc.id})")

    # Im Vision-Modus ist leerer full_text kein Abbruchgrund — die Pipeline
    # rendert Seiten als Bilder und fragt das Vision-LLM direkt.
    skip_questions = no_questions or (not info.full_text.strip() and not vision)
    if skip_questions:
        if no_questions:
            ui.info("Fragen-Generierung übersprungen (--no-questions).")
        elif not info.full_text.strip():
            ui.warn("Kein Text extrahiert und kein --vision Flag — keine Fragen generierbar.")
            ui.info(f"Tipp: lernos doc attach \"{topic.name}\" {os.path.basename(pdf_path)} --vision")
        return

    # 4. Fragen generieren
    ui.section("Fragen generieren")
    ollama_ok = is_ollama_running()

    if vision:
        from lernos.pdf.vision import check_vision_dependencies
        deps = check_vision_dependencies()
        if deps["vision_model"]:
            ui.info(f"Vision-Modus: {deps['vision_model']} · {dpi} DPI")
        else:
            ui.warn("Kein Vision-Modell in Ollama gefunden. Verfügbare Modelle installieren:")
            ui.info("  ollama pull llava       (empfohlen, ~4GB)")
            ui.info("  ollama pull llava-phi3  (kleiner, ~3GB)")
            ui.info("  ollama pull llama3.2-vision (aktuell, ~8GB)")
            ui.info("Weiter mit Text-LLM Fallback...")
    elif ollama_ok:
        mode_hint = " [Präsentation → folienweise]" if info.is_presentation else ""
        ui.info(f"Generiere {count} Fragen via Ollama ({model}){mode_hint}...")
    else:
        ui.warn(f"Ollama nicht verfügbar — nutze Heuristik-Extraktion.")

    # Fragen generieren
    if vision and dest_path:
        try:
            from lernos.pdf.vision import (
                generate_questions_from_pdf_vision,
                get_available_vision_model,
                _format_slide_status,
            )
            _model = vision_model or get_available_vision_model()
            if not _model:
                ui.error("Kein Vision-Modell in Ollama gefunden.")
                ui.info("Installieren mit: ollama pull llava")
                ui.info("Oder prüfen mit:  lernos setup --vision")
                return

            qs_raw, _used_model, slide_results = generate_questions_from_pdf_vision(
                filepath=dest_path,
                topic_name=topic.name,
                count=count,
                model=_model,
                dpi=int(dpi),
                verbose=True,
            )

            # Folienstatus ausgeben
            if slide_results:
                print()
                ui.section("Folien-Analyse")
                hw_count  = 0
                skip_count = 0
                for r in slide_results:
                    print(_format_slide_status(r))
                    pt = r["page_type"]
                    if pt.get("has_handwriting"):
                        hw_count += 1
                    if not pt.get("has_printed_text") and not pt.get("has_technical_diagram"):
                        skip_count += 1

                if hw_count:
                    print()
                    ui.warn(
                        f"Handschrift auf {hw_count}/{len(slide_results)} Folien erkannt "
                        f"— nur gedruckter Folientext wurde für Fragen genutzt."
                    )
                if skip_count:
                    ui.info(
                        f"{skip_count} Folie(n) ohne verwertbaren Inhalt übersprungen."
                    )

            questions    = qs_raw
            ai_generated = bool(qs_raw)

        except ImportError as _ie:
            ui.error(f"Vision-Abhängigkeit fehlt: {_ie}")
            ui.info("Beheben mit: lernos setup --vision")
            return
        except RuntimeError as _re:
            ui.error(str(_re))
            ui.info("Beheben mit: lernos setup --vision")
            return
        except Exception as _ve:
            ui.warn(f"Vision-Pipeline-Fehler: {_ve} — kein Vision-Ergebnis.")
            questions, ai_generated = [], False
    else:
        questions, ai_generated = generate_questions(
            info.full_text, topic.name, count=count, model=model,
            pages=info.pages if info.pages else None,
            is_presentation=info.is_presentation,
            use_vision=False,
            vision_model=None,
            pdf_path=dest_path,
            vision_dpi=int(dpi),
        )

    if not questions:
        ui.warn("Keine Fragen generiert.")
        return

    source = ui.c("KI (Ollama)", ui.BRIGHT_GREEN) if ai_generated else ui.c("Heuristik", ui.BRIGHT_YELLOW)
    ui.success(f"{len(questions)} Fragen generiert via {source}")
    print()

    # 5. Fragen anzeigen und bestätigen
    for i, q in enumerate(questions, 1):
        diff_stars = "★" * q["difficulty"] + "☆" * (5 - q["difficulty"])
        print(f"  {ui.c(f'[{i}]', ui.BRIGHT_CYAN)} {ui.c(diff_stars, ui.BRIGHT_YELLOW)} "
              f"{ui.c(q.get('type',''), ui.DIM)}")
        print(f"      {ui.c('F:', ui.BOLD)} {q['question'][:120]}")
        if q["answer"]:
            print(f"      {ui.c('A:', ui.DIM)} {q['answer'][:100]}...")
        print()

    save = ui.confirm(f"Alle {len(questions)} Fragen speichern?", True)
    if not save:
        # Einzeln auswählen
        for i, q in enumerate(questions, 1):
            keep = ui.confirm(f"  Frage {i} speichern?", True)
            if keep:
                add_question(conn, topic.id, q["question"], q["answer"],
                             q.get("difficulty", 3), doc.id)
        ui.success("Ausgewählte Fragen gespeichert.")
    else:
        for q in questions:
            add_question(conn, topic.id, q["question"], q["answer"],
                         q.get("difficulty", 3), doc.id)
        ui.success(f"Alle {len(questions)} Fragen gespeichert.")

    ui.info(f"Review starten mit: lernos doc review \"{topic.name}\"")
    print()


@cmd_doc.command("list")
@click.argument("topic_name")
def doc_list(topic_name: str):
    """Alle Dokumente eines Topics anzeigen."""
    from lernos.db.schema import startup

    conn = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)

    docs = get_documents_for_topic(conn, topic.id)
    ui.header(f"📎 Dokumente: {topic.name}", f"{len(docs)} Dokument(e)")

    if not docs:
        ui.info("Keine Dokumente angehängt.")
        ui.info(f"Anhängen mit: lernos doc attach \"{topic.name}\" <datei.pdf>")
        return

    for doc in docs:
        size_kb = doc.file_size // 1024
        q_count = len(get_questions_for_topic(conn, topic.id))
        exists  = "✅" if os.path.exists(doc.filepath) else "❌"
        print(f"\n  {exists} {ui.c(f'[ID={doc.id}]', ui.BRIGHT_BLACK)} "
              f"{ui.c(doc.filename, ui.BOLD)}")
        print(f"     {ui.c(f'{doc.page_count} Seiten  |  {size_kb} KB  |  '
              f'{q_count} Fragen', ui.DIM)}")
        if doc.text_excerpt:
            print(f"     {ui.c(doc.text_excerpt[:120] + '…', ui.DIM)}")
    print()


@cmd_doc.command("questions")
@click.argument("topic_name")
@click.option("--regenerate", "-r", is_flag=True, help="Fragen neu generieren")
@click.option("--count", "-n", default=5)
@click.option("--model", default="phi3")
@click.option("--vision", is_flag=True, help="Vision-Modell für Regenerierung nutzen")
@click.option("--vision-model", default=None)
@click.option("--dpi", default=96, type=click.Choice(["72","96","150"]))
def doc_questions(topic_name: str, regenerate: bool, count: int, model: str,
                  vision: bool, vision_model: str, dpi: str):
    """Generierte Fragen eines Topics anzeigen oder neu generieren."""
    from lernos.db.schema import startup
    from lernos.pdf.questions import generate_questions
    from lernos.ollama.embed import is_ollama_running

    conn = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)

    docs = get_documents_for_topic(conn, topic.id)

    if regenerate:
        if not docs:
            ui.error("Keine Dokumente vorhanden. Zuerst PDF anhängen.")
            sys.exit(1)
        ui.info("Lösche bestehende Fragen und generiere neu...")
        deleted = delete_questions_for_topic(conn, topic.id)
        ui.info(f"{deleted} alte Fragen gelöscht.")

        # Alle Docs zusammenführen
        combined_text = "\n\n".join(d.full_text for d in docs if d.full_text)
        # Seitenstruktur aus dem ersten Dokument nutzen falls vorhanden
        first_pages = None
        first_is_pres = False
        try:
            from lernos.pdf.reader import extract_pdf as _re_pdf, PDFEmptyError
            if docs:
                try:
                    _info = _re_pdf(docs[0].filepath)
                    first_pages   = _info.pages
                    first_is_pres = _info.is_presentation
                except PDFEmptyError:
                    # Bild-PDF — Vision-Modus nutzt direkt den Dateipfad
                    first_is_pres = True
                    first_pages   = None
        except Exception:
            pass
        # Ersten Dokument-Pfad für Vision-Pipeline
        first_pdf_path = docs[0].filepath if docs else None
        qs, ai = generate_questions(
            combined_text, topic.name, count, model,
            pages=first_pages,
            is_presentation=first_is_pres,
            use_vision=vision,
            vision_model=vision_model or None,
            pdf_path=first_pdf_path,
            vision_dpi=int(dpi),
        )
        for q in qs:
            add_question(conn, topic.id, q["question"], q["answer"],
                         q.get("difficulty", 3))
        source = "KI" if ai else "Heuristik"
        ui.success(f"{len(qs)} neue Fragen generiert ({source}).")

    questions = get_questions_for_topic(conn, topic.id, unused_first=False)
    ui.header(f"❓ Fragen: {topic.name}", f"{len(questions)} Frage(n)")

    if not questions:
        ui.info("Keine Fragen vorhanden.")
        if docs:
            ui.info("Generieren mit: lernos doc questions --regenerate")
        else:
            ui.info("Zuerst PDF anhängen: lernos doc attach")
        return

    for i, q in enumerate(questions, 1):
        diff_stars = ui.c("★" * q.difficulty + "☆" * (5 - q.difficulty), ui.BRIGHT_YELLOW)
        used_str   = ui.c(f"  (genutzt: {q.used_count}×)", ui.DIM)
        print(f"\n  {ui.c(str(i).rjust(2), ui.BRIGHT_BLACK)}. {diff_stars}{used_str}")
        print(f"     {ui.c('F:', ui.BOLD)} {q.question}")
        if q.answer:
            print(f"     {ui.c('A:', ui.DIM)} {ui.c(q.answer[:200], ui.DIM)}")
    print()


@cmd_doc.command("review")
@click.argument("topic_name")
@click.option("--count", "-n", default=3, help="Anzahl Fragen pro Session")
@click.option("--all-questions", is_flag=True, help="Alle Fragen, nicht nur ungenutzte")
def doc_review(topic_name: str, count: int, all_questions: bool):
    """
    Fragen-basierte Review-Session.
    Nutzt generierte Fragen aus angehängten Dokumenten.
    Integriert sich vollständig in den SM-2-Workflow.
    """
    from lernos.db.schema import startup

    conn = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)

    questions = get_questions_for_topic(conn, topic.id, unused_first=not all_questions)
    if not questions:
        ui.error(f"Keine Fragen für '{topic.name}'. Zuerst PDF anhängen.")
        sys.exit(1)

    selected = questions[:count]
    ui.header(f"❓ Fragen-Review: {topic.name}",
              f"{len(selected)} von {len(questions)} Fragen")

    correct_count = 0
    for idx, q in enumerate(selected, 1):
        print(f"\n  {ui.c(f'[{idx}/{len(selected)}]', ui.BRIGHT_BLACK)}")
        diff_str = "★" * q.difficulty + "☆" * (5 - q.difficulty)
        print(f"  {ui.c(diff_str, ui.BRIGHT_YELLOW)}\n")
        print(f"  {ui.c('Frage:', ui.BOLD)}")
        print(f"  {q.question}\n")

        input(f"  {ui.c('[ ENTER zum Aufdecken... ]', ui.DIM)}")
        print()
        print(f"  {ui.c('Antwort:', ui.BOLD, ui.BRIGHT_CYAN)}")
        print(f"  {q.answer}\n")

        mark_question_used(conn, q.id)

        correct = ui.confirm("Warst du korrekt?", True)
        if correct:
            correct_count += 1
            print(f"  {ui.c('✅ Gut!', ui.BRIGHT_GREEN)}")
        else:
            print(f"  {ui.c('❌ Noch üben.', ui.BRIGHT_RED)}")

        if idx < len(selected):
            cont = ui.prompt("\n  Weiter? (J/n)", "j").lower()
            if cont in ("n", "nein"):
                break

    # Zusammenfassung
    print()
    acc = round(correct_count / len(selected) * 100)
    bar = ui.progress_bar(acc, max_val=100, width=20)
    ui.section("Session abgeschlossen")
    print(f"  Ergebnis: {bar} {acc}%  ({correct_count}/{len(selected)} richtig)")

    # Normalen SM-2-Review vorschlagen
    if topic.is_due:
        print()
        ui.info(f"'{topic.name}' ist fällig. SM-2 Review starten?")
        if ui.confirm("Ja", True):
            from lernos.cmd.review import _do_review
            _do_review(conn, topic)
    print()


@cmd_doc.command("remove")
@click.argument("doc_id", type=int)
def doc_remove(doc_id: int):
    """Dokument und zugehörige Fragen entfernen."""
    from lernos.db.schema import startup

    conn = startup()
    doc = get_document_by_id(conn, doc_id)
    if not doc:
        ui.error(f"Dokument ID={doc_id} nicht gefunden.")
        sys.exit(1)

    ui.warn(f"Entferne: {doc.filename} (ID={doc.id})")
    if not ui.confirm("Sicher?", False):
        return

    # Datei löschen wenn vorhanden
    if os.path.exists(doc.filepath):
        os.remove(doc.filepath)

    delete_document(conn, doc.id)
    ui.success(f"Dokument {doc.filename} entfernt.")


@cmd_doc.command("open")
@click.argument("topic_name",
                shell_complete=lambda c, p, i: __import__(
                    'lernos.completion_helpers', fromlist=['complete_topic_names']
                ).complete_topic_names(c, p, i))
@click.argument("doc_id", type=int, required=False)
def doc_open(topic_name: str, doc_id: int | None):
    """
    PDF im Standard-Viewer des Systems öffnen.

    \b
    Linux: xdg-open  |  macOS: open  |  Windows: start

    \b
    Beispiele:
      lernos doc open "Kettenregel"        # Erstes PDF öffnen
      lernos doc open "Kettenregel" 3      # Dokument mit ID=3 öffnen
    """
    import subprocess, platform

    conn  = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)

    if doc_id is not None:
        doc = get_document_by_id(conn, doc_id)
        if not doc or doc.topic_id != topic.id:
            ui.error(f"Dokument ID={doc_id} für '{topic.name}' nicht gefunden.")
            sys.exit(1)
    else:
        docs = get_documents_for_topic(conn, topic.id)
        if not docs:
            ui.error(f"Keine Dokumente für '{topic.name}'.")
            ui.info(f"Anhängen mit: lernos doc attach \"{topic.name}\" datei.pdf")
            sys.exit(1)
        if len(docs) == 1:
            doc = docs[0]
        else:
            labels = [f"{d.filename}  {ui.c(f'(ID={d.id})', ui.DIM)}" for d in docs]
            idx    = ui.select(f"Dokument für '{topic.name}' öffnen", labels)
            doc    = docs[idx]

    if not doc.filepath or not os.path.exists(doc.filepath):
        ui.error(f"Datei nicht gefunden: {doc.filepath}")
        ui.info("Datei wurde möglicherweise manuell verschoben oder gelöscht.")
        sys.exit(1)

    system = platform.system()
    cmd = (["open", doc.filepath] if system == "Darwin"
           else ["start", "", doc.filepath] if system == "Windows"
           else ["xdg-open", doc.filepath])

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ui.success(f"Öffne: {doc.filename}")
        ui.info(f"Pfad: {doc.filepath}")
    except FileNotFoundError:
        ui.error(f"'{cmd[0]}' nicht gefunden.")
        ui.info(f"Datei liegt unter: {doc.filepath}")
