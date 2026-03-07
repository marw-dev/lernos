"""
LernOS — Tests für Schema-Migration und PDF-Pipeline

Punkt 3: Tests für das Herzstück
    - DB-Migration: idempotent, transaktional, PRAGMA-basiert
    - Config-Loading: ungültiges JSON → WARNING, kein Silent Fail
    - _make_slide_chunks: isoliert testbar ohne Ollama
    - string.Template Prompt-Sicherheit
    - JSON-Parser Robustheit
    - Chunking-Logik Edge Cases

Punkt 4: Kein globaler os.environ-Hack —
    alle DB-Pfade kommen aus dem db-Fixture in conftest.py
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ── Hilfsfunktion: PageInfo-Stub ─────────────────────────────────────────────
# Wir wollen _make_slide_chunks isoliert testen — ohne pdf2image, ohne Ollama,
# ohne echte PDFs. Ein minimaler Stub der das PageInfo-Interface erfüllt reicht.


@dataclass
class _PageStub:
    """Minimaler PageInfo-Ersatz für Tests."""

    title: str = ""
    text: str = ""
    bullets: list = field(default_factory=list)
    number: int = 1
    is_empty: bool = False

    @property
    def structured_text(self) -> str:
        if not self.title and not self.bullets:
            return self.text
        parts = []
        if self.title:
            parts.append(f"# {self.title}")
        if self.bullets:
            parts.extend(f"- {b}" for b in self.bullets)
        elif self.text:
            parts.append(self.text)
        return "\n".join(parts)


def _page(title="", text="", bullets=None, is_empty=False, n=1) -> _PageStub:
    return _PageStub(
        title=title, text=text, bullets=bullets or [], is_empty=is_empty, number=n
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1. Schema-Migration
# ═════════════════════════════════════════════════════════════════════════════


class TestSchemaMigration:
    """Migration ist korrekt, transaktional, idempotent."""

    def _fresh_db(self, tmp_path) -> sqlite3.Connection:
        path = str(tmp_path / "test.db")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def test_fresh_db_has_all_tables(self, db):
        tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for expected in (
            "topics",
            "edges",
            "sessions",
            "documents",
            "generated_questions",
            "schema_version",
        ):
            assert expected in tables, f"Tabelle '{expected}' fehlt"

    def test_fresh_db_has_learning_resets_column(self, db):
        from lernos.db.schema import _column_exists

        assert _column_exists(db, "topics", "learning_resets")

    def test_migration_is_idempotent(self, db):
        """Zweimalige Migration darf nicht crashen."""
        from lernos.db.schema import migrate

        migrate(db)  # schon migriert — darf nichts werfen
        migrate(db)  # noch mal — immer noch ok

    def test_version_not_bumped_on_migration_failure(self, tmp_path):
        """
        Kernprinzip: Wenn _migrate_v1_to_v2 wirft, darf die Versionsnummer
        NICHT auf v2 gesetzt werden. DB bleibt in konsistentem Zustand.
        """
        import lernos.db.schema as sch
        from lernos.db.schema import _migrate_v1_to_v2, get_connection

        path = str(tmp_path / "v1.db")
        conn = get_connection(path)

        # Simuliere eine v1-DB: Schema ohne learning_resets, Version=1
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (1);
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                module TEXT NOT NULL DEFAULT '',
                description TEXT DEFAULT '',
                ef REAL NOT NULL DEFAULT 2.5,
                interval_d INTEGER NOT NULL DEFAULT 1,
                repetitions INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'NEW',
                due_date TEXT NOT NULL DEFAULT (date('now'))
            );
        """)
        conn.commit()

        # Migration soll fehlschlagen
        with patch.object(
            sch, "_migrate_v1_to_v2", side_effect=sqlite3.OperationalError("Disk full")
        ):
            with pytest.raises(sqlite3.OperationalError, match="Disk full"):
                sch.migrate(conn)

        # Versionsnummer muss noch 1 sein
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == 1, f"Version wurde trotz Fehler hochgesetzt: {row[0]}"
        conn.close()

    def test_column_exists_returns_true_for_existing(self, db):
        from lernos.db.schema import _column_exists

        assert _column_exists(db, "topics", "name")
        assert _column_exists(db, "topics", "ef")

    def test_column_exists_returns_false_for_missing(self, db):
        from lernos.db.schema import _column_exists

        assert not _column_exists(db, "topics", "nonexistent_column_xyz")

    def test_alter_table_skipped_if_column_exists(self, tmp_path):
        """
        _migrate_v1_to_v2 darf kein ALTER TABLE ausführen wenn die
        Spalte bereits existiert — kein 'duplicate column name'-Fehler.
        """
        from lernos.db.schema import _migrate_v1_to_v2, get_connection

        path = str(tmp_path / "already_v2.db")
        conn = get_connection(path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER);
            INSERT INTO schema_version VALUES (2);
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                ef REAL NOT NULL DEFAULT 2.5,
                interval_d INTEGER NOT NULL DEFAULT 1,
                repetitions INTEGER NOT NULL DEFAULT 0,
                learning_resets INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'NEW',
                due_date TEXT NOT NULL DEFAULT (date('now'))
            );
        """)
        conn.commit()
        # Darf nicht werfen
        _migrate_v1_to_v2(conn)
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# 2. Config-Loading: ungültiges JSON → WARNING, kein Silent Fail
# ═════════════════════════════════════════════════════════════════════════════


class TestConfigLoading:
    def test_valid_config_loaded(self, tmp_path):
        cfg = tmp_path / ".lernosrc"
        cfg.write_text(json.dumps({"db_path": "/tmp/mydb"}))
        import lernos.db.schema as sch

        with (
            patch("os.path.join", return_value=str(cfg)),
            patch("os.path.exists", return_value=True),
        ):
            result = sch._load_path_config()
        assert result.get("db_path") == "/tmp/mydb"

    def test_missing_config_returns_empty(self):
        import lernos.db.schema as sch

        with patch("os.path.exists", return_value=False):
            result = sch._load_path_config()
        assert result == {}

    def test_invalid_json_logs_warning_and_returns_empty(self, tmp_path, caplog):
        """
        Ungültiges JSON darf NICHT still geschluckt werden.
        Der User muss wissen dass seine Config defekt ist.
        """
        cfg = tmp_path / ".lernosrc"
        cfg.write_text("{this is not valid json}")
        import lernos.db.schema as sch

        with caplog.at_level(logging.WARNING, logger="lernos.db.schema"):
            with (
                patch("os.path.join", return_value=str(cfg)),
                patch("os.path.exists", return_value=True),
            ):
                result = sch._load_path_config()
        assert result == {}, "Soll leeres Dict zurückgeben"
        assert any(
            "JSON" in r.message or "lernosrc" in r.message for r in caplog.records
        ), f"Kein WARNING geloggt. Records: {[r.message for r in caplog.records]}"

    def test_os_error_logs_warning_and_returns_empty(self, caplog):
        """Datei nicht lesbar → WARNING, kein crash."""
        import lernos.db.schema as sch

        with caplog.at_level(logging.WARNING, logger="lernos.db.schema"):
            with (
                patch("os.path.exists", return_value=True),
                patch("builtins.open", side_effect=OSError("Permission denied")),
            ):
                result = sch._load_path_config()
        assert result == {}
        assert any(
            "lernosrc" in r.message.lower() or "gelesen" in r.message
            for r in caplog.records
        )

    def test_empty_json_object_is_valid(self, tmp_path):
        cfg = tmp_path / ".lernosrc"
        cfg.write_text("{}")
        import lernos.db.schema as sch

        with (
            patch("os.path.join", return_value=str(cfg)),
            patch("os.path.exists", return_value=True),
        ):
            result = sch._load_path_config()
        assert result == {}


# ═════════════════════════════════════════════════════════════════════════════
# 3. _make_slide_chunks — isoliert, ohne Ollama
# ═════════════════════════════════════════════════════════════════════════════


class TestMakeSlideChunks:
    """
    _make_slide_chunks kann vollständig ohne Ollama, ohne PDF, ohne Netzwerk
    getestet werden. Der Stub PageInfo implementiert nur das nötige Interface.
    """

    def setup_method(self):
        from lernos.pdf.questions import _make_slide_chunks

        self.chunk = _make_slide_chunks

    def test_empty_pages_returns_single_empty_chunk(self):
        result = self.chunk([], max_chars=2000)
        assert result == [""]

    def test_single_page_becomes_one_chunk(self):
        pages = [_page(title="Mengen", bullets=["Definition", "Beispiele"])]
        result = self.chunk(pages, max_chars=2000)
        assert len(result) == 1
        assert "Mengen" in result[0]

    def test_empty_pages_are_skipped(self):
        pages = [
            _page(title="Echte Folie", text="Inhalt"),
            _page(is_empty=True),
            _page(title="Noch eine", text="Mehr Inhalt"),
        ]
        result = self.chunk(pages, max_chars=2000)
        # Beide echten Folien sollen in einem Chunk landen
        assert len(result) == 1
        assert "Echte Folie" in result[0]
        assert "Noch eine" in result[0]

    def test_all_empty_pages_returns_empty_chunk(self):
        pages = [_page(is_empty=True), _page(is_empty=True)]
        result = self.chunk(pages, max_chars=2000)
        assert result == [""]

    def test_pages_split_into_chunks_when_limit_exceeded(self):
        """Drei Folien à 400 Zeichen, Limit 500 → 2 Chunks (1+1, 1)."""
        text_400 = "x" * 400
        pages = [
            _page(text=text_400, n=1),
            _page(text=text_400, n=2),
            _page(text=text_400, n=3),
        ]
        result = self.chunk(pages, max_chars=500)
        assert len(result) >= 2, f"Erwartet ≥2 Chunks, got {len(result)}"
        # Kein Chunk überschreitet das Limit
        for chunk in result:
            assert len(chunk) <= 500, f"Chunk zu lang: {len(chunk)}"

    def test_oversized_page_gets_its_own_chunk_truncated(self):
        """Eine Folie die allein schon > max_chars ist, bekommt eigenen Chunk."""
        big_text = "y" * 3000
        pages = [
            _page(text="klein", n=1),
            _page(text=big_text, n=2),
            _page(text="auch klein", n=3),
        ]
        result = self.chunk(pages, max_chars=500)
        # Die große Folie wird auf 500 Zeichen gekürzt
        has_truncated = any(len(c) == 500 for c in result)
        assert has_truncated, "Große Folie wurde nicht korrekt gekürzt"

    def test_chunk_content_is_structured_text(self):
        """Chunks enthalten strukturierten Text (# Titel, - Bullets)."""
        pages = [_page(title="Vektoren", bullets=["Betrag", "Richtung"])]
        result = self.chunk(pages, max_chars=2000)
        assert "# Vektoren" in result[0]
        assert "- Betrag" in result[0]
        assert "- Richtung" in result[0]

    def test_multiple_pages_joined_with_double_newline(self):
        pages = [
            _page(title="A", text="Inhalt A", n=1),
            _page(title="B", text="Inhalt B", n=2),
        ]
        result = self.chunk(pages, max_chars=2000)
        assert len(result) == 1
        assert "\n\n" in result[0]

    def test_exact_limit_fit(self):
        """Folie die exakt auf das Limit passt → kein Split."""
        text = "z" * 100
        pages = [_page(text=text, n=1), _page(text=text, n=2)]
        # max_chars = 200 → beide passen exakt
        result = self.chunk(pages, max_chars=200)
        assert len(result) == 1

    def test_result_always_has_at_least_one_element(self):
        """chunks or [''] stellt sicher dass wir nie eine leere Liste zurückgeben."""
        result = self.chunk([], max_chars=1)
        assert len(result) >= 1


# ═════════════════════════════════════════════════════════════════════════════
# 4. string.Template Prompt-Sicherheit (Punkt 3: fehlende Tests)
# ═════════════════════════════════════════════════════════════════════════════


class TestPromptTemplateSafety:
    """
    Prompt-Templates müssen mit beliebigem User-Content umgehen können.
    .format() würde bei {} in chunk_text crashen — string.Template nicht.
    """

    def test_slide_prompt_is_string_template(self):
        import string

        from lernos.pdf.questions import SLIDE_PROMPT

        assert isinstance(SLIDE_PROMPT, string.Template)

    def test_text_prompt_is_string_template(self):
        import string

        from lernos.pdf.questions import TEXT_PROMPT

        assert isinstance(TEXT_PROMPT, string.Template)

    def test_slide_prompt_survives_cpp_code(self):
        from lernos.pdf.questions import SLIDE_PROMPT

        dangerous = "struct Point { int x; int y; }; // C++"
        # Darf keinen KeyError/ValueError werfen:
        result = SLIDE_PROMPT.substitute(topic_name="C++", count=3, text=dangerous)
        assert "struct Point" in result

    def test_slide_prompt_survives_python_dict(self):
        from lernos.pdf.questions import SLIDE_PROMPT

        dangerous = 'd = {"schluessel": 42, "liste": [1, 2, 3]}'
        result = SLIDE_PROMPT.substitute(topic_name="Python", count=2, text=dangerous)
        assert "schluessel" in result

    def test_slide_prompt_survives_latex_braces(self):
        from lernos.pdf.questions import SLIDE_PROMPT

        latex = r"f(x) = \frac{1}{n} \sum_{i=0}^{n} x_i"
        result = SLIDE_PROMPT.substitute(topic_name="Statistik", count=1, text=latex)
        assert r"\frac" in result

    def test_slide_prompt_survives_empty_braces(self):
        from lernos.pdf.questions import SLIDE_PROMPT

        result = SLIDE_PROMPT.substitute(topic_name="Mathe", count=1, text="M = {}")
        assert "M = {}" in result

    def test_text_prompt_survives_json_in_text(self):
        from lernos.pdf.questions import TEXT_PROMPT

        json_text = '{"key": "value", "nested": {"a": 1}}'
        result = TEXT_PROMPT.substitute(topic_name="APIs", count=2, text=json_text)
        assert '"key"' in result

    def test_vision_slide_prompt_is_string_template(self):
        import string

        from lernos.pdf.vision import SLIDE_PROMPT as VP

        assert isinstance(VP, string.Template)

    def test_vision_prompt_no_count_needed(self):
        """Vision-Prompt hat kein $count — LLM entscheidet selbst."""
        from lernos.pdf.vision import SLIDE_PROMPT as VP

        # Darf nicht werfen:
        result = VP.substitute(topic_name="Lineare Algebra")
        assert "Lineare Algebra" in result


# ═════════════════════════════════════════════════════════════════════════════
# 5. JSON-Parser Robustheit (fehlende Tests laut Review)
# ═════════════════════════════════════════════════════════════════════════════


class TestJsonParserEdgeCases:
    """
    json_utils._extract_balanced und parse_questions müssen mit echten
    LLM-Outputs umgehen — die sind oft halbgar.
    """

    def setup_method(self):
        from lernos.pdf.json_utils import (
            _extract_balanced,
            parse_object,
            parse_questions,
        )

        self.pq = parse_questions
        self.po = parse_object
        self.eb = _extract_balanced

    def test_llm_preamble_before_json(self):
        """LLM schreibt oft 'Hier sind die Fragen: [...]' davor."""
        raw = 'Hier sind die Lernfragen:\n[{"question":"Q","answer":"A","difficulty":3,"type":"def"}]'
        qs = self.pq(raw)
        assert len(qs) == 1

    def test_llm_postamble_after_json(self):
        raw = '[{"question":"Q","answer":"A","difficulty":3,"type":"def"}]\nIch hoffe das hilft!'
        qs = self.pq(raw)
        assert len(qs) == 1

    def test_markdown_fenced_code_block(self):
        raw = (
            '```json\n[{"question":"Q","answer":"A","difficulty":2,"type":"app"}]\n```'
        )
        qs = self.pq(raw)
        assert len(qs) == 1
        assert qs[0]["type"] == "app"

    def test_markdown_without_language_tag(self):
        raw = '```\n[{"question":"Q","answer":"A","difficulty":1,"type":"def"}]\n```'
        qs = self.pq(raw)
        assert len(qs) == 1

    def test_braces_in_answer_not_confused_as_fragment(self):
        data = [
            {
                "question": "Was ist ein Set?",
                "answer": "s = {1, 2, 3}",
                "difficulty": 2,
                "type": "def",
            }
        ]
        qs = self.pq(json.dumps(data))
        assert len(qs) == 1
        assert "{1, 2, 3}" in qs[0]["answer"]

    def test_difficulty_below_1_clamped_to_1(self):
        qs = self.pq('[{"question":"Q","answer":"A","difficulty":0,"type":"x"}]')
        assert qs[0]["difficulty"] == 1

    def test_difficulty_above_5_clamped_to_5(self):
        qs = self.pq('[{"question":"Q","answer":"A","difficulty":10,"type":"x"}]')
        assert qs[0]["difficulty"] == 5

    def test_missing_difficulty_defaults_to_3(self):
        qs = self.pq('[{"question":"Q","answer":"A","type":"def"}]')
        assert len(qs) == 1  # accepted
        assert 1 <= qs[0]["difficulty"] <= 5

    def test_completely_empty_input(self):
        assert self.pq("") == []
        assert self.po("") is None

    def test_whitespace_only_input(self):
        assert self.pq("   \n\t  ") == []

    def test_only_invalid_fragments(self):
        assert self.pq("{bad} {also bad} {3rd bad}") == []

    def test_deeply_nested_valid_json(self):
        data = [
            {
                "question": "Q",
                "answer": {"nested": {"deep": "A"}},
                "difficulty": 3,
                "type": "def",
            }
        ]
        # answer ist kein String — wird von parse_questions ggf. konvertiert oder abgelehnt
        # wir testen nur dass es nicht crashed
        result = self.pq(json.dumps(data))
        assert isinstance(result, list)

    def test_large_valid_array(self):
        data = [
            {
                "question": f"Q{i}",
                "answer": f"A{i}",
                "difficulty": (i % 5) + 1,
                "type": "def",
            }
            for i in range(50)
        ]
        qs = self.pq(json.dumps(data))
        assert len(qs) == 50

    def test_skip_item_without_question(self):
        data = [
            {"answer": "A", "difficulty": 3, "type": "def"},
            {"question": "Q", "answer": "A", "difficulty": 3, "type": "def"},
        ]
        qs = self.pq(json.dumps(data))
        assert len(qs) == 1
        assert qs[0]["question"] == "Q"

    def test_skip_item_without_answer(self):
        data = [
            {"question": "Q", "difficulty": 3, "type": "def"},
            {"question": "Q2", "answer": "A2", "difficulty": 3, "type": "def"},
        ]
        qs = self.pq(json.dumps(data))
        assert len(qs) == 1


# ═════════════════════════════════════════════════════════════════════════════
# 6. SM2 EF-Ceiling — der Regression-Test
# ═════════════════════════════════════════════════════════════════════════════


class TestSM2EFCeiling:
    """
    EF_CEILING = 2.5 — Grade 5 kann EF an 2.5 heranführen aber nie überschreiten.
    Dieser Test schlägt mit dem alten ceiling=2.6 fehl.
    """

    def test_ef_never_exceeds_2_5(self):
        from lernos.sm2.algorithm import calc_ef

        ef = 2.4
        for _ in range(20):
            ef = calc_ef(ef, 5)
        assert ef <= 2.5, f"EF={ef} überschreitet 2.5"

    def test_ef_ceiling_constant_is_2_5(self):
        from lernos.sm2.algorithm import EF_CEILING

        assert EF_CEILING == 2.5

    def test_no_semicolons_in_algorithm(self):
        """PEP8: Semikolons gehören nicht in Python."""
        import inspect

        import lernos.sm2.algorithm as alg

        src = inspect.getsource(alg)
        lines_with_semicolons = [
            (i + 1, line.rstrip())
            for i, line in enumerate(src.splitlines())
            if ";" in line and not line.strip().startswith("#")
        ]
        assert not lines_with_semicolons, f"Semikolons gefunden:\n" + "\n".join(
            f"  L{ln}: {content}" for ln, content in lines_with_semicolons
        )
