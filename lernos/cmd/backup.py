"""
LernOS — `lernos backup` / `lernos restore`

backup: Erstellt ZIP mit SQLite-DB + docs-Verzeichnis
restore: Entpackt ZIP, ersetzt DB + docs (mit Sicherheits-Backup)

ZIP-Struktur:
  lernos_backup_YYYYMMDD_HHMMSS/
    lernosdb          ← SQLite-Datenbankdatei
    docs/             ← Alle angehängten PDFs
    meta.json         ← Version, Zeitstempel, Statistiken
"""
from __future__ import annotations
import os
import sys
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

import click

from lernos import ui


def _create_backup_zip(db_path: str, docs_dir: str, output_path: str) -> dict:
    """
    Packt DB + docs in ein ZIP-Archiv.
    Gibt Meta-Dict mit Statistiken zurück.
    """
    # Statistiken aus DB lesen
    meta = {
        "version":    "1.4",
        "created_at": datetime.now().isoformat(),
        "db_path":    db_path,
        "docs_path":  docs_dir,
        "topics":     0,
        "sessions":   0,
        "documents":  0,
    }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        meta["topics"]   = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        meta["sessions"] = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        meta["documents"]= conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        conn.close()
    except Exception:
        pass

    stamp      = datetime.now().strftime("%Y%m%d_%H%M%S")
    inner_name = f"lernos_backup_{stamp}"

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # meta.json
        zf.writestr(f"{inner_name}/meta.json", json.dumps(meta, indent=2, ensure_ascii=False))

        # DB — per SQLite backup API für konsistenten Snapshot (auch bei laufender DB)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            src = sqlite3.connect(db_path)
            dst = sqlite3.connect(tmp_path)
            src.backup(dst)
            src.close(); dst.close()
            zf.write(tmp_path, f"{inner_name}/lernosdb")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # Docs-Verzeichnis
        if os.path.exists(docs_dir):
            for root, _dirs, files in os.walk(docs_dir):
                for file in files:
                    full = os.path.join(root, file)
                    rel  = os.path.relpath(full, os.path.dirname(docs_dir))
                    zf.write(full, f"{inner_name}/docs/{rel.split(os.sep, 1)[-1]}")

    return meta


def _restore_backup_zip(zip_path: str, db_path: str, docs_dir: str,
                         yes: bool = False) -> dict:
    """
    Stellt Backup wieder her.
    Sichert bestehende Daten vorher in DB_PATH.restore_backup.
    """
    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"Backup-Datei nicht gefunden: {zip_path}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        # meta.json finden
        meta_names = [n for n in names if n.endswith("meta.json")]
        if not meta_names:
            raise ValueError("Ungültiges Backup: meta.json fehlt.")
        meta = json.loads(zf.read(meta_names[0]))

        db_names  = [n for n in names if n.endswith("/lernosdb") or n == "lernosdb"]
        if not db_names:
            raise ValueError("Ungültiges Backup: Datenbankdatei fehlt.")

        with tempfile.TemporaryDirectory() as tmpdir:
            zf.extractall(tmpdir)
            inner_dirs = [d for d in os.listdir(tmpdir)
                         if os.path.isdir(os.path.join(tmpdir, d))]
            if not inner_dirs:
                raise ValueError("Backup-Archiv hat unerwartete Struktur.")
            inner = os.path.join(tmpdir, inner_dirs[0])

            # Sicherheits-Backup der aktuellen Daten
            if os.path.exists(db_path):
                safety = db_path + ".restore_backup"
                shutil.copy2(db_path, safety)
                if not ui._quiet:
                    ui.info(f"Sicherheitskopie: {safety}")

            # DB ersetzen
            src_db = os.path.join(inner, "lernosdb")
            shutil.copy2(src_db, db_path)

            # docs ersetzen
            # BUG FIX: Auch wenn kein docs/-Ordner im Backup vorhanden ist,
            # muss der aktuelle docs_dir geleert werden — sonst bleiben verwaiste
            # PDF-Dateien als Orphans zurück, die die wiederhergestellte DB
            # nicht mehr kennt (Speicherleck + inkonsistenter Zustand).
            src_docs = os.path.join(inner, "docs")
            if os.path.exists(docs_dir):
                shutil.rmtree(docs_dir)   # Immer räumen — ob Backup-Docs da oder nicht
            if os.path.exists(src_docs):
                shutil.copytree(src_docs, docs_dir)
            else:
                os.makedirs(docs_dir, exist_ok=True)  # Leeres Verzeichnis anlegen

    return meta


@click.command("backup")
@click.option("--output", "-o", default="",
              help="Ausgabepfad (Standard: ~/lernos_backup_DATUM.zip)")
@click.option("--yes", "-y", is_flag=True, help="Ohne Bestätigung")
def cmd_backup(output: str, yes: bool):
    """
    Vollständiges Backup erstellen (DB + alle PDFs).

    \b
    Erstellt ein ZIP-Archiv mit:
      - SQLite-Datenbank (konsistenter Snapshot via SQLite-Backup-API)
      - Alle angehängten PDF-Dateien
      - Metadaten (Topics, Sessions, Zeitstempel)

    \b
    Beispiel:
      lernos backup
      lernos backup --output ~/Nextcloud/lernos_backup.zip
    """
    from lernos.db.schema import get_db_path, get_docs_dir

    db_path  = get_db_path()
    docs_dir = get_docs_dir()

    if not os.path.exists(db_path):
        ui.error(f"Datenbank nicht gefunden: {db_path}")
        sys.exit(1)

    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not output:
        output = os.path.join(os.path.expanduser("~"), f"lernos_backup_{stamp}.zip")

    ui.header("💾 LernOS Backup", db_path)

    try:
        meta = _create_backup_zip(db_path, docs_dir, output)
    except Exception as e:
        ui.error(f"Backup fehlgeschlagen: {e}")
        sys.exit(1)

    size_kb = os.path.getsize(output) // 1024
    ui.success(f"Backup erstellt: {output}")
    ui.info(f"{meta['topics']} Topics · {meta['sessions']} Sessions · "
            f"{meta['documents']} Dokumente · {size_kb} KB")

    # Machine-readable bei --quiet
    if ui._quiet:
        print(output)


@click.command("restore")
@click.argument("backup_zip")
@click.option("--yes", "-y", is_flag=True, help="Ohne Bestätigung")
def cmd_restore(backup_zip: str, yes: bool):
    """
    Backup wiederherstellen aus ZIP-Archiv.

    \b
    ⚠️  Überschreibt aktuelle Datenbank und PDF-Dateien.
    Erstellt vorher automatisch eine Sicherheitskopie (DB.restore_backup).

    \b
    Beispiel:
      lernos restore ~/lernos_backup_20260227.zip
      lernos restore backup.zip --yes
    """
    from lernos.db.schema import get_db_path, get_docs_dir

    if not os.path.exists(backup_zip):
        ui.error(f"Datei nicht gefunden: {backup_zip}")
        sys.exit(1)

    db_path  = get_db_path()
    docs_dir = get_docs_dir()

    # Vorschau aus meta.json
    try:
        with zipfile.ZipFile(backup_zip) as zf:
            meta_names = [n for n in zf.namelist() if n.endswith("meta.json")]
            if meta_names:
                meta = json.loads(zf.read(meta_names[0]))
                ui.header("📦 Backup wiederherstellen",
                          f"Erstellt: {meta.get('created_at','?')[:19]}")
                ui.info(f"Enthält: {meta.get('topics','?')} Topics · "
                        f"{meta.get('documents','?')} Dokumente")
    except Exception:
        ui.header("📦 Backup wiederherstellen", backup_zip)

    ui.warn("Aktuelle Datenbank und PDFs werden überschrieben!")
    ui.info(f"Sicherheitskopie wird unter {db_path}.restore_backup gespeichert.")

    if not (yes or ui._yes_all or ui.confirm("Fortfahren?", default=False)):
        ui.info("Abgebrochen.")
        return

    try:
        meta = _restore_backup_zip(backup_zip, db_path, docs_dir, yes=yes)
    except (FileNotFoundError, ValueError) as e:
        ui.error(str(e))
        sys.exit(1)
    except Exception as e:
        ui.error(f"Restore fehlgeschlagen: {e}")
        sys.exit(1)

    ui.success("Backup erfolgreich wiederhergestellt.")
    ui.info(f"DB: {db_path}")
    ui.info(f"Docs: {docs_dir}")
    ui.info(f"Sicherheitskopie der alten Daten: {db_path}.restore_backup")
