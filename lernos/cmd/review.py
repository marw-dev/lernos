"""
LernOS — Review-Session
Vollständig implementiert:
  - Standard-Review (2 Eingaben: Konfidenz + Grade)
  - Active-Recall-Modus (Antwort eintippen → KI oder lokaler Fallback bewertet)
  - Fragen-basierter Review (--questions): nutzt generated_questions aus DB
  - Session-Limit (--limit) und Zeitlimit (--time)
  - Vollständiges Feedback mit EF-Verlauf, Kaskaden-Info
"""
from __future__ import annotations

import sys
import time

import click

from lernos import ui
from lernos.completion_helpers import complete_topic_names, complete_due_topic_names, complete_module_names
from lernos.db.topics import (
    STATE_EMOJI,
    get_due_topics,
    get_topic_by_id,
    log_session,
    update_topic_sm2,
    get_documents_for_topic,
    get_questions_for_topic,
    mark_question_used,
)
from lernos.fuzzy.resolve import resolve_topic
from lernos.graph.topo import get_prerequisites
from lernos.sm2.algorithm import (
    CONFIDENCE_DESCRIPTIONS,
    GRADE_DESCRIPTIONS,
    calculate,
)
from lernos.sm2.cascade import STATE_LEARNING, cascade_review


@click.command("review")
@click.argument("topic_name", required=False, shell_complete=complete_due_topic_names)
@click.option("--module", "-m", default="", help="Nur Topics aus diesem Modul")
@click.option("--all", "review_all", is_flag=True, help="Alle fälligen Topics")
@click.option(
    "--active", is_flag=True,
    help="Active-Recall-Modus: Antwort eintippen, KI oder Fallback bewertet automatisch"
)
@click.option(
    "--questions", "-q", is_flag=True,
    help="Fragen-Modus: Nutzt KI-generierte Fragen aus angehängten PDFs"
)
@click.option("--limit", "-l", type=int, default=0, help="Max. Karten pro Session")
@click.option(
    "--time", "-t", "time_limit", type=int, default=0,
    help="Zeitlimit in Minuten (0 = unbegrenzt)"
)
@click.option(
    "--fix-order", "fix_order", is_flag=True,
    help="Topologische Reihenfolge: Voraussetzungen zuerst (statt Fälligkeitsdatum)"
)
@click.option(
    "--web", "-w", "web_mode", is_flag=True,
    help="Browser-Review starten (HTML + lokaler Server)"
)
@click.option(
    "--port", default=0,
    help="Port für den Web-Review-Server (0 = automatisch)"
)
@click.option(
    "--output", "web_output", default="",
    help="HTML auch als Datei speichern (z.B. ~/review.html)"
)
def cmd_review(
    topic_name: str | None,
    module: str,
    review_all: bool,
    active: bool,
    questions: bool,
    limit: int,
    time_limit: int,
    fix_order: bool,
    web_mode: bool,
    port: int,
    web_output: str,
):
    """
    Review-Session starten.

    \b
    Modi:
      Standard:   lernos review              (fälligstes Topic)
      Alle:       lernos review --all
      Fuzzy:      lernos review taylr
      Aktiv:      lernos review --active      (Antwort eintippen)
      Fragen:     lernos review --questions   (aus PDF-Dokumenten)
      Topo:       lernos review --all --fix-order  (Voraussetzungen zuerst)
      Web:        lernos review --web         (Browser-Interface)
      Web+Aktiv:  lernos review --web --active
      Limit:      lernos review --all --limit 10
      Zeit:       lernos review --all --time 20
    """
    from lernos.db.schema import startup
    conn = startup()

    # ── Web-Modus: HTML-Review im Browser ────────────────────────────────
    if web_mode:
        _run_web_review(conn, topic_name, module, review_all, active, questions,
                        limit, port, web_output, fix_order=fix_order)
        return

    if topic_name:
        topic = resolve_topic(conn, topic_name)
        if not topic:
            ui.error(f"Topic '{topic_name}' nicht gefunden.")
            sys.exit(1)
        topics = [topic]
    else:
        topics = get_due_topics(conn)
        if module:
            topics = [t for t in topics if t.module.lower() == module.lower()]
        if not topics:
            ui.success("Heute nichts fällig! Genieß den Tag. 🎉")
            return
        # --fix-order: topologische Sortierung (Voraussetzungen zuerst)
        if fix_order and (review_all or limit or time_limit):
            topics = _topo_sort_due(conn, topics)
        elif not review_all and not limit and not time_limit:
            topics = [topics[0]]

    if limit and 0 < limit < len(topics):
        topics = topics[:limit]

    total = len(topics)
    if fix_order:
        mode_str = "Fragen-Modus [Topo]" if questions else ("Active-Recall [Topo]" if active else "Topo-Reihenfolge")
    else:
        mode_str = "Fragen-Modus" if questions else ("Active-Recall" if active else "Standard")
    ui.header(
        "📚 Review-Session",
        f"{total} Topic{'s' if total > 1 else ''}  |  {mode_str}"
        + (f"  |  ⏳ {time_limit}min" if time_limit else ""),
    )

    results = []
    start_time = time.time()

    for idx, topic in enumerate(topics, 1):
        if time_limit and (time.time() - start_time) / 60 >= time_limit:
            ui.warn(f"⏳ Zeitlimit von {time_limit} Minuten erreicht!")
            break

        if total > 1:
            elapsed = (time.time() - start_time) / 60
            remaining = f"  ⏳ {time_limit - elapsed:.0f}min verbleibend" if time_limit else ""
            print(f"\n  {ui.c(f'[{idx}/{total}]', ui.BRIGHT_BLACK)}{remaining}")

        if questions:
            result = _do_question_review(conn, topic)
        else:
            result = _do_review(conn, topic, active)

        results.append(result)

        if total > 1 and idx < total:
            cont = ui.prompt("Weiter? (J/n)", "j").lower()
            if cont in ("n", "nein"):
                break

    if len(results) > 1:
        _session_summary(results)


# ─────────────────────────────────────────────────────────────────────────────
# Standard-Review (mit optionalem Active-Recall)
# ─────────────────────────────────────────────────────────────────────────────

def _do_review(conn, topic, active_mode: bool) -> dict:
    """Standard SM-2 Review. 2 Eingaben (Konfidenz + Grade) oder 1 bei Active-Recall."""
    prereqs = get_prerequisites(conn, topic.id)
    _render_topic_card(topic, prereqs, conn)

    typed_ans = ""
    if active_mode:
        print()
        typed_ans = ui.prompt("📝 Deine Antwort (Stichworte reichen)", "")

    # Schritt 1: Konfidenz (VOR dem Aufdecken)
    confidence = _get_int_input(
        "Wie sicher warst du? (1-5)", 1, 5, CONFIDENCE_DESCRIPTIONS
    )

    # Aufdecken
    print()
    print(f"  {ui.c('─' * 52, ui.BRIGHT_BLACK)}")
    print(f"  {ui.c('Musterantwort / Beschreibung:', ui.DIM)}")
    if topic.description:
        print(f"  {ui.c(topic.description, ui.BRIGHT_CYAN)}")
    else:
        print(f"  {ui.c('[Keine Beschreibung hinterlegt — nur Name als Karte]', ui.DIM)}")
    print(f"  {ui.c('─' * 52, ui.BRIGHT_BLACK)}")
    print()

    # Active-Recall: KI oder lokaler Fallback bewertet die Antwort
    ai_grade   = None
    ai_src_lbl = ""
    if active_mode and typed_ans and topic.description:
        ai_grade, ai_src_lbl = _evaluate_typed_answer(topic.description, typed_ans)

    # ── Sokratischer Dialog bei Note 2 oder 3 ────────────────────────────
    if active_mode and ai_grade in (2, 3):
        src_color = ui.BRIGHT_MAGENTA if "KI" in ai_src_lbl else ui.BRIGHT_YELLOW
        print(f"  {ui.c('Erste Bewertung:', ui.BOLD)} "
              f"{ui.c(f'[{ai_grade}]', ui.BRIGHT_CYAN)} "
              f"{GRADE_DESCRIPTIONS.get(ai_grade, '')}  "
              f"{ui.c(f'({ai_src_lbl})', src_color)}")
        print()
        do_socratic = ui.prompt(
            "💬 Deine Antwort war unvollständig. "
            "Möchtest du mit einem sokratischen Tipp nachbessern? (J/n)", "j"
        ).lower()
        if do_socratic not in ("n", "nein"):
            typed_ans, ai_grade = _socratic_loop(
                expected=topic.description,
                given=typed_ans,
                grade=ai_grade,
                topic_name=topic.name,
            )
        print()

    # Schritt 2: Grade (KI-Vorschlag als Default wenn vorhanden)
    if ai_grade is not None:
        src_color = ui.BRIGHT_MAGENTA if "KI" in ai_src_lbl else ui.BRIGHT_YELLOW
        print(f"  {ui.c('Vorgeschlagene Bewertung:', ui.BOLD)} "
              f"{ui.c(f'[{ai_grade}]', ui.BRIGHT_CYAN)} "
              f"{GRADE_DESCRIPTIONS.get(ai_grade, '')}  "
              f"{ui.c(f'({ai_src_lbl})', ui.DIM)}")
    grade = _get_int_input(
        "Bewertung (0-5)", 0, 5, GRADE_DESCRIPTIONS, default=ai_grade
    )

    return _process_and_save(conn, topic, grade, confidence)


# ─────────────────────────────────────────────────────────────────────────────
# Fragen-basierter Review (aus generated_questions)
# ─────────────────────────────────────────────────────────────────────────────

def _do_question_review(conn, topic) -> dict:
    """
    Review-Session mit KI-generierten Fragen aus angehängten PDFs.
    Integriert sich vollständig in SM-2: am Ende wird der Themen-SM2-State aktualisiert.
    """
    questions = get_questions_for_topic(conn, topic.id, unused_first=True)

    if not questions:
        # Kein Fragen vorhanden → prüfen ob Dokumente da sind
        docs = get_documents_for_topic(conn, topic.id)
        if docs:
            ui.warn(
                f"'{topic.name}' hat {len(docs)} Dokument(e), aber keine Fragen."
            )
            ui.info("Fragen generieren mit: lernos doc questions --regenerate")
        else:
            ui.warn(f"'{topic.name}' hat keine Dokumente/Fragen.")
            ui.info(f"PDF anhängen mit: lernos doc attach \"{topic.name}\" datei.pdf")
        # Fallback: normalen Review machen
        ui.info("Starte Standard-Review als Fallback...")
        return _do_review(conn, topic, active_mode=False)

    prereqs = get_prerequisites(conn, topic.id)
    _render_topic_card(topic, prereqs, conn)
    ui.info(f"{len(questions)} Fragen verfügbar · {sum(1 for q in questions if q.used_count == 0)} noch unbenutzt")
    print()

    # Maximal 5 Fragen pro Session (oder alle verfügbaren)
    session_qs = questions[:min(5, len(questions))]
    correct_count = 0
    total_grade   = 0

    for q_idx, q in enumerate(session_qs, 1):
        diff_str = ui.c("★" * q.difficulty + "☆" * (5 - q.difficulty), ui.BRIGHT_YELLOW)
        print(f"  {ui.c(f'Frage {q_idx}/{len(session_qs)}', ui.BRIGHT_BLACK)}  {diff_str}")
        print()
        print(f"  {ui.c('❓', ui.BOLD)} {q.question}")
        print()

        # Antwort eintippen (Pflicht im Fragen-Modus)
        typed = ui.prompt("📝 Deine Antwort", "")
        mark_question_used(conn, q.id)

        # Antwort auswerten
        ai_grade   = None
        ai_src_lbl = ""
        if typed and q.answer:
            ai_grade, ai_src_lbl = _evaluate_typed_answer(q.answer, typed)

        # ── Sokratischer Dialog bei Note 2 oder 3 (vor Musterantwort!) ───
        if ai_grade in (2, 3):
            src_color = ui.BRIGHT_MAGENTA if "KI" in ai_src_lbl else ui.BRIGHT_YELLOW
            print()
            print(f"  {ui.c('Erste Bewertung:', ui.BOLD)} "
                  f"{ui.c(f'[{ai_grade}]', ui.BRIGHT_CYAN)} "
                  f"{GRADE_DESCRIPTIONS.get(ai_grade, '')}  "
                  f"{ui.c(f'({ai_src_lbl})', src_color)}")
            print()
            do_socratic = ui.prompt(
                "💬 Deine Antwort war unvollständig. "
                "Sokratischer Tipp zum Nachbessern? (J/n)", "j"
            ).lower()
            if do_socratic not in ("n", "nein"):
                typed, ai_grade = _socratic_loop(
                    expected=q.answer,
                    given=typed,
                    grade=ai_grade,
                    topic_name=topic.name,
                )
            print()

        # Musterantwort zeigen
        print(f"  {ui.c('─' * 52, ui.BRIGHT_BLACK)}")
        print(f"  {ui.c('Musterantwort:', ui.DIM)}")
        print(f"  {ui.c(q.answer, ui.BRIGHT_CYAN)}")
        print(f"  {ui.c('─' * 52, ui.BRIGHT_BLACK)}")
        print()

        if ai_grade is not None:
            grade_desc = GRADE_DESCRIPTIONS.get(ai_grade, "")
            src_color  = ui.BRIGHT_MAGENTA if "KI" in ai_src_lbl else ui.BRIGHT_YELLOW
            print(f"  {ui.c('Vorgeschlagene Bewertung:', ui.BOLD)} "
                  f"{ui.c(f'[{ai_grade}]', ui.BRIGHT_CYAN)} {grade_desc}  "
                  f"{ui.c(f'({ai_src_lbl})', src_color)}")

        grade = _get_int_input("Deine Bewertung (0-5)", 0, 5, GRADE_DESCRIPTIONS, default=ai_grade)
        total_grade += grade
        if grade >= 3:
            correct_count += 1

        if q_idx < len(session_qs):
            cont = ui.prompt("Nächste Frage? (J/n)", "j").lower()
            if cont in ("n", "nein"):
                break
        print()

    # Durchschnittsgrade für SM-2
    avg_grade   = round(total_grade / len(session_qs))
    avg_grade   = max(0, min(5, avg_grade))
    confidence  = 3   # Neutral für Fragen-Modus

    # Zusammenfassung der Fragen-Session
    acc = round(correct_count / len(session_qs) * 100)
    bar = ui.progress_bar(acc, max_val=100, width=25)
    print(f"\n  {ui.c('Fragen-Ergebnis:', ui.BOLD)} {bar} {acc}%  "
          f"({correct_count}/{len(session_qs)} richtig)"
          f"  →  Ø Grade: {ui.c(str(avg_grade), ui.BRIGHT_CYAN)}")
    print()

    # SM-2 mit Durchschnittsgrade aktualisieren
    return _process_and_save(conn, topic, avg_grade, confidence,
                              source="questions")


# ─────────────────────────────────────────────────────────────────────────────
# Gemeinsame Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────



def _evaluate_typed_answer(expected: str, given: str) -> tuple[int, str]:
    """
    Bewertet eine Freitextantwort.
    Gibt (grade, source_label) zurück.
    source_label: "KI ✓" | "KI Timeout" | "KI OOM" | "Lokal" | etc.
    Versucht zuerst Ollama, informiert über Fallback-Grund.
    """
    from lernos.ollama.embed import is_ollama_running, evaluate_answer, evaluate_answer_local

    if is_ollama_running():
        grade, source = evaluate_answer(expected, given)
        if grade is not None:
            return grade, "KI ✓"
        # Reason für Fallback kommunizieren
        reason_map = {
            "timeout": "KI Timeout →Fallback",
            "oom":     "KI OOM →Fallback",
            "offline": "Lokal",
        }
        label = reason_map.get(source, f"KI Fehler →Fallback")
        return evaluate_answer_local(expected, given), label

    return evaluate_answer_local(expected, given), "Lokal"


def _topo_sort_due(conn, due_topics: list) -> list:
    """
    Sortiert fällige Topics in topologischer Reihenfolge:
    Voraussetzungen erscheinen vor abhängigen Topics.

    Verwendet Kahn's Algorithmus auf dem Subgraphen der fälligen Topics.
    Topics ohne Kanten behalten ihre SM-2-Priorität (LEARNING > REVIEW > NEW).

    Beispiel:
      Ohne --fix-order: [Differenzierbarkeit (heute fällig), Stetigkeit, Grenzwerte]
      Mit    --fix-order: [Grenzwerte → Stetigkeit → Differenzierbarkeit]
    """
    from collections import deque

    if len(due_topics) <= 1:
        return due_topics

    due_ids   = {t.id for t in due_topics}
    topic_map = {t.id: t for t in due_topics}

    # Nur Kanten zwischen fälligen Topics — direktes SQL ohne topics-JOIN
    placeholders = ",".join("?" * len(due_ids))
    raw_edges = conn.execute(
        f"SELECT from_id, to_id FROM edges WHERE from_id IN ({placeholders}) AND to_id IN ({placeholders})",
        list(due_ids) + list(due_ids),
    ).fetchall()

    in_degree: dict[int, int] = {tid: 0 for tid in due_ids}
    adj: dict[int, list[int]] = {tid: [] for tid in due_ids}

    for row in raw_edges:
        in_degree[row[1]] += 1
        adj[row[0]].append(row[1])

    # Kahn's BFS — bei gleichen In-Degrees: SM-2-Priorität entscheidet
    SM2_PRIO = {"LEARNING": 0, "REVIEW": 1, "NEW": 2, "MASTERED": 3, "FROZEN": 4}

    def sm2_key(tid: int) -> int:
        return SM2_PRIO.get(topic_map[tid].state, 9)

    # Queue: alle ohne eingehende Kanten, sortiert nach SM-2-Priorität
    queue = sorted(
        [tid for tid, deg in in_degree.items() if deg == 0],
        key=sm2_key,
    )
    queue = deque(queue)
    result: list = []

    while queue:
        curr = queue.popleft()
        result.append(topic_map[curr])
        # Nachfolger: In-Degree reduzieren, bei 0 in Queue einreihen
        freed = []
        for nxt in adj[curr]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                freed.append(nxt)
        # Neue Queue-Einträge nach SM-2-Priorität einsortieren
        freed.sort(key=sm2_key)
        queue.extend(freed)

    # Bei Zykel: restliche Topics anhängen
    remaining = [t for t in due_topics if t not in result]
    result.extend(remaining)

    return result


def _socratic_loop(
    expected:  str,
    given:     str,
    grade:     int,
    topic_name: str = "",
    max_rounds: int = 2,
) -> tuple[str, int]:
    """
    Sokratischer Nachbesserungs-Dialog bei unvollständigen Antworten (Note 2-3).

    Ablauf pro Runde:
      1. KI generiert eine sokratische Rückfrage (KEIN Spoiler)
      2. Lernender tippt verbesserte Antwort (Enter = überspringen)
      3. KI bewertet neu — Note kann nur gleich bleiben oder steigen

    Args:
        expected:   Musterantwort
        given:      Erste (unvollständige) Antwort des Lernenden
        grade:      Bisherige Note (triggert nur bei 2 oder 3)
        topic_name: Für Kontext im Prompt
        max_rounds: Maximale Nachbesserungs-Runden (Standard: 2)

    Returns:
        (final_answer, final_grade) — die beste erreichte Note
    """
    from lernos.ollama.embed import (
        generate_socratic_hint, is_ollama_running,
        evaluate_answer, evaluate_answer_local,
    )

    # Sokratischer Dialog nur bei Ollama + Note 2 oder 3
    if not is_ollama_running() or grade not in (2, 3):
        return given, grade

    best_grade  = grade
    best_answer = given
    current_ans = given

    for round_num in range(1, max_rounds + 1):
        # Sokratische Rückfrage generieren
        hint = generate_socratic_hint(expected, current_ans, best_grade, topic_name)
        if not hint:
            break

        print()
        print(f"  {ui.c('─' * 52, ui.BRIGHT_BLACK)}")
        print(f"  {ui.c('💡 Sokratische Rückfrage:', ui.BRIGHT_YELLOW, ui.BOLD)}"
              f"  {ui.c(f'(Runde {round_num}/{max_rounds})', ui.BRIGHT_BLACK)}")
        print()
        # Rückfrage zeilenweise umbrechen für bessere Lesbarkeit
        for line in _wrap_text(hint, width=60):
            print(f"     {ui.c(line, ui.BRIGHT_WHITE)}")
        print()
        print(f"  {ui.c('─' * 52, ui.BRIGHT_BLACK)}")
        print()

        # Antwort nachbessern oder überspringen
        improved = ui.prompt(
            "📝 Verbesserte Antwort (Enter zum Überspringen)", ""
        ).strip()

        if not improved:
            # Leer = Überspringen — Dialog endet sofort
            break

        # Neu bewerten — Note kann NUR steigen (max mit bisheriger Note)
        new_grade, _src = evaluate_answer(expected, improved)
        if new_grade is None:
            new_grade = evaluate_answer_local(expected, improved)
        new_grade   = max(0, min(5, new_grade))
        final_grade = max(best_grade, new_grade)   # Note kann nicht fallen

        if final_grade > best_grade:
            improvement = final_grade - best_grade
            best_grade  = final_grade
            best_answer = improved
            current_ans = improved
            print(f"  {ui.c(f'⬆  Verbessert! Note: {final_grade}  (+{improvement})', ui.BRIGHT_GREEN, ui.BOLD)}")
        else:
            # Note gleich oder schlechter → beste Note beibehalten
            best_answer = improved
            current_ans = improved
            if new_grade == best_grade:
                print(f"  {ui.c(f'→  Note bleibt: {best_grade}', ui.BRIGHT_BLUE)}")
            else:
                print(f"  {ui.c(f'→  Behalte bisherige Note: {best_grade}', ui.DIM)}")

        # Fertig wenn Maximum oder Note gut genug
        if best_grade >= 4 or round_num >= max_rounds:
            break

        cont = ui.prompt("Noch eine Runde? (J/n)", "j").lower()
        if cont in ("n", "nein", ""):
            break

    return best_answer, best_grade


def _wrap_text(text: str, width: int = 60) -> list[str]:
    """Einfacher Zeilenumbruch für Terminal-Ausgabe."""
    import textwrap
    lines = []
    for paragraph in text.split("\n"):
        if paragraph.strip():
            lines.extend(textwrap.wrap(paragraph.strip(), width=width) or [paragraph])
        else:
            lines.append("")
    return lines


def _render_topic_card(topic, prereqs, conn):
    """Rendert die Topic-Karte mit Rahmen, Metadaten, Voraussetzungen."""
    w = ui.term_width()
    state_colors = {
        "NEW": ui.BRIGHT_BLACK, "LEARNING": ui.BRIGHT_RED,
        "REVIEW": ui.BRIGHT_BLUE, "MASTERED": ui.BRIGHT_GREEN,
        "FROZEN": ui.BRIGHT_MAGENTA,
    }
    col = state_colors.get(topic.state, ui.WHITE)

    print()
    print(ui.c("  ┌" + "─" * (w - 4) + "┐", ui.BRIGHT_BLACK))

    emoji = STATE_EMOJI.get(topic.state, "  ")
    title = f"  {emoji} {topic.name}"
    pad   = " " * max(0, w - 6 - len(title))
    print(ui.c("  │ ", ui.BRIGHT_BLACK)
          + ui.c(title, ui.BOLD, ui.BRIGHT_WHITE)
          + pad + ui.c(" │", ui.BRIGHT_BLACK))

    meta  = f"  {topic.module or '—'}  |  EF:{topic.ef:.2f}  |  Intervall:{topic.interval_d}d  |  "
    meta += ui.c(topic.state, col)
    due   = f"  |  {ui.format_due(topic)}"
    print(ui.c("  │ ", ui.BRIGHT_BLACK) + meta + due
          + ui.c(" │", ui.BRIGHT_BLACK))

    if prereqs:
        pre_col = {
            "MASTERED": ui.BRIGHT_GREEN, "REVIEW": ui.BRIGHT_BLUE,
            "LEARNING": ui.BRIGHT_RED, "NEW": ui.BRIGHT_BLACK, "FROZEN": ui.BRIGHT_MAGENTA,
        }
        names = ", ".join(
            ui.c(p.name, pre_col.get(p.state, ui.WHITE))
            for p in prereqs
        )
        print(ui.c("  │ ", ui.BRIGHT_BLACK)
              + f"  Voraussetzungen: {names}"
              + ui.c(" │", ui.BRIGHT_BLACK))

    # Dokument-Hinweis
    docs    = get_documents_for_topic(conn, topic.id)
    q_count = len(get_questions_for_topic(conn, topic.id))
    if docs:
        hint = (f"  📎 {len(docs)} PDF(s) · {q_count} Fragen  "
                f"→  lernos review --questions  |  lernos doc review \"{topic.name}\"")
        print(ui.c("  │ ", ui.BRIGHT_BLACK)
              + ui.c(hint, ui.DIM)
              + ui.c(" │", ui.BRIGHT_BLACK))

    print(ui.c("  └" + "─" * (w - 4) + "┘", ui.BRIGHT_BLACK))


def _process_and_save(conn, topic, grade: int, confidence: int,
                      source: str = "manual") -> dict:
    """SM-2 berechnen, DB updaten, Kaskade auslösen, Feedback zeigen."""
    result = calculate(topic, grade, confidence)

    new_resets = getattr(topic, "learning_resets", 0) or 0
    if result.new_state == STATE_LEARNING and topic.state != STATE_LEARNING:
        new_resets += 1

    update_topic_sm2(
        conn, topic.id,
        result.new_state, result.new_ef,
        result.new_interval, result.new_reps,
        result.new_due_date,
        learning_resets=new_resets,
    )
    log_session(
        conn, topic.id,
        grade, confidence, result.correct,
        topic.state, result.new_state,
        topic.ef, result.new_ef,
        topic.interval_d, result.new_interval,
    )

    cascade_info = []
    if result.new_state == STATE_LEARNING and topic.state != STATE_LEARNING:
        cascade_info = cascade_review(conn, topic.id)

    _show_feedback(topic, result, grade, confidence, cascade_info, source)

    return {
        "name":         topic.name,
        "correct":      result.correct,
        "grade":        grade,
        "grade_used":   result.grade_used,
        "new_state":    result.new_state,
        "new_ef":       result.new_ef,
        "new_interval": result.new_interval,
        "source":       source,
    }


def _get_int_input(
    prompt_text: str,
    min_val: int,
    max_val: int,
    descriptions: dict,
    default: int | None = None,
) -> int:
    print()
    print(f"  {ui.c(prompt_text, ui.BOLD)}")
    for i in range(min_val, max_val + 1):
        star = ui.c(" ◄", ui.BRIGHT_MAGENTA) if default == i else ""
        print(f"  {ui.c(f'[{i}]', ui.BRIGHT_CYAN)} {descriptions.get(i, '')}{star}")

    def_str = str(default) if default is not None else str(min_val)
    while True:
        raw = ui.prompt(f"({min_val}-{max_val})", def_str)
        try:
            val = int(raw)
            if min_val <= val <= max_val:
                return val
        except ValueError:
            pass
        ui.error(f"Bitte eine Zahl zwischen {min_val} und {max_val} eingeben.")


def _show_feedback(topic, result, grade, confidence, cascade_info, source="manual"):
    """Vollständiges Feedback-Panel nach jedem Review."""
    print()
    state_colors = {
        "NEW": ui.BRIGHT_BLACK, "LEARNING": ui.BRIGHT_RED,
        "REVIEW": ui.BRIGHT_BLUE, "MASTERED": ui.BRIGHT_GREEN,
        "FROZEN": ui.BRIGHT_MAGENTA,
    }

    # Ergebnis-Zeile
    if result.correct:
        if result.grade_used >= 5:
            print(f"  {ui.c('🌟 Perfekt!', ui.BRIGHT_GREEN, ui.BOLD)}")
        elif result.grade_used >= 4:
            print(f"  {ui.c('✅ Gut!', ui.BRIGHT_GREEN)}")
        else:
            print(f"  {ui.c('✅ Richtig (knapp).', ui.GREEN)}")
    else:
        if confidence >= 4:
            print(f"  {ui.c('⚠️  Falsch — Overconfidence erkannt! Grade -2', ui.BRIGHT_RED, ui.BOLD)}")
        else:
            print(f"  {ui.c('❌ Falsch.', ui.BRIGHT_RED)}")

    # Grade-Anpassung durch Konfidenz
    if result.grade_used != grade:
        diff = result.grade_used - grade
        print(f"  {ui.c(f'Grade angepasst: {grade} → {result.grade_used} ({diff:+d} Confidence-Modifikator)', ui.BRIGHT_YELLOW)}")

    print()

    # EF-Visualisierung
    ef_diff  = result.new_ef - topic.ef
    ef_sign  = "+" if ef_diff >= 0 else ""
    ef_color = ui.BRIGHT_GREEN if ef_diff >= 0 else ui.BRIGHT_RED
    print(f"  {ui.c('EF:       ', ui.DIM)} "
          f"{ui.progress_bar(topic.ef, 2.5, 18)} {topic.ef:.2f}"
          f" → {ui.progress_bar(result.new_ef, 2.5, 18)} "
          f"{ui.c(f'{result.new_ef:.2f} ({ef_sign}{ef_diff:.3f})', ef_color)}")

    # Intervall
    print(f"  {ui.c('Intervall:', ui.DIM)} "
          f"{ui.c(f'{topic.interval_d}d', ui.DIM)} → "
          f"{ui.c(f'{result.new_interval}d', ui.BOLD)}")

    # Zustandswechsel
    old_col = state_colors.get(topic.state, ui.WHITE)
    new_col = state_colors.get(result.new_state, ui.WHITE)
    if topic.state != result.new_state:
        print(f"  {ui.c('Zustand:  ', ui.DIM)} "
              f"{ui.c(topic.state, old_col)} → "
              f"{ui.c(result.new_state, new_col, ui.BOLD)}")
    else:
        print(f"  {ui.c('Zustand:  ', ui.DIM)} {ui.c(result.new_state, new_col)}")

    # Nächste Fälligkeit
    print(f"  {ui.c('Nächstes: ', ui.DIM)} {ui.c(result.new_due_date, ui.BRIGHT_CYAN)}")

    # Quelle
    if source == "questions":
        print(f"  {ui.c('Quelle:   ', ui.DIM)} {ui.c('Fragen-Review (PDF)', ui.BRIGHT_YELLOW)}")

    # Kaskadierende Wiederholungen
    if cascade_info:
        print()
        print(f"  {ui.c('⚡ Kaskade ausgelöst (1 Ebene):', ui.BRIGHT_YELLOW, ui.BOLD)}")
        for item in cascade_info:
            old_c = state_colors.get(item["old"], ui.WHITE)
            new_c = state_colors.get(item["new"], ui.WHITE)
            print(f"     → {ui.c(item['name'], ui.BOLD)}: "
                  f"{ui.c(item['old'], old_c)} → {ui.c(item['new'], new_c)}  "
                  f"  {ui.c("(Gewicht: " + str(round(item["weight"], 2)) + ")", ui.DIM)}")
        print(f"  {ui.c('  Tiefere Ebenen erst bei erneutem Fehlschlag.', ui.DIM)}")

    print()


def _session_summary(results: list[dict]):
    """Zusammenfassung am Ende einer Multi-Topic-Session."""
    correct  = sum(1 for r in results if r["correct"])
    total    = len(results)
    acc      = round(correct / total * 100)
    bar      = ui.progress_bar(acc, max_val=100, width=25)

    state_changes = [r for r in results if r.get("new_state") != r.get("old_state")]
    mastered_new  = sum(1 for r in results if r.get("new_state") == "MASTERED")

    ui.section("Session-Zusammenfassung")
    print(f"  Topics:     {total}")
    print(f"  Korrekt:    {correct}/{total}  →  {bar}  {acc}%")
    if mastered_new:
        print(f"  {ui.c(f'🏆 {mastered_new} Topic(s) neu gemeistert!', ui.BRIGHT_GREEN, ui.BOLD)}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Web-Review
# ─────────────────────────────────────────────────────────────────────────────

def _run_web_review(conn, topic_name, module, review_all, active, questions,
                    limit, port, web_output, fix_order: bool = False):
    """
    Startet den Browser-Review-Server und blockiert bis Ctrl+C.
    Wählt Topics nach den gleichen Regeln wie der Terminal-Review.
    """
    import signal

    if topic_name:
        topic = resolve_topic(conn, topic_name)
        if not topic:
            ui.error(f"Topic '{topic_name}' nicht gefunden.")
            sys.exit(1)
        topics = [topic]
    else:
        topics = get_due_topics(conn)
        if module:
            topics = [t for t in topics if t.module.lower() == module.lower()]
        if not review_all and not limit:
            topics = topics[:1] if topics else []

    if limit and 0 < limit < len(topics):
        topics = topics[:limit]

    if not topics:
        ui.success("Heute nichts fällig! 🎉")
        return

    if fix_order and len(topics) > 1:
        topics = _topo_sort_due(conn, topics)

    mode = "questions" if questions else ("active" if active else "standard")
    out  = web_output or None

    from lernos.graph.export_review import start_review_server
    from lernos.db.schema import get_db_path
    actual_port, server = start_review_server(
        conn, topics, mode=mode, port=port,
        open_browser=True, output_path=out,
        db_path=get_db_path(),
    )

    total = len(topics)
    mode_str = {"standard":"Standard","active":"Active-Recall","questions":"Fragen"}[mode]
    ui.header(
        "🌐 Web-Review gestartet",
        f"{total} Topic{'s' if total != 1 else ''}  |  {mode_str}"
    )
    ui.success(f"http://127.0.0.1:{actual_port}/")
    if out:
        ui.info(f"HTML gespeichert: {out}")
    ui.info("Strg+C zum Beenden")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        ui.info("Server beendet.")
    finally:
        server.server_close()
