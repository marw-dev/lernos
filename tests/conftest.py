"""
LernOS — pytest conftest.py

Globale Fixtures und Konfiguration.
Kein globaler State, kein os.environ-Hack zwischen Imports.
monkeypatch isoliert Umgebungsvariablen pro Test.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from lernos.db.schema import get_connection, migrate


@pytest.fixture(autouse=True)
def isolate_db_path(monkeypatch, tmp_path):
    """
    Sorgt dafür dass JEDER Test seine eigene Datenbank bekommt.
    LERNOS_TEST wird nicht mehr global gesetzt — monkeypatch
    gilt nur für den jeweiligen Test und wird danach automatisch
    zurückgesetzt.
    """
    monkeypatch.setenv("LERNOS_TEST", "1")
    monkeypatch.setenv("LERNOS_DB_PATH", str(tmp_path / "test.db"))


@pytest.fixture
def db(tmp_path):
    """Frische SQLite-Datenbank pro Test — kein globaler State."""
    path = str(tmp_path / "lernos_test.db")
    conn = get_connection(path)
    migrate(conn)
    yield conn
    conn.close()
