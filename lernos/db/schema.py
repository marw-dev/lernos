"""
LernOS — SQLite Schema & Migration mit automatischem Backup

Migration-Prinzipien:
    - Jede Migration läuft in einer Transaktion (BEGIN/COMMIT)
    - Bei Fehler: ROLLBACK, Exception nach oben — Versionsnummer wird NIE
      hochgesetzt wenn die Migration fehlschlug
    - ALTER TABLE ADD COLUMN nur wenn Spalte noch nicht existiert
      (geprüft via PRAGMA table_info — kein blindes except-pass)
    - Neue Tabellen: immer CREATE TABLE IF NOT EXISTS
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime

log = logging.getLogger("lernos.db.schema")

SCHEMA_VERSION = 2

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    module          TEXT    NOT NULL DEFAULT '',
    description     TEXT    DEFAULT '',
    state           TEXT    NOT NULL DEFAULT 'NEW',
    ef              REAL    NOT NULL DEFAULT 2.5,
    interval_d      INTEGER NOT NULL DEFAULT 1,
    repetitions     INTEGER NOT NULL DEFAULT 0,
    learning_resets INTEGER NOT NULL DEFAULT 0,
    due_date        TEXT    NOT NULL DEFAULT (date('now')),
    frozen_until    TEXT    DEFAULT NULL,
    embedding       BLOB    DEFAULT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS edges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    to_id      INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    weight     REAL    NOT NULL DEFAULT 0.5,
    confirmed  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_id, to_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id     INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    grade        INTEGER NOT NULL,
    confidence   INTEGER NOT NULL,
    correct      INTEGER NOT NULL,
    old_state    TEXT    NOT NULL,
    new_state    TEXT    NOT NULL,
    old_ef       REAL    NOT NULL,
    new_ef       REAL    NOT NULL,
    old_interval INTEGER NOT NULL,
    new_interval INTEGER NOT NULL,
    reviewed_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id     INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    filename     TEXT    NOT NULL,
    filepath     TEXT    NOT NULL,
    file_size    INTEGER NOT NULL DEFAULT 0,
    page_count   INTEGER NOT NULL DEFAULT 0,
    text_excerpt TEXT    DEFAULT '',
    full_text    TEXT    DEFAULT '',
    added_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generated_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    question    TEXT    NOT NULL,
    answer      TEXT    NOT NULL DEFAULT '',
    difficulty  INTEGER NOT NULL DEFAULT 3,
    used_count  INTEGER NOT NULL DEFAULT 0,
    last_used   TEXT    DEFAULT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    topic_count INTEGER NOT NULL,
    payload     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_due      ON topics(due_date, state);
CREATE INDEX IF NOT EXISTS idx_topics_state    ON topics(state);
CREATE INDEX IF NOT EXISTS idx_topics_module   ON topics(module);
CREATE INDEX IF NOT EXISTS idx_edges_from      ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to        ON edges(to_id);
CREATE INDEX IF NOT EXISTS idx_sessions_topic  ON sessions(topic_id, reviewed_at);
CREATE INDEX IF NOT EXISTS idx_docs_topic      ON documents(topic_id);
CREATE INDEX IF NOT EXISTS idx_questions_topic ON generated_questions(topic_id);
"""


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Prüft via PRAGMA table_info ob eine Spalte existiert."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """
    Migration v1 → v2: learning_resets-Spalte + documents/generated_questions.

    Läuft in einer Transaktion. Bei Fehler: Exception propagiert nach oben,
    Versionsnummer wird NICHT hochgesetzt.
    Jeder Schritt wird nur ausgeführt wenn nötig — idempotent.
    """
    # ALTER TABLE ADD COLUMN nur wenn Spalte fehlt
    if not _column_exists(conn, "topics", "learning_resets"):
        conn.execute(
            "ALTER TABLE topics ADD COLUMN learning_resets INTEGER NOT NULL DEFAULT 0"
        )
        log.info("Migration v2: Spalte learning_resets hinzugefügt")
    else:
        log.debug("Migration v2: learning_resets bereits vorhanden — übersprungen")

    # Neue Tabellen: IF NOT EXISTS ist sicher bei Wiederholung
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id     INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            filename     TEXT    NOT NULL,
            filepath     TEXT    NOT NULL,
            file_size    INTEGER NOT NULL DEFAULT 0,
            page_count   INTEGER NOT NULL DEFAULT 0,
            text_excerpt TEXT    DEFAULT '',
            full_text    TEXT    DEFAULT '',
            added_at     TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS generated_questions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            question    TEXT    NOT NULL,
            answer      TEXT    NOT NULL DEFAULT '',
            difficulty  INTEGER NOT NULL DEFAULT 3,
            used_count  INTEGER NOT NULL DEFAULT 0,
            last_used   TEXT    DEFAULT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_docs_topic      ON documents(topic_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_questions_topic ON generated_questions(topic_id)"
    )
    log.info("Migration v2: abgeschlossen")


def _load_path_config() -> dict:
    """
    Lädt Pfad-Konfiguration aus ~/.lernosrc.

    Bei ungültigem JSON wird eine WARNING geloggt und ein leeres Dict
    zurückgegeben — stilles Schlucken würde den User tagelang verwirren
    warum seine DB am falschen Ort landet.
    """
    cfg_path = os.path.join(os.path.expanduser("~"), ".lernosrc")
    if not os.path.exists(cfg_path):
        return {}
    try:
        import json as _json
        with open(cfg_path) as f:
            return _json.load(f)
    except ValueError as e:
        # JSONDecodeError ist Unterklasse von ValueError
        log.warning(
            "~/.lernosrc enthält kein valides JSON — Konfiguration ignoriert. "
            "Fehler: %s",
            e,
        )
        return {}
    except OSError as e:
        log.warning("~/.lernosrc konnte nicht gelesen werden: %s", e)
        return {}


def get_db_path() -> str:
    """Datenbankpfad — überschreibbar via `lernos config --db-path`."""
    cfg = _load_path_config()
    if cfg.get("db_path"):
        return os.path.expanduser(cfg["db_path"])
    return os.path.join(os.path.expanduser("~"), ".lernosdb")


def get_docs_dir() -> str:
    """Dokumentenverzeichnis — überschreibbar via `lernos config --docs-path`."""
    cfg = _load_path_config()
    if cfg.get("docs_path"):
        d = os.path.expanduser(cfg["docs_path"])
    else:
        d = os.path.join(os.path.expanduser("~"), ".lernos_docs")
    os.makedirs(d, exist_ok=True)
    return d


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _rolling_backup(db_path: str, keep: int = 5) -> None:
    """Erstellt rolling Backup (max. keep Versionen)."""
    backup_dir = os.path.join(os.path.dirname(db_path), ".lernos_backups")
    os.makedirs(backup_dir, exist_ok=True)

    stamp       = datetime.now().strftime("%Y%m%d")
    backup_path = os.path.join(backup_dir, f"lernosdb_{stamp}.bak")

    if os.path.exists(backup_path):
        return

    shutil.copy2(db_path, backup_path)

    backups = sorted(f for f in os.listdir(backup_dir) if f.endswith(".bak"))
    while len(backups) > keep:
        os.remove(os.path.join(backup_dir, backups.pop(0)))


def migrate(conn: sqlite3.Connection) -> None:
    """
    Führt Schema-Migrationen aus.

    Garantien:
        - Versionsnummer wird nur hochgesetzt wenn die Migration erfolgreich war
        - Jede Migration läuft in einer Transaktion
        - Bei Fehler: Exception propagiert, DB bleibt konsistent
    """
    conn.executescript(SCHEMA_SQL)

    row             = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    current_version = row["version"] if row else 0

    if current_version == 0:
        conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
        return

    if current_version < 2:
        # Transaktion: Versionsnummer nur setzen wenn alle Schritte klappen
        try:
            conn.execute("BEGIN")
            _migrate_v1_to_v2(conn)
            conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
            conn.execute("COMMIT")
            log.info("Schema auf Version %d migriert", SCHEMA_VERSION)
        except Exception:
            conn.execute("ROLLBACK")
            raise   # DB bleibt in konsistentem Zustand, App startet nicht


def startup(db_path: str | None = None) -> sqlite3.Connection:
    """Öffnet DB, migriert, erstellt Backup. Einstiegspunkt für alle Befehle."""
    path = db_path or get_db_path()
    if os.path.exists(path):
        _rolling_backup(path)
    conn = get_connection(path)
    migrate(conn)
    return conn
