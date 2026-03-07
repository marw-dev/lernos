"""
LernOS — Weitere CLI-Befehle:
  list, stats (mit Heatmap), freeze, unfreeze, delete, graph, export,
  edge (add/list/delete/cleanup), notify (Telegram vollständig), config,
  install-scheduler, import-csv
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date, timedelta

import click

from lernos import ui
from lernos.completion_helpers import complete_topic_names, complete_due_topic_names, complete_module_names


# ── list ──────────────────────────────────────────────────────────────────────

@click.command("list")
@click.option("--state", "-s", default="", help="Filter: NEW/LEARNING/REVIEW/MASTERED/FROZEN")
@click.option("--module", "-m", default="", help="Filter nach Modul")
@click.option("--due", is_flag=True, help="Nur heute fällige Topics")
@click.option("--page", "-p", default=1, help="Seitennummer (Standard: 1)")
@click.option("--page-size", default=0, help="Einträge pro Seite (0 = alle, Standard: auto)")
@click.option("--format", "-f", "fmt",
              type=click.Choice(["table", "json", "csv", "names"]),
              default="table", help="Ausgabeformat (table/json/csv/names)")
def cmd_list(state: str, module: str, due: bool, page: int, page_size: int, fmt: str):
    """
    Alle Topics gruppiert nach Modulen anzeigen.

    \b
    Bei > 30 Topics wird automatisch paginiert (--page / --page-size).
    Für Skripte/Pipes nutze --format json oder --format names.
    """
    from lernos.db.schema import startup
    from lernos.db.topics import get_all_topics, get_due_topics
    import shutil

    conn = startup()
    if due:
        topics = get_due_topics(conn)
    else:
        topics = get_all_topics(conn, state=state.upper() or None, module=module or None)

    if not topics:
        ui.info("Keine Topics gefunden.")
        return

    total_topics = len(topics)
    total_due    = sum(1 for t in topics if t.is_due)

    # Auto-Paginiergröße: Terminal-Höhe - 10 Zeilen Overhead
    term_height = shutil.get_terminal_size((80, 24)).lines
    auto_size   = max(10, term_height - 10)
    ps          = page_size if page_size > 0 else (auto_size if total_topics > auto_size else 0)

    # Pagination
    if ps and ps < total_topics:
        total_pages = (total_topics + ps - 1) // ps
        page        = max(1, min(page, total_pages))
        start       = (page - 1) * ps
        end         = start + ps
        topics      = topics[start:end]
        page_info   = f"  Seite {page}/{total_pages}  (--page N)"
    else:
        page_info   = ""
        total_pages = 1

    # ── Nicht-tabellarische Formate (kein Paginieren nötig) ────────────────
    if fmt == "json":
        import json as _json
        from datetime import date as _date
        out = []
        for t in (get_due_topics(conn) if due else get_all_topics(
                conn, state=state.upper() or None, module=module or None)):
            out.append({
                "id":          t.id, "name":       t.name,
                "module":      t.module, "state":     t.state,
                "ef":          round(t.ef, 4), "interval_d": t.interval_d,
                "repetitions": t.repetitions, "due_date":   t.due_date,
                "is_due":      t.is_due,
                "days_until_due": t.days_until_due,
                "description": t.description,
            })
        ui.raw(_json.dumps(out, ensure_ascii=False, indent=2))
        return

    if fmt == "csv":
        import csv as _csv, io as _io
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["id","name","module","state","ef","interval_d",
                    "repetitions","due_date","is_due"])
        all_t = get_due_topics(conn) if due else get_all_topics(
            conn, state=state.upper() or None, module=module or None)
        for t in all_t:
            w.writerow([t.id, t.name, t.module, t.state,
                        round(t.ef,4), t.interval_d, t.repetitions,
                        t.due_date, int(t.is_due)])
        ui.raw(buf.getvalue().rstrip())
        return

    if fmt == "names":
        all_t = get_due_topics(conn) if due else get_all_topics(
            conn, state=state.upper() or None, module=module or None)
        for t in all_t:
            ui.raw(t.name)
        return

    # ── Tabellen-Format (Standard) ────────────────────────────────────────
    modules: dict[str, list] = {}
    for t in topics:
        modules.setdefault(t.module or "—", []).append(t)

    ui.header(
        f"📋 Topics ({total_topics} gesamt · {total_due} fällig)",
        ("Nur fällige" if due else "Alle")
        + (f"  |  Zustand: {state}" if state else "")
        + (f"  |  Modul: {module}" if module else "")
        + page_info,
    )

    for mod, mod_topics in sorted(modules.items()):
        ui.section(mod)
        for t in mod_topics:
            print(ui.topic_state_line(t))

    if total_pages > 1:
        print()
        print(f"  {ui.c(f'Seite {page}/{total_pages} · Weiter: lernos list --page {min(page+1, total_pages)}', ui.DIM)}")
    print()


# ── stats ─────────────────────────────────────────────────────────────────────

@click.command("stats")
@click.option("--week",  "period", flag_value="week",  default=True, help="Letzte 7 Tage")
@click.option("--month", "period", flag_value="month",               help="Letzte 30 Tage")
@click.option("--all",   "period", flag_value="all",                 help="Gesamter Zeitraum")
def cmd_stats(period: str):
    """
    Detaillierte Lernstatistiken mit:
      - Korrektheit, Sessions, Ø Konfidenz
      - 7-Tage Aktivitäts-Heatmap (Kalenderansicht)
      - 14-Tage Prognose fälliger Reviews (Balkengraph)
      - Zustandsverteilung aller Topics
      - Problematische Topics (niedrigster EF)
    """
    from lernos.db.schema import startup
    from lernos.db.stats import get_week_stats

    conn  = startup()
    days  = {"week": 7, "month": 30, "all": 365}.get(period, 7)
    s     = get_week_stats(conn, days=days)
    label = {"week": "7 Tage", "month": "30 Tage", "all": "Gesamt"}.get(period)

    ui.header("📊 Lernstatistiken", f"Zeitraum: {label}")

    # ── Übersicht ──────────────────────────────────────────────────────────────
    ui.section("Übersicht")
    acc_bar = ui.progress_bar(s["accuracy"], max_val=100, width=20)
    print(f"  Korrektheit:  {acc_bar} {s['accuracy']}%")
    print(f"  Sessions:     {s['total_sessions']}")
    print(f"  Ø Konfidenz:  {s['avg_confidence']:.1f}/5")
    print(f"  Unique Topics: {s['unique_topics']}")
    if s["best_day"] != "-":
        print(f"  Bester Tag:   {s['best_day']} ({s['best_day_count']} Sessions)")
    if s["best_hour"] != "-":
        print(f"  Beste Uhrzeit: {s['best_hour']}")

    # ── Streak ────────────────────────────────────────────────────────────────
    from lernos.db.stats import get_streak
    streak = get_streak(conn)
    ui.section("🔥 Lern-Streak")
    _render_streak(streak)

    # ── 7-Tage Aktivitäts-Heatmap ─────────────────────────────────────────────
    ui.section("Aktivitäts-Heatmap (letzte 14 Tage)")
    _render_heatmap(conn)

    # ── 14-Tage Prognose ──────────────────────────────────────────────────────
    ui.section("Prognose: Fällige Reviews (nächste 14 Tage)")
    forecast = conn.execute(
        """SELECT due_date, count(*) as cnt FROM topics
           WHERE state NOT IN ('FROZEN','MASTERED')
             AND due_date > date('now')
             AND due_date <= date('now', '+14 days')
           GROUP BY due_date ORDER BY due_date"""
    ).fetchall()

    if not forecast:
        ui.info("Keine anstehenden Reviews in den nächsten 14 Tagen.")
    else:
        max_cnt = max(r["cnt"] for r in forecast)
        for r in forecast:
            d_str = r["due_date"][5:]   # MM-DD
            filled = max(1, int((r["cnt"] / max_cnt) * 28))
            bar    = "█" * filled
            color  = ui.BRIGHT_RED if r["cnt"] >= 5 else (
                     ui.BRIGHT_YELLOW if r["cnt"] >= 3 else ui.BRIGHT_CYAN)
            print(f"  {ui.c(d_str, ui.DIM)} │{ui.c(bar, color)} {r['cnt']}")

    # ── Zustandsverteilung ────────────────────────────────────────────────────
    ui.section("Zustandsverteilung")
    total_topics = sum(s["state_dist"].values())
    state_order  = ["NEW", "LEARNING", "REVIEW", "MASTERED", "FROZEN"]
    state_labels = {"NEW": "Neu", "LEARNING": "Lernen", "REVIEW": "Review",
                    "MASTERED": "Mastered", "FROZEN": "Frozen"}
    state_colors = {"NEW": ui.BRIGHT_BLACK, "LEARNING": ui.BRIGHT_RED,
                    "REVIEW": ui.BRIGHT_BLUE, "MASTERED": ui.BRIGHT_GREEN,
                    "FROZEN": ui.BRIGHT_MAGENTA}
    for st in state_order:
        cnt = s["state_dist"].get(st, 0)
        if cnt == 0:
            continue
        pct  = cnt / total_topics * 100
        bar  = ui.progress_bar(pct, max_val=100, width=20)
        col  = state_colors.get(st, ui.WHITE)
        lbl  = state_labels.get(st, st)
        print(f"  {ui.c(lbl.ljust(10), col)} {bar} {cnt:3d} ({pct:.0f}%)")

    # ── Problematische Topics ─────────────────────────────────────────────────
    if s["hard_topics"]:
        ui.section("⚠️  Schwierigste Topics (niedrigster EF)")
        for ht in s["hard_topics"]:
            fail_rate = round(ht["failures"] / ht["total"] * 100) if ht["total"] else 0
            ef_bar    = ui.progress_bar(ht["ef"], max_val=2.5, width=12)
            print(f"  {ui.c(ht['name'][:30], ui.BOLD):32}  EF:{ef_bar} {ht['ef']:.2f}  "
                  f"Fehler: {ui.c(f'{fail_rate}%', ui.BRIGHT_RED)}")
    print()


def _render_streak(streak: dict) -> None:
    """
    Rendert den Streak-Block:
      - Aktuelle Streak (Flammen-Balken)
      - Längste Streak ever
      - 7-Tage Mini-Kalender (● = gelernt, ○ = nicht gelernt)
    """
    current  = streak["current"]
    longest  = streak["longest"]
    active   = streak["active_today"]
    last_7   = streak["last_7"]    # [(date_str, count), ...]

    # Streak-Visualisierung
    if current == 0:
        flame = ui.c("○  Noch kein Streak", ui.BRIGHT_BLACK)
        hint  = "  Lerne heute, um einen Streak zu starten!"
    elif not active:
        # Streak läuft aber heute noch nicht gelernt
        flame_bar = ui.c("🔥" * min(current, 10), ui.BRIGHT_YELLOW)
        flame     = f"{flame_bar}  {ui.c(f'{current} Tag(e) — heute noch lernen!', ui.BRIGHT_YELLOW, ui.BOLD)}"
        hint      = ""
    else:
        color     = ui.BRIGHT_GREEN if current >= 7 else (
                    ui.BRIGHT_CYAN  if current >= 3 else ui.BRIGHT_WHITE)
        flame_bar = ui.c("🔥" * min(current, 10), color)
        flame     = f"{flame_bar}  {ui.c(f'{current} Tag(e) in Folge', color, ui.BOLD)}"
        hint      = ""

    print(f"  Aktuell:  {flame}")
    if hint:
        print(f"  {ui.c(hint, ui.BRIGHT_YELLOW)}")
    print(f"  Rekord:   {ui.c(str(longest), ui.BRIGHT_MAGENTA)} Tag(e)")

    # 7-Tage Mini-Kalender
    print()
    day_abbr  = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"]
    from datetime import date as ddate, datetime as dtime
    cal_top   = "  "
    cal_dots  = "  "
    cal_btm   = "  "
    for d_str, cnt in last_7:
        d       = dtime.strptime(d_str, "%Y-%m-%d").date()
        wd      = day_abbr[d.weekday()]
        day_num = d_str[8:]   # DD
        is_today = d_str == ddate.today().isoformat()

        if is_today and not cnt:
            dot   = ui.c("◈ ", ui.BRIGHT_YELLOW)   # Heute, noch kein Eintrag
        elif cnt == 0:
            dot   = ui.c("○ ", ui.BRIGHT_BLACK)
        elif cnt <= 2:
            dot   = ui.c("◉ ", ui.BRIGHT_CYAN)
        else:
            dot   = ui.c("● ", ui.BRIGHT_GREEN)

        cal_top  += ui.c(f"{day_num} ", ui.DIM)
        cal_dots += dot
        cal_btm  += ui.c(f"{wd} ", ui.DIM)

    print(cal_top)
    print(cal_dots)
    print(cal_btm)
    legend = (
        f"\n  {ui.c('○', ui.BRIGHT_BLACK)} Kein  "
        f"{ui.c('◉', ui.BRIGHT_CYAN)} 1-2 Sessions  "
        f"{ui.c('●', ui.BRIGHT_GREEN)} 3+ Sessions  "
        f"{ui.c('◈', ui.BRIGHT_YELLOW)} Heute (offen)"
    )
    print(legend)
    print()


def _render_heatmap(conn):
    """
    Rendert eine 2-Wochen-Kalender-Heatmap der Lernaktivität im Terminal.
    Zeigt Sessions pro Tag als farbige Blöcke.
    """
    from datetime import date as ddate
    today = ddate.today()

    # Sessions der letzten 14 Tage holen
    rows = conn.execute(
        """SELECT date(reviewed_at) as d, COUNT(*) as cnt
           FROM sessions
           WHERE reviewed_at >= date('now', '-14 days')
           GROUP BY d""",
    ).fetchall()
    counts = {r["d"]: r["cnt"] for r in rows}

    # Höchste Anzahl für Normierung
    max_cnt = max(counts.values(), default=1)

    # 14 Tage × 2 Zeilen (Woche oben/unten)
    days14 = [(today - timedelta(days=13 - i)) for i in range(14)]

    # Wochentag-Labels
    day_abbr = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"]
    print()

    # Obere Zeile: Datum MM-DD
    date_row = "  "
    for d in days14:
        date_row += f" {d.strftime('%d')}"
    print(ui.c(date_row, ui.DIM))

    # Heatmap-Zeile
    block_row = "  "
    for d in days14:
        ds   = d.isoformat()
        cnt  = counts.get(ds, 0)
        rel  = cnt / max_cnt if max_cnt > 0 else 0
        if cnt == 0:
            block = ui.c("░░", ui.BRIGHT_BLACK)
        elif rel < 0.33:
            block = ui.c("▒▒", ui.BRIGHT_YELLOW)
        elif rel < 0.67:
            block = ui.c("▓▓", ui.BRIGHT_CYAN)
        else:
            block = ui.c("██", ui.BRIGHT_GREEN)
        # Heute markieren
        if d == today:
            block = ui.c("◆◆", ui.BRIGHT_WHITE)
        block_row += f" {block}"
    print(block_row)

    # Wochentag-Labels unten
    wd_row = "  "
    for d in days14:
        wd_row += f" {ui.c(day_abbr[d.weekday()], ui.DIM)}"
    print(wd_row)

    # Legende
    print(f"\n  {ui.c('░░', ui.BRIGHT_BLACK)} Kein   "
          f"{ui.c('▒▒', ui.BRIGHT_YELLOW)} Wenig   "
          f"{ui.c('▓▓', ui.BRIGHT_CYAN)} Mittel   "
          f"{ui.c('██', ui.BRIGHT_GREEN)} Viel   "
          f"{ui.c('◆◆', ui.BRIGHT_WHITE)} Heute")
    print()



# ── diagnose ──────────────────────────────────────────────────────────────────

@click.command("diagnose")
@click.argument("topic_name", shell_complete=complete_topic_names)
def cmd_diagnose(topic_name: str):
    """
    Tiefenanalyse eines einzelnen Topics.

    Zeigt:
      - EF-Verlauf über alle Reviews (Sparkline)
      - Konfidenz- vs. Korrektheit-Muster (Overconfidence-Erkennung)
      - Worst-Fragen aus PDFs (am häufigsten falsch beantwortet)
      - Empfehlung: was als nächstes tun?
    """
    from lernos.db.schema import startup
    from lernos.db.stats  import get_session_history
    from lernos.fuzzy.resolve import resolve_topic
    from lernos.db.topics import get_questions_for_topic

    conn  = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        import sys; sys.exit(1)

    history = get_session_history(conn, topic.id, limit=50)

    state_colors = {
        "NEW": ui.BRIGHT_BLACK, "LEARNING": ui.BRIGHT_RED,
        "REVIEW": ui.BRIGHT_BLUE, "MASTERED": ui.BRIGHT_GREEN,
        "FROZEN": ui.BRIGHT_MAGENTA,
    }
    col = state_colors.get(topic.state, ui.WHITE)

    ui.header(
        f"🔬 Diagnose: {topic.name}",
        f"{topic.module or '—'}  |  {ui.c(topic.state, col)}  |  "
        f"EF:{topic.ef:.2f}  |  Intervall:{topic.interval_d}d"
    )

    if not history:
        ui.warn("Noch keine Review-Sessions für dieses Topic.")
        ui.info("Starte ein Review mit: lernos review \"" + topic.name + "\"")
        return

    # ── 1. Übersicht ──────────────────────────────────────────────────────────
    ui.section("Übersicht")
    total    = len(history)
    correct  = sum(1 for s in history if s["correct"])
    acc      = round(correct / total * 100) if total else 0
    resets   = getattr(topic, "learning_resets", 0) or 0
    avg_conf = sum(s["confidence"] for s in history) / total if total else 0
    avg_grade= sum(s["grade"]      for s in history) / total if total else 0

    acc_bar  = ui.progress_bar(acc, max_val=100, width=20)
    print(f"  Reviews gesamt:   {total}")
    print(f"  Korrektheit:      {acc_bar} {acc}%  ({correct}/{total})")
    print(f"  Ø Konfidenz:      {avg_conf:.1f}/5")
    print(f"  Ø Grade:          {avg_grade:.1f}/5")
    print(f"  Learning-Resets:  {ui.c(str(resets), ui.BRIGHT_RED if resets >= 3 else ui.WHITE)}")
    print(f"  Nächste Fälligkeit: {ui.format_due(topic)}")

    # ── 2. EF-Verlauf (Sparkline) ─────────────────────────────────────────────
    ui.section("EF-Verlauf (neueste → älteste)")
    ef_vals = [s["new_ef"] for s in history]   # history ist DESC (neueste zuerst)
    _render_sparkline(ef_vals[:20], min_val=1.0, max_val=2.8, label="EF")

    # ── 3. Konfidenz vs. Korrektheit ─────────────────────────────────────────
    ui.section("Konfidenz vs. Korrektheit")
    _render_confidence_matrix(history)

    # ── 4. EF-Trend ───────────────────────────────────────────────────────────
    if len(ef_vals) >= 3:
        recent_3  = sum(ef_vals[:3])  / 3
        older_3   = sum(ef_vals[3:6]) / 3 if len(ef_vals) >= 6 else ef_vals[-1]
        trend     = recent_3 - older_3
        trend_sym = "⬆" if trend > 0.05 else ("⬇" if trend < -0.05 else "→")
        trend_col = ui.BRIGHT_GREEN if trend > 0.05 else (
                    ui.BRIGHT_RED   if trend < -0.05 else ui.DIM)
        print(f"  EF-Trend (letzte 3 vs. davor): "
              f"{ui.c(f'{trend_sym} {trend:+.2f}', trend_col)}")
        print()

    # ── 5. Schwächste Fragen (aus PDFs) ──────────────────────────────────────
    questions = get_questions_for_topic(conn, topic.id)
    used_qs   = [q for q in questions if q.used_count > 0]
    if used_qs:
        # Sortierung: am meisten verwendet (Proxy für "oft falsch, daher wiederholt")
        hard_qs = sorted(used_qs, key=lambda q: q.used_count, reverse=True)[:3]
        ui.section("📋 Meistgenutzte Fragen (PDF)")
        for q in hard_qs:
            times = ui.c(f"({q.used_count}× genutzt)", ui.BRIGHT_YELLOW)
            diff  = ui.c("★" * q.difficulty + "☆" * (5 - q.difficulty), ui.DIM)
            print(f"  {diff}  {q.question[:70]}{'…' if len(q.question)>70 else ''}  {times}")
        print()

    # ── 6. Empfehlung ─────────────────────────────────────────────────────────
    ui.section("💡 Empfehlung")
    _render_recommendation(topic, history, resets, acc)


def _render_sparkline(values: list[float],
                      min_val: float, max_val: float,
                      label: str = "") -> None:
    """
    Rendert eine horizontale Sparkline aus Float-Werten.
    Neueste Werte links (wie history DESC).
    Blöcke: ▁▂▃▄▅▆▇█
    """
    blocks = "▁▂▃▄▅▆▇█"
    if not values:
        print(f"  {ui.c('(keine Daten)', ui.DIM)}")
        return

    span = max_val - min_val
    line = ""
    for v in values:
        idx   = int(((v - min_val) / span) * (len(blocks) - 1))
        idx   = max(0, min(len(blocks) - 1, idx))
        color = (ui.BRIGHT_GREEN if v >= 2.2 else
                 ui.BRIGHT_YELLOW if v >= 1.7 else ui.BRIGHT_RED)
        line += ui.c(blocks[idx], color)

    # Erste und letzte EF-Werte anzeigen
    newest = values[0]
    oldest = values[-1]
    print(f"  {label:4}  {line}  {ui.c(f'→ aktuell {newest:.2f}', ui.DIM)}")
    print(f"         {ui.c(f'alt:{oldest:.2f}', ui.DIM)} → {ui.c(f'neu:{newest:.2f}', ui.DIM)}"
          f"  (Bereich: {min_val:.1f}–{max_val:.1f})")
    print()


def _render_confidence_matrix(history: list[dict]) -> None:
    """
    Zeigt eine 5×2-Matrix: Konfidenz (1-5) × Korrektheit (✓/✗).
    Macht Overconfidence-Muster sichtbar.
    """
    from collections import defaultdict
    matrix = defaultdict(lambda: {"correct": 0, "wrong": 0})
    for s in history:
        key = s["confidence"]
        if s["correct"]:
            matrix[key]["correct"] += 1
        else:
            matrix[key]["wrong"] += 1

    print(f"  {'Konfidenz':12} {'✓ Richtig':12} {'✗ Falsch':12} {'Trefferquote':12}")
    print(f"  {ui.c('─' * 52, ui.BRIGHT_BLACK)}")

    overconf_warn = False
    for conf in range(1, 6):
        c = matrix[conf]["correct"]
        w = matrix[conf]["wrong"]
        if c + w == 0:
            continue
        rate = round(c / (c + w) * 100)
        bar  = ui.progress_bar(rate, max_val=100, width=12)
        # Overconfidence: hohe Konfidenz + schlechte Trefferquote
        warn = ""
        if conf >= 4 and rate < 50 and (c + w) >= 2:
            warn = ui.c("  ⚠ Overconfidence!", ui.BRIGHT_RED)
            overconf_warn = True
        conf_col = ui.BRIGHT_YELLOW if conf >= 4 else ui.DIM
        print(f"  {ui.c(str(conf) + '/5', conf_col):14} "
              f"{ui.c(str(c), ui.BRIGHT_GREEN):14} "
              f"{ui.c(str(w), ui.BRIGHT_RED if w > 0 else ui.DIM):14} "
              f"{bar} {rate}%{warn}")

    if overconf_warn:
        print(f"\n  {ui.c('Tipp: Senke deine Konfidenz-Einschätzung auf 1-3 bis die Trefferquote steigt.', ui.BRIGHT_YELLOW)}")
    print()


def _render_recommendation(topic, history: list[dict],
                             resets: int, accuracy: int) -> None:
    """Gibt eine klare, priorisierte Handlungsempfehlung aus."""
    from datetime import date as ddate

    recs = []

    # Ease-Hell-Warnung
    if topic.ef < 1.5:
        recs.append((ui.BRIGHT_RED, "KRITISCH",
                     f"EF={topic.ef:.2f} — Das Topic steckt in der Ease-Hell. "
                     "Übe es täglich bis EF > 2.0."))

    # Viele Resets
    if resets >= 3:
        recs.append((ui.BRIGHT_RED, "ACHTUNG",
                     f"{resets} Learning-Resets — Dieses Topic ist hartnäckig. "
                     "Prüfe ob die Beschreibung klar genug ist: lernos edit"))

    # Schlechte Korrektheit
    if accuracy < 40 and len(history) >= 3:
        recs.append((ui.BRIGHT_YELLOW, "HINWEIS",
                     f"Nur {accuracy}% Korrektheit. Erwäge die Beschreibung zu überarbeiten "
                     "oder in kleinere Topics aufzuteilen."))

    # Zu lange nicht gelernt
    if history and "reviewed_at" in history[0]:
        from datetime import datetime
        try:
            last     = datetime.fromisoformat(history[0]["reviewed_at"].split(".")[0])
            days_ago = (datetime.now() - last).days
            if days_ago > 30 and topic.state != "FROZEN":
                recs.append((ui.BRIGHT_YELLOW, "HINWEIS",
                             f"Zuletzt vor {days_ago} Tagen gelernt. Topic ist möglicherweise veraltet."))
        except (ValueError, TypeError):
            pass

    # Topic gut drauf
    if not recs and topic.ef >= 2.2 and accuracy >= 80:
        recs.append((ui.BRIGHT_GREEN, "GUT",
                     f"EF={topic.ef:.2f}, {accuracy}% Korrektheit — "
                     "Dieses Topic läuft sehr gut. Weiter so!"))

    if not recs:
        recs.append((ui.DIM, "OK",
                     "Keine besonderen Auffälligkeiten. Weiter mit dem normalen Review-Zyklus."))

    for color, level, msg in recs:
        print(f"  {ui.c(f'[{level}]', color, ui.BOLD)} {msg}")
    print()


# ── delete ─────────────────────────────────────────────────────────────────────

@click.command("delete")
@click.argument("topic_name", shell_complete=complete_topic_names)
@click.option("--force", "-f", is_flag=True, help="Ohne Bestätigung sofort löschen")
def cmd_delete(topic_name: str, force: bool):
    """Topic samt Kanten, PDFs und Fragen unwiderruflich löschen."""
    from lernos.db.schema import startup
    from lernos.db.topics import delete_topic
    from lernos.fuzzy.resolve import resolve_topic

    conn = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)

    if not force:
        ui.warn(f"Topic '{topic.name}' (Modul: {topic.module or '—'}) löschen?")
        ui.info("Achtung: Alle Kanten, Dokumente, Fragen und Sessions werden mitgelöscht!")
        if not ui.confirm("Endgültig löschen?", default=False):
            ui.info("Abgebrochen.")
            return

    # Dateien sicher löschen (robust gegen bereits gelöschte oder geteilte Pfade)
    from lernos.db.topics import get_documents_for_topic
    import os as _os
    docs = get_documents_for_topic(conn, topic.id)
    deleted_paths = set()
    for doc in docs:
        if doc.filepath and doc.filepath not in deleted_paths:
            try:
                if _os.path.exists(doc.filepath):
                    _os.remove(doc.filepath)
                    deleted_paths.add(doc.filepath)
            except OSError as file_err:
                ui.warn(f"Datei konnte nicht gelöscht werden: {doc.filepath} ({file_err})")

    delete_topic(conn, topic.id)
    ui.success(f"Topic '{topic.name}' vollständig gelöscht.")


# ── freeze / unfreeze ──────────────────────────────────────────────────────────

@click.command("freeze")
@click.argument("topic_name", shell_complete=complete_topic_names)
@click.option("--days", default=6, help="Tage zum Pausieren (Standard: 6)")
def cmd_freeze(topic_name: str, days: int):
    """Topic für N Tage aus dem Review-Zyklus aussetzen (FROZEN)."""
    from lernos.db.schema import startup
    from lernos.db.topics import freeze_topic
    from lernos.fuzzy.resolve import resolve_topic

    conn  = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)

    if topic.state not in ("MASTERED", "REVIEW"):
        ui.warn(f"'{topic.name}' ist {topic.state}. Freeze empfohlen nur für MASTERED/REVIEW.")
        if not ui.confirm("Trotzdem einfrieren?", False):
            return

    until = (date.today() + timedelta(days=days)).isoformat()
    freeze_topic(conn, topic.id, days=days)
    ui.success(f"'{topic.name}' eingefroren bis {until} ({days} Tage)")


@click.command("unfreeze")
@click.argument("topic_name", shell_complete=complete_topic_names)
def cmd_unfreeze(topic_name: str):
    """Eingeforenes Topic manuell reaktivieren (FROZEN → REVIEW, heute fällig)."""
    from lernos.db.schema import startup
    from lernos.db.topics import unfreeze_topic
    from lernos.fuzzy.resolve import resolve_topic

    conn  = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)
    if topic.state != "FROZEN":
        ui.warn(f"'{topic.name}' ist nicht FROZEN (Zustand: {topic.state}).")
        return
    unfreeze_topic(conn, topic.id)
    ui.success(f"'{topic.name}' reaktiviert → REVIEW (heute fällig)")


# ── graph ──────────────────────────────────────────────────────────────────────

@click.command("graph")
@click.option("--module", "-m", default="", help="Nur Topics dieses Moduls")
@click.option("--open/--no-open", "open_browser", default=True)
@click.option("--output", "-o", default="", help="Ausgabepfad für HTML-Datei")
def cmd_graph(module: str, open_browser: bool, output: str):
    """
    Interaktiven D3.js Wissensgraph als HTML exportieren und öffnen.

    Features:
      - Force-Directed Layout mit Modul-Clustering (Inselbildung)
      - Farbkodierung nach Lernzustand (NEW/LEARNING/REVIEW/MASTERED/FROZEN)
      - Modul-Filter (Checkboxen links)
      - Tooltips mit EF, Intervall, Fälligkeit, Wiederholungen
      - Cross-Module-Kanten gestrichelt dargestellt
      - Zoom, Drag, Layout-Neuberechnung
    """
    import webbrowser
    from lernos.db.schema import startup
    from lernos.graph.export_html import export_graph_html

    conn = startup()
    out  = output or os.path.join(os.path.expanduser("~"), "lernos_graph.html")
    n    = export_graph_html(conn, out)
    ui.success(f"Graph mit {n} Topics exportiert: {out}")

    if open_browser:
        try:
            webbrowser.open(f"file://{os.path.abspath(out)}")
            ui.info("Browser geöffnet.")
        except Exception:
            ui.warn("Browser konnte nicht automatisch geöffnet werden.")
            ui.info(f"Manuell öffnen: file://{os.path.abspath(out)}")


# ── export ─────────────────────────────────────────────────────────────────────

@click.command("export")
@click.option("--module", "-m", default="", help="Nur Topics dieses Moduls")
@click.option("--days", default=14, help="Tage bis zur Prüfung")
def cmd_export(module: str, days: int):
    """Priorisierten Lernplan für Prüfungsphase (topologisch sortiert)."""
    from lernos.db.schema import startup
    from lernos.graph.topo import build_exam_plan

    conn = startup()
    plan = build_exam_plan(conn, module=module or None, days=days)
    if not plan:
        ui.info("Keine Topics gefunden.")
        return

    title = "PRÜFUNGSPLAN"
    if module:
        title += f": {module}"
    ui.header(f"📅 {title}", f"{days} Tage bis zur Prüfung")

    PRIO_COLOR = {
        "SEHR HOCH": ui.BRIGHT_RED,   "HOCH":    ui.BRIGHT_YELLOW,
        "MITTEL":    ui.BRIGHT_BLUE,  "NIEDRIG": ui.BRIGHT_GREEN,
        "PAUSIERT":  ui.BRIGHT_MAGENTA,
    }
    STATE_EMOJI_MAP = {
        "NEW": "🆕", "LEARNING": "⚠️ ", "REVIEW": "🔄",
        "MASTERED": "✅", "FROZEN": "❄️ ",
    }

    for item in plan:
        t     = item["topic"]
        label = item["label"]
        col   = PRIO_COLOR.get(label, ui.WHITE)
        emoji = STATE_EMOJI_MAP.get(t.state, "  ")
        ef_str = f"{t.ef:.2f}" if t.state != "NEW" else " —  "
        print(f"  {ui.c(str(item['pos']).rjust(3), ui.BRIGHT_BLACK)}. "
              f"{emoji} {ui.c(t.name, ui.BOLD):<35} "
              f"{ui.c(f'[{t.state}]', ui.DIM):<12} "
              f"EF: {ui.c(ef_str, ui.DIM):<8} "
              f"Prio: {ui.c(label, col)}")
    print()
    ui.info("Themen sind in topologischer Reihenfolge: Voraussetzungen zuerst.")
    print()


# ── edge ───────────────────────────────────────────────────────────────────────

@click.group("edge")
def cmd_edge():
    """Kanten im Wissensgraph manuell verwalten."""


@cmd_edge.command("add")
@click.argument("from_topic")
@click.argument("to_topic")
@click.option("--weight", "-w", default=0.6, help="Kantengewicht 0.1-1.0")
def edge_add(from_topic: str, to_topic: str, weight: float):
    """Abhängigkeit hinzufügen (FROM ist Voraussetzung für TO)."""
    from lernos.db.schema import startup
    from lernos.db.topics import create_edge, get_all_edges
    from lernos.fuzzy.resolve import resolve_topic
    from lernos.cmd.add import _would_create_cycle

    conn   = startup()
    t_from = resolve_topic(conn, from_topic)
    t_to   = resolve_topic(conn, to_topic)
    if not t_from:
        ui.error(f"Topic '{from_topic}' nicht gefunden.")
        sys.exit(1)
    if not t_to:
        ui.error(f"Topic '{to_topic}' nicht gefunden.")
        sys.exit(1)

    all_edges = get_all_edges(conn)
    if _would_create_cycle(all_edges, t_from.id, t_to.id):
        ui.error("Kante würde einen Zykel erzeugen — abgebrochen.")
        sys.exit(1)

    w = max(0.1, min(1.0, weight))
    create_edge(conn, t_from.id, t_to.id, weight=w, confirmed=True)
    ui.success(f"Kante: {t_from.name} → {t_to.name} (Gewicht: {w:.2f})")
    if w >= 0.6:
        ui.info(f"Kaskade aktiv: Fehlschlag in '{t_from.name}' setzt '{t_to.name}' auf REVIEW.")


@cmd_edge.command("list")
@click.argument("topic_name", shell_complete=complete_topic_names)
def edge_list(topic_name: str):
    """Alle Kanten eines Topics anzeigen."""
    from lernos.db.schema import startup
    from lernos.db.topics import get_edges_for_topic
    from lernos.fuzzy.resolve import resolve_topic

    conn  = startup()
    topic = resolve_topic(conn, topic_name)
    if not topic:
        ui.error(f"Topic '{topic_name}' nicht gefunden.")
        sys.exit(1)

    edges = get_edges_for_topic(conn, topic.id)
    ui.header(f"Kanten: {topic.name}", "")

    if edges["incoming"]:
        ui.section("Voraussetzungen (eingehend)")
        for e in edges["incoming"]:
            bar      = ui.progress_bar(e.weight, max_val=1.0, width=12)
            cascade  = ui.c("  ⚡ Kaskade aktiv", ui.BRIGHT_YELLOW) if e.weight >= 0.6 else ""
            print(f"  {ui.c('←', ui.BRIGHT_GREEN)} {e.from_name:<30} {bar} {e.weight:.2f}{cascade}")
    else:
        ui.info("Keine Voraussetzungen (Basisthema).")

    if edges["outgoing"]:
        ui.section("Abhängige Topics (ausgehend)")
        for e in edges["outgoing"]:
            bar     = ui.progress_bar(e.weight, max_val=1.0, width=12)
            cascade = ui.c("  ⚡ Kaskade aktiv", ui.BRIGHT_YELLOW) if e.weight >= 0.6 else ""
            print(f"  {ui.c('→', ui.BRIGHT_BLUE)} {e.to_name:<30} {bar} {e.weight:.2f}{cascade}")
    else:
        ui.info("Keine abhängigen Topics.")
    print()


@cmd_edge.command("delete")
@click.argument("from_topic")
@click.argument("to_topic")
def edge_delete(from_topic: str, to_topic: str):
    """Kante zwischen zwei Topics löschen."""
    from lernos.db.schema import startup
    from lernos.db.topics import delete_edge
    from lernos.fuzzy.resolve import resolve_topic

    conn   = startup()
    t_from = resolve_topic(conn, from_topic)
    t_to   = resolve_topic(conn, to_topic)
    if not t_from or not t_to:
        ui.error("Eines der Topics nicht gefunden.")
        sys.exit(1)
    delete_edge(conn, t_from.id, t_to.id)
    ui.success(f"Kante {t_from.name} → {t_to.name} gelöscht.")


@cmd_edge.command("cleanup")
@click.option("--threshold", default=0.25, help="Ähnlichkeits-Schwellenwert für Warnung")
@click.option("--auto", is_flag=True, help="Ohne Rückfrage löschen")
def edge_cleanup(threshold: float, auto: bool):
    """Schwache Kanten via Vektor-Ähnlichkeit finden und entfernen."""
    from lernos.db.schema import startup
    from lernos.db.topics import delete_edge, get_all_edges, get_topic_by_id
    from lernos.ollama.embed import blob_to_embedding, cosine_similarity

    conn      = startup()
    all_edges = get_all_edges(conn)
    weak      = []

    for e in all_edges:
        tf = get_topic_by_id(conn, e.from_id)
        tt = get_topic_by_id(conn, e.to_id)
        if tf and tt and tf.embedding and tt.embedding:
            v1  = blob_to_embedding(tf.embedding)
            v2  = blob_to_embedding(tt.embedding)
            sim = cosine_similarity(v1, v2)
            if sim < threshold:
                weak.append((e, sim, tf, tt))

    if not weak:
        ui.success("Keine schwachen Kanten gefunden — Graph ist sauber!")
        return

    ui.header("🧹 Edge Cleanup", f"{len(weak)} schwache Kante(n) gefunden (< {threshold})")

    if auto:
        # Non-interactive: alles löschen
        for e, sim, tf, tt in weak:
            delete_edge(conn, e.from_id, e.to_id)
        ui.success(f"Auto-Cleanup: {len(weak)} Kante(n) gelöscht.")
        return

    # Interaktive Multiauswahl: welche Kanten sollen gelöscht werden?
    labels = [
        f"{tf.name}  →  {tt.name}  "
        f"{ui.c(f'Sim:{sim:.2f}', ui.DIM)}  "
        f"{ui.progress_bar(sim, max_val=1.0, width=10)}"
        for e, sim, tf, tt in weak
    ]
    chosen = ui.multiselect("Zu löschende Kanten auswählen", labels,
                            selected=list(range(len(weak))))

    deleted = 0
    for idx in chosen:
        e, sim, tf, tt = weak[idx]
        delete_edge(conn, e.from_id, e.to_id)
        deleted += 1

    if deleted:
        ui.success(f"Cleanup: {deleted} Kante(n) gelöscht.")
    else:
        ui.info("Nichts gelöscht.")


# ── notify ─────────────────────────────────────────────────────────────────────

@click.command("notify")
@click.option("--dry-run", is_flag=True, help="Nur Vorschau, kein Telegram-Versand")
def cmd_notify(dry_run: bool):
    """
    Täglicher Cron-Job:
      1. Abgelaufene FROZEN-Topics reaktivieren
      2. Fällige Topics sammeln
      3. Formatierte Telegram-Nachricht senden
    """
    from lernos.db.schema import startup
    from lernos.db.topics import get_due_topics, thaw_expired_frozen

    conn = startup()

    # Abgelaufene Freezes reaktivieren
    thawed = thaw_expired_frozen(conn)
    if thawed:
        ui.info(f"{thawed} Topic(s) aus FROZEN reaktiviert.")

    topics = get_due_topics(conn)
    state_emoji = {
        "NEW": "🆕", "LEARNING": "⚠️", "REVIEW": "🔄",
        "MASTERED": "✅", "FROZEN": "❄️",
    }

    if not topics:
        msg = "✅ *LernOS* — Heute nichts fällig\\. Genieß den Tag\\!"
        if dry_run:
            _print_dry_run(msg)
        else:
            _send_telegram(msg)
        return

    # Nachricht aufbauen (Telegram MarkdownV2)
    lines = [f"📚 *LernOS Tagesplan* — {len(topics)} Topic(s)\n"]

    # Nach Priorität sortieren: LEARNING zuerst
    PRIO = {"LEARNING": 0, "NEW": 1, "REVIEW": 2, "MASTERED": 3}
    topics_sorted = sorted(topics, key=lambda t: PRIO.get(t.state, 9))

    for i, t in enumerate(topics_sorted[:10], 1):
        emoji   = state_emoji.get(t.state, "•")
        mod_str = f" \\({t.module}\\)" if t.module else ""
        name    = t.name.replace(".", "\\.").replace("-", "\\-").replace("(", "\\(").replace(")", "\\)")
        lines.append(f"{i}\\. {emoji} {name}{mod_str}")

    if len(topics) > 10:
        lines.append(f"\n_\\.\\.\\. und {len(topics) - 10} weitere_")

    # Statistik anhängen
    learning_count = sum(1 for t in topics if t.state == "LEARNING")
    if learning_count:
        lines.append(f"\n⚠️ *{learning_count} im LEARNING\\-Zustand* \\(höchste Priorität\\)")

    msg = "\n".join(lines)

    if dry_run:
        _print_dry_run(msg)
    else:
        success = _send_telegram(msg)
        if success:
            conn.execute(
                "INSERT INTO notifications (topic_count, payload) VALUES (?,?)",
                (len(topics), json.dumps([t.id for t in topics]))
            )
            conn.commit()
            ui.success(f"Telegram-Benachrichtigung gesendet ({len(topics)} Topics).")


def _print_dry_run(msg: str):
    ui.header("Notify Dry-Run", "")
    print(f"  {ui.c('Nachricht (MarkdownV2):', ui.DIM)}")
    for line in msg.split("\n"):
        print(f"    {line}")
    print()


def _send_telegram(msg: str) -> bool:
    """
    Sendet Nachricht via Telegram Bot API (MarkdownV2).
    Gibt True bei Erfolg zurück, False bei Fehler.
    """
    cfg     = _load_config()
    token   = cfg.get("telegram_token", "").strip()
    chat_id = cfg.get("telegram_chat_id", "").strip()

    if not token or not chat_id:
        ui.warn("Telegram nicht konfiguriert. Führe 'lernos config' aus.")
        ui.info("Nachricht:\n" + msg)
        return False

    try:
        import requests as req
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r   = req.post(
            url,
            json={
                "chat_id":    chat_id,
                "text":       msg,
                "parse_mode": "MarkdownV2",
            },
            timeout=10,
        )
        if r.status_code == 200:
            return True
        else:
            data = r.json()
            ui.error(f"Telegram API Fehler {r.status_code}: {data.get('description', r.text)}")
            # Fallback: ohne Markdown senden
            r2 = req.post(
                url,
                json={"chat_id": chat_id, "text": msg.replace("\\", "")},
                timeout=10,
            )
            return r2.status_code == 200
    except Exception as e:
        ui.error(f"Telegram nicht erreichbar: {e}")
        return False


# ── config ─────────────────────────────────────────────────────────────────────

def _config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".lernosrc")


def _load_config() -> dict:
    if os.path.exists(_config_path()):
        try:
            with open(_config_path()) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_config(cfg: dict):
    with open(_config_path(), "w") as f:
        json.dump(cfg, f, indent=2)


@click.command("config")
@click.option("--show", is_flag=True, help="Aktuelle Konfiguration anzeigen")
@click.option("--test-telegram", is_flag=True, help="Telegram-Verbindung testen")
def cmd_config(show: bool, test_telegram: bool):
    """Einstellungen verwalten (Telegram-Bot, Ollama-URL)."""
    cfg = _load_config()

    if test_telegram:
        ui.info("Sende Test-Nachricht an Telegram...")
        success = _send_telegram("🧪 *LernOS Test* — Verbindung erfolgreich\\!")
        if success:
            ui.success("Telegram-Test erfolgreich!")
        return

    if show:
        ui.header("⚙️  Konfiguration", "")
        if not cfg:
            ui.info("Keine Konfiguration gefunden.")
        for k, v in cfg.items():
            masked = v[:6] + "***" if ("token" in k or "key" in k) and len(v) > 6 else v
            print(f"  {ui.c(k + ':', ui.DIM)} {masked}")
        print()
        return

    ui.header("⚙️  LernOS Konfiguration", "ENTER = Wert beibehalten")

    ui.section("Telegram-Benachrichtigungen")
    ui.info("Erstelle einen Bot via @BotFather auf Telegram.")
    token = ui.prompt("Telegram Bot Token", cfg.get("telegram_token", ""))
    if token:
        cfg["telegram_token"] = token
    chat_id = ui.prompt("Telegram Chat-ID", cfg.get("telegram_chat_id", ""))
    if chat_id:
        cfg["telegram_chat_id"] = chat_id

    ui.section("Ollama LLM")
    ollama_url = ui.prompt("Ollama URL", cfg.get("ollama_url", "http://localhost:11434"))
    cfg["ollama_url"] = ollama_url
    ollama_model = ui.prompt("Ollama Modell", cfg.get("ollama_model", "phi3"))
    cfg["ollama_model"] = ollama_model

    ui.section("Speicherpfade (für Sync via Nextcloud/Dropbox etc.)")
    ui.info("Leer lassen für Standard-Pfade (~/.lernosdb / ~/.lernos_docs)")
    db_path = ui.prompt("Datenbankpfad", cfg.get("db_path", ""))
    if db_path.strip():
        cfg["db_path"] = db_path.strip()
    elif "db_path" in cfg:
        del cfg["db_path"]

    docs_path = ui.prompt("Dokumente-Verzeichnis", cfg.get("docs_path", ""))
    if docs_path.strip():
        cfg["docs_path"] = docs_path.strip()
    elif "docs_path" in cfg:
        del cfg["docs_path"]

    if db_path.strip() or docs_path.strip():
        ui.info(f"Neue DB: {db_path or '~/.lernosdb (Standard)'}")
        ui.info(f"Neue Docs: {docs_path or '~/.lernos_docs (Standard)'}")
        ui.warn("Bestehende Daten werden NICHT automatisch verschoben!")
        ui.info("Anleitung: cp ~/.lernosdb <neuer-pfad> && cp -r ~/.lernos_docs <neuer-docs-pfad>")

    _save_config(cfg)
    ui.success("Konfiguration gespeichert.")

    if token and chat_id:
        if ui.confirm("Telegram-Verbindung jetzt testen?", True):
            _send_telegram("🧪 *LernOS* — Konfiguration erfolgreich\\!")


@click.command("install-scheduler")
def cmd_install_scheduler():
    """systemd-Timer für tägliche Benachrichtigungen installieren."""
    import shutil
    lernos_bin = shutil.which("lernos") or "lernos"

    service = f"""[Unit]
Description=LernOS Daily Notification
After=network.target

[Service]
Type=oneshot
ExecStart={lernos_bin} notify
StandardOutput=journal
"""
    timer = """[Unit]
Description=LernOS Daily Timer

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
"""
    systemd_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(systemd_dir, exist_ok=True)

    svc_path = os.path.join(systemd_dir, "lernos.service")
    tmr_path = os.path.join(systemd_dir, "lernos.timer")

    with open(svc_path, "w") as f:
        f.write(service)
    with open(tmr_path, "w") as f:
        f.write(timer)

    ui.success(f"Service: {svc_path}")
    ui.success(f"Timer:   {tmr_path}")
    print()
    ui.info("Aktivieren mit:")
    print(f"  {ui.c('systemctl --user daemon-reload', ui.BRIGHT_CYAN)}")
    print(f"  {ui.c('systemctl --user enable --now lernos.timer', ui.BRIGHT_CYAN)}")
    print(f"  {ui.c('systemctl --user status lernos.timer', ui.BRIGHT_CYAN)}")
    print()
    ui.info("macOS LaunchAgent:")
    _print_macos_launchagent(lernos_bin)


def _print_macos_launchagent(lernos_bin: str):
    home = os.path.expanduser("~")
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.lernos.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>{lernos_bin}</string>
        <string>notify</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>8</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
</dict>
</plist>"""
    plist_path = os.path.join(home, "Library/LaunchAgents/com.lernos.daily.plist")
    print(f"\n  Pfad: {ui.c(plist_path, ui.DIM)}")
    print(f"  Aktivieren: {ui.c('launchctl load ' + plist_path, ui.BRIGHT_CYAN)}")

    if ui.confirm("macOS plist jetzt erstellen?", False):
        os.makedirs(os.path.dirname(plist_path), exist_ok=True)
        with open(plist_path, "w") as f:
            f.write(plist)
        ui.success(f"Erstellt: {plist_path}")


# ── import-csv ─────────────────────────────────────────────────────────────────

@click.command("import-csv")
@click.argument("filepath")
@click.option("--delimiter", default=",", help="CSV-Trennzeichen (Standard: Komma)")
def cmd_import_csv(filepath: str, delimiter: str):
    """
    Topics aus CSV importieren.

    Format: Name, Modul, Beschreibung (Header wird automatisch erkannt)

    Beispiel:
      Grenzwerte, Analysis I, Definition und Berechnung von Grenzwerten
      Stetigkeit, Analysis I, Stetigkeit von Funktionen
    """
    import csv
    from lernos.db.schema import startup
    from lernos.db.topics import create_topic, get_topic_by_name

    conn = startup()
    if not os.path.exists(filepath):
        ui.error(f"Datei '{filepath}' nicht gefunden.")
        sys.exit(1)

    added = 0; skipped = 0; errors = 0

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=delimiter)
        for line_num, row in enumerate(reader, 1):
            if not row or len(row) < 1:
                continue
            name = row[0].strip()
            if not name or name.lower() in ("name", "thema", "topic"):
                continue   # Header-Zeile überspringen
            mod  = row[1].strip() if len(row) > 1 else ""
            desc = row[2].strip() if len(row) > 2 else ""

            if get_topic_by_name(conn, name):
                skipped += 1
            else:
                try:
                    create_topic(conn, name, mod, desc)
                    added += 1
                except Exception as e:
                    ui.warn(f"Zeile {line_num}: Fehler bei '{name}': {e}")
                    errors += 1

    ui.success(f"CSV-Import: {added} hinzugefügt, {skipped} übersprungen"
               + (f", {errors} Fehler" if errors else ""))
    if added:
        ui.info(f"Embeddings holen mit: lernos add (oder in Batch via Skript)")
