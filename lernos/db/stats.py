"""
LernOS — Statistik-Abfragen
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta


def get_week_stats(conn: sqlite3.Connection, days: int = 7) -> dict:
    since = (datetime.now() - timedelta(days=days)).isoformat()

    total = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE reviewed_at >= ?", (since,)
    ).fetchone()[0]

    correct = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE reviewed_at >= ? AND correct=1", (since,)
    ).fetchone()[0]

    avg_conf = conn.execute(
        "SELECT AVG(confidence) FROM sessions WHERE reviewed_at >= ?", (since,)
    ).fetchone()[0]

    unique_topics = conn.execute(
        "SELECT COUNT(DISTINCT topic_id) FROM sessions WHERE reviewed_at >= ?", (since,)
    ).fetchone()[0]

    # Bester Wochentag (0=So, 1=Mo, ... 6=Sa)
    day_rows = conn.execute(
        """SELECT strftime('%w', reviewed_at) as dow, COUNT(*) as cnt
           FROM sessions WHERE reviewed_at >= ?
           GROUP BY dow ORDER BY cnt DESC LIMIT 1""",
        (since,)
    ).fetchone()

    day_names = {
        "0": "Sonntag", "1": "Montag", "2": "Dienstag",
        "3": "Mittwoch", "4": "Donnerstag", "5": "Freitag", "6": "Samstag"
    }
    best_day = day_names.get(day_rows["dow"], "?") if day_rows else "-"
    best_day_count = day_rows["cnt"] if day_rows else 0

    # Beste Tageszeit (Stunden-Block)
    hour_row = conn.execute(
        """SELECT strftime('%H', reviewed_at) as h, COUNT(*) as cnt
           FROM sessions WHERE reviewed_at >= ?
           GROUP BY h ORDER BY cnt DESC LIMIT 1""",
        (since,)
    ).fetchone()
    best_hour = f"{hour_row['h']}:00 – {int(hour_row['h'])+1}:30" if hour_row else "-"

    # Problematischste Topics
    hard_rows = conn.execute(
        """SELECT t.name, t.ef, t.state,
                  SUM(CASE WHEN s.correct=0 THEN 1 ELSE 0 END) as failures,
                  COUNT(*) as total
           FROM topics t
           JOIN sessions s ON s.topic_id = t.id
           WHERE s.reviewed_at >= ?
           GROUP BY t.id
           HAVING failures > 0
           ORDER BY t.ef ASC
           LIMIT 5""",
        (since,)
    ).fetchall()

    # Zustandsverteilung
    state_rows = conn.execute(
        """SELECT state, COUNT(*) as cnt FROM topics GROUP BY state"""
    ).fetchall()
    state_dist = {r["state"]: r["cnt"] for r in state_rows}

    return {
        "total_sessions":  total,
        "correct":         correct,
        "accuracy":        round(correct / total * 100) if total > 0 else 0,
        "avg_confidence":  round(avg_conf, 1) if avg_conf else 0,
        "unique_topics":   unique_topics,
        "best_day":        best_day,
        "best_day_count":  best_day_count,
        "best_hour":       best_hour,
        "hard_topics":     [dict(r) for r in hard_rows],
        "state_dist":      state_dist,
        "days":            days,
    }


def get_session_history(conn: sqlite3.Connection,
                        topic_id: int, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """SELECT s.*, t.name
           FROM sessions s JOIN topics t ON t.id = s.topic_id
           WHERE s.topic_id = ?
           ORDER BY s.reviewed_at DESC LIMIT ?""",
        (topic_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def get_total_topics(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]


def get_total_sessions(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def get_streak(conn: sqlite3.Connection) -> dict:
    """
    Berechnet den aktuellen und längsten Lernstreak.

    Streak = aufeinanderfolgende Tage mit mind. einer Review-Session.
    Heute zählt mit, auch wenn noch keine Session heute war (Streak "in Gefahr").

    Returns:
        current:     Aktuelle Streak-Länge in Tagen
        longest:     Längste je erreichte Streak
        active_today: True wenn heute bereits gelernt wurde
        last_7_days: Liste von (date_str, session_count) für die letzten 7 Tage
    """
    today = datetime.now().date()

    # Alle eindeutigen Lerntage holen (neueste zuerst)
    rows = conn.execute(
        """SELECT date(reviewed_at) as d, COUNT(*) as cnt
           FROM sessions
           GROUP BY d
           ORDER BY d DESC"""
    ).fetchall()

    day_counts = {r["d"]: r["cnt"] for r in rows}
    today_str  = today.isoformat()

    # Heutiger Stand
    active_today = today_str in day_counts

    # Aktuellen Streak berechnen
    # Zählt rückwärts ab heute (bzw. gestern wenn heute noch keine Session)
    current = 0
    check   = today

    # Heute mitrechnen wenn bereits gelernt, sonst ab gestern
    if active_today:
        while check.isoformat() in day_counts:
            current += 1
            check -= timedelta(days=1)
    else:
        # Gestern prüfen — vielleicht läuft der Streak noch
        yesterday = today - timedelta(days=1)
        check = yesterday
        while check.isoformat() in day_counts:
            current += 1
            check -= timedelta(days=1)
        # Streak ist "in Gefahr" wenn gestern gelernt aber heute noch nicht
        # current bleibt wie berechnet — heute muss noch gelernt werden

    # Längsten Streak berechnen (über alle Zeiten)
    longest = 0
    streak  = 0
    if rows:
        all_days = sorted(day_counts.keys())
        prev_d   = None
        for d_str in all_days:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
            if prev_d is not None and (d - prev_d).days == 1:
                streak += 1
            else:
                streak = 1
            longest  = max(longest, streak)
            prev_d   = d

    # Letzte 7 Tage für Mini-Übersicht
    last_7 = []
    for i in range(6, -1, -1):
        d     = today - timedelta(days=i)
        d_str = d.isoformat()
        last_7.append((d_str, day_counts.get(d_str, 0)))

    return {
        "current":      current,
        "longest":      longest,
        "active_today": active_today,
        "last_7":       last_7,
    }
