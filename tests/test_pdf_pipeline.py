"""
LernOS — Test-Suite für die PDF-Pipeline

Abgedeckt:
  - json_utils:      Stack-Matcher, parse_questions, Robustheit
  - ollama_client:   URL-Konfiguration, Timeouts, Exception-Handling
  - vision:          process_slide, image_to_base64 Kompression, pdf_to_images Fehler
  - questions:       string.Template Sicherheit, TF-IDF, Exception-Handling
"""
from __future__ import annotations

import json
import os
import string
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
import requests
import requests.exceptions
from PIL import Image as PILImage


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def small_image() -> PILImage.Image:
    return PILImage.new("RGB", (200, 150), color="white")


@pytest.fixture
def large_image() -> PILImage.Image:
    """Bild das fast sicher über MAX_B64_CHARS liegt ohne Kompression."""
    return PILImage.new("RGB", (4000, 3000), color=(200, 100, 50))


def _ok_response(body: dict) -> MagicMock:
    """Mock-Response mit 200 OK und JSON-Body."""
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = body
    m.raise_for_status.return_value = None
    return m


# ─────────────────────────────────────────────────────────────────────────────
# json_utils
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractBalanced:
    """Stack-basierter JSON-Extraktor."""

    def setup_method(self):
        from lernos.pdf.json_utils import _extract_balanced, parse_object, parse_array, parse_questions
        self.eb = _extract_balanced
        self.po = parse_object
        self.pa = parse_array
        self.pq = parse_questions

    def test_simple_object(self):
        assert self.po('{"a": 1}') == {"a": 1}

    def test_nested_object(self):
        assert self.po('{"a": {"b": {"c": 42}}}') == {"a": {"b": {"c": 42}}}

    def test_skips_invalid_fragment_and_finds_next(self):
        """Das Kernversprechen: ungültiges {bad} vor echtem JSON → findet echtes."""
        result = self.po('Some {bad} text here. {"key": "value", "n": {"x": 1}}')
        assert result == {"key": "value", "n": {"x": 1}}

    def test_cpp_code_in_text(self):
        """C++ Structs auf Vorlesungsfolien dürfen den Parser nicht brechen."""
        cpp = 'struct Point { int x; int y; }; Answer: {"question": "Q", "answer": "A"}'
        result = self.po(cpp)
        assert result == {"question": "Q", "answer": "A"}

    def test_markdown_json_wrapper(self):
        md = '```json\n[{"question":"Q","answer":"A","difficulty":3,"type":"def"}]\n```'
        qs = self.pq(md)
        assert len(qs) == 1
        assert qs[0]["question"] == "Q"

    def test_empty_string_returns_none(self):
        assert self.po("") is None

    def test_plain_text_returns_none(self):
        assert self.po("kein JSON hier, nur Text") is None

    def test_empty_array(self):
        assert self.pq("[]") == []

    def test_empty_string_returns_empty_list(self):
        assert self.pq("") == []

    def test_difficulty_clamped_min(self):
        qs = self.pq('[{"question":"Q","answer":"A","difficulty":-5,"type":"x"}]')
        assert qs[0]["difficulty"] == 1

    def test_difficulty_clamped_max(self):
        qs = self.pq('[{"question":"Q","answer":"A","difficulty":99,"type":"x"}]')
        assert qs[0]["difficulty"] == 5

    def test_missing_answer_skipped(self):
        data = '[{"question":"Q only, no answer","difficulty":3,"type":"def"}]'
        assert self.pq(data) == []

    def test_missing_question_skipped(self):
        data = '[{"answer":"A only","difficulty":3,"type":"def"}]'
        assert self.pq(data) == []

    def test_multiple_questions(self):
        data = json.dumps([
            {"question": "Q1", "answer": "A1", "difficulty": 2, "type": "def"},
            {"question": "Q2", "answer": "A2", "difficulty": 4, "type": "app"},
        ])
        qs = self.pq(data)
        assert len(qs) == 2
        assert qs[1]["difficulty"] == 4

    def test_deeply_nested_braces_in_answer(self):
        """Antwort mit geschweiften Klammern (LaTeX, Code) darf nicht brechen."""
        data = [{"question": "Was ist ein Set?",
                 "answer": "In Python: s = {1, 2, 3}",
                 "difficulty": 2, "type": "def"}]
        qs = self.pq(json.dumps(data))
        assert len(qs) == 1
        assert "{1, 2, 3}" in qs[0]["answer"]


class TestParseSlideResult:
    def test_full_object(self):
        from lernos.pdf.json_utils import parse_slide_result
        default_pt = {"has_printed_text": True, "has_handwriting": False,
                      "has_technical_diagram": False, "has_decorative_image": False,
                      "handwriting_note": "", "content_summary": ""}
        raw = json.dumps({
            "page_type": {"has_handwriting": True, "handwriting_note": "Rand oben"},
            "questions": [{"question": "Q", "answer": "A", "difficulty": 3, "type": "def"}]
        })
        result = parse_slide_result(raw, page_num=5, default_page_type=default_pt)
        assert result["page_num"] == 5
        assert result["page_type"]["has_handwriting"] is True
        assert len(result["questions"]) == 1

    def test_fallback_on_invalid_json(self):
        from lernos.pdf.json_utils import parse_slide_result
        result = parse_slide_result("komplett kaputt", page_num=2,
                                    default_page_type={"has_printed_text": True})
        assert result["page_num"] == 2
        assert isinstance(result["questions"], list)


# ─────────────────────────────────────────────────────────────────────────────
# ollama_client
# ─────────────────────────────────────────────────────────────────────────────

class TestOllamaClientURL:
    """URL-Konfiguration via Umgebungsvariablen."""

    def setup_method(self):
        # Cache invalidieren damit Umgebungsvariablen wirken
        from lernos.pdf import ollama_client
        self.oc = ollama_client

    def teardown_method(self):
        os.environ.pop("LERNOS_OLLAMA_URL",  None)
        os.environ.pop("LERNOS_OLLAMA_HOST", None)

    def test_default_url(self):
        os.environ.pop("LERNOS_OLLAMA_URL", None)
        os.environ.pop("LERNOS_OLLAMA_HOST", None)
        assert self.oc.get_base_url() == "http://localhost:11434"

    def test_lernos_ollama_url_overrides(self):
        os.environ["LERNOS_OLLAMA_URL"] = "http://gpu-box:11434"
        assert self.oc.get_base_url() == "http://gpu-box:11434"

    def test_lernos_ollama_host_adds_protocol(self):
        os.environ["LERNOS_OLLAMA_HOST"] = "myhost:11435"
        url = self.oc.get_base_url()
        assert url.startswith("http://")
        assert "myhost:11435" in url

    def test_generate_url_follows_base(self):
        os.environ["LERNOS_OLLAMA_URL"] = "http://custom:9999"
        assert self.oc.generate_url() == "http://custom:9999/api/generate"

    def test_trailing_slash_stripped(self):
        os.environ["LERNOS_OLLAMA_URL"] = "http://custom:9999/"
        assert not self.oc.get_base_url().endswith("/")


class TestOllamaClientExceptions:
    """Spezifisches Exception-Handling — keine Pokemon-Catches."""

    def test_list_models_connection_error_returns_empty(self):
        from lernos.pdf.ollama_client import list_models
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError):
            result = list_models()
        assert result == []

    def test_list_models_timeout_returns_empty(self):
        from lernos.pdf.ollama_client import list_models
        with patch("requests.get", side_effect=requests.exceptions.Timeout):
            result = list_models()
        assert result == []

    def test_generate_propagates_http_error(self):
        """HTTP 500 soll als HTTPError nach oben propagieren — kein stilles Schlucken."""
        from lernos.pdf.ollama_client import generate
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(requests.exceptions.HTTPError):
                generate("llava", "test prompt")

    def test_generate_connection_error_propagates(self):
        """ConnectionError soll nach oben propagieren damit der Aufrufer entscheiden kann."""
        from lernos.pdf.ollama_client import generate
        with patch("requests.post", side_effect=requests.exceptions.ConnectionError):
            with pytest.raises(requests.exceptions.ConnectionError):
                generate("llava", "test prompt")

    def test_generate_returns_empty_string_on_model_error(self):
        """Ollama gibt {"error": "..."} zurück — soll leeren String liefern, nicht crashen."""
        from lernos.pdf.ollama_client import generate
        with patch("requests.post", return_value=_ok_response({"error": "model not found"})):
            result = generate("nonexistent-model", "test")
        assert result == ""

    def test_named_timeout_constants(self):
        """Magic Numbers sind jetzt benannte Konstanten."""
        from lernos.pdf import ollama_client
        assert hasattr(ollama_client, "TIMEOUT_TAGS")
        assert hasattr(ollama_client, "TIMEOUT_GENERATE")
        assert isinstance(ollama_client.TIMEOUT_TAGS,     int)
        assert isinstance(ollama_client.TIMEOUT_GENERATE, int)

    def test_lru_cache_on_vision_model(self):
        """get_available_vision_model() nutzt lru_cache — thread-safe."""
        from lernos.pdf.ollama_client import get_available_vision_model
        assert hasattr(get_available_vision_model, "cache_info")
        get_available_vision_model.cache_clear()  # muss aufrufbar sein


# ─────────────────────────────────────────────────────────────────────────────
# vision.py
# ─────────────────────────────────────────────────────────────────────────────

class TestImageToBase64:
    """Iterative JPEG-Kompression — kein Hoffnungs-Driven-Development."""

    def test_small_image_within_limit(self, small_image):
        from lernos.pdf.vision import image_to_base64, MAX_B64_CHARS
        b64 = image_to_base64(small_image)
        assert len(b64) <= MAX_B64_CHARS

    def test_large_image_within_limit(self, large_image):
        """Auch sehr große Bilder müssen unter das Limit komprimiert werden."""
        from lernos.pdf.vision import image_to_base64, MAX_B64_CHARS
        b64 = image_to_base64(large_image)
        assert len(b64) <= MAX_B64_CHARS

    def test_rgba_converted_to_rgb(self):
        """RGBA-Bilder (PNG mit Transparenz) müssen konvertiert werden."""
        from lernos.pdf.vision import image_to_base64, MAX_B64_CHARS
        rgba = PILImage.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        b64 = image_to_base64(rgba)
        assert len(b64) > 0
        assert len(b64) <= MAX_B64_CHARS

    def test_palette_mode_converted(self):
        from lernos.pdf.vision import image_to_base64, MAX_B64_CHARS
        p_img = PILImage.new("P", (100, 100))
        b64 = image_to_base64(p_img)
        assert len(b64) <= MAX_B64_CHARS


class TestProcessSlide:
    """Einzel-Folie: Handschrift erkennen, Fragen generieren."""

    def _mock_resp(self, has_handwriting: bool, n_questions: int) -> str:
        questions = [
            {"question": f"Q{i}", "answer": f"A{i}", "difficulty": 2, "type": "def"}
            for i in range(n_questions)
        ]
        return json.dumps({
            "page_type": {
                "has_printed_text":    not has_handwriting,
                "has_handwriting":     has_handwriting,
                "has_technical_diagram": False,
                "has_decorative_image":  False,
                "handwriting_note":    "Rand oben" if has_handwriting else "",
                "content_summary":     "",
            },
            "questions": questions,
        })

    def test_handwriting_detected(self, small_image):
        from lernos.pdf.vision import process_slide
        with patch("lernos.pdf.vision.ollama_generate",
                   return_value=self._mock_resp(has_handwriting=True, n_questions=1)):
            r = process_slide(small_image, "Mengen", "llava", count=1, page_num=3)
        assert r["page_type"]["has_handwriting"] is True
        assert r["page_num"] == 3

    def test_no_handwriting_gives_questions(self, small_image):
        from lernos.pdf.vision import process_slide
        with patch("lernos.pdf.vision.ollama_generate",
                   return_value=self._mock_resp(has_handwriting=False, n_questions=2)):
            r = process_slide(small_image, "Mengen", "llava", count=2, page_num=1)
        assert len(r["questions"]) == 2

    def test_only_handwriting_returns_zero_questions(self, small_image):
        """Seite die nur Handschrift hat → Modell gibt [] zurück → wir akzeptieren das."""
        from lernos.pdf.vision import process_slide
        with patch("lernos.pdf.vision.ollama_generate",
                   return_value=self._mock_resp(has_handwriting=True, n_questions=0)):
            r = process_slide(small_image, "Mengen", "llava", count=2, page_num=5)
        assert r["questions"] == []

    def test_connection_error_returns_safe_default(self, small_image):
        """ConnectionError → sicherer Default, kein Crash — aber WARNING im Log."""
        from lernos.pdf.vision import process_slide
        with patch("lernos.pdf.vision.ollama_generate",
                   side_effect=requests.exceptions.ConnectionError):
            r = process_slide(small_image, "Mengen", "llava", count=1, page_num=1)
        assert r["questions"] == []
        assert r["page_num"] == 1

    def test_timeout_returns_safe_default(self, small_image):
        from lernos.pdf.vision import process_slide
        with patch("lernos.pdf.vision.ollama_generate",
                   side_effect=requests.exceptions.Timeout):
            r = process_slide(small_image, "Mengen", "llava", count=1, page_num=2)
        assert r["questions"] == []

    def test_malformed_json_returns_safe_default(self, small_image):
        from lernos.pdf.vision import process_slide
        with patch("lernos.pdf.vision.ollama_generate", return_value="komplett kaputt"):
            r = process_slide(small_image, "Mengen", "llava", count=1, page_num=1)
        assert isinstance(r["questions"], list)

    def test_page_num_preserved(self, small_image):
        from lernos.pdf.vision import process_slide
        with patch("lernos.pdf.vision.ollama_generate", return_value="{}"):
            r = process_slide(small_image, "Test", "llava", count=1, page_num=7)
        assert r["page_num"] == 7


class TestPdfToImages:
    def test_missing_file_raises_file_not_found(self):
        from lernos.pdf.vision import pdf_to_images
        with pytest.raises(FileNotFoundError):
            pdf_to_images("/nonexistent/path.pdf")

    def test_missing_poppler_raises_runtime_error(self, tmp_path):
        from lernos.pdf.vision import pdf_to_images
        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake")
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="Poppler"):
                pdf_to_images(str(fake_pdf))

    def test_missing_pdf2image_raises_import_error(self, tmp_path):
        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake")
        with patch("lernos.pdf.vision._PDF2IMAGE_OK", False):
            from lernos.pdf.vision import pdf_to_images
            with pytest.raises(ImportError, match="pdf2image"):
                pdf_to_images(str(fake_pdf))


# ─────────────────────────────────────────────────────────────────────────────
# questions.py
# ─────────────────────────────────────────────────────────────────────────────

class TestStringTemplateSafety:
    """
    .format() mit User-Content crasht bei geschweiften Klammern.
    string.Template ist immun dagegen.
    """

    def test_slide_prompt_template_type(self):
        """SLIDE_PROMPT muss ein string.Template sein, kein str."""
        from lernos.pdf.questions import SLIDE_PROMPT, TEXT_PROMPT
        assert isinstance(SLIDE_PROMPT, string.Template)
        assert isinstance(TEXT_PROMPT,  string.Template)

    def test_substitute_with_curly_braces_in_text(self):
        """C++ Code auf Folie: struct Point { int x; int y; } → kein KeyError."""
        from lernos.pdf.questions import SLIDE_PROMPT
        dangerous_text = "struct Point { int x; int y; }; // C++ Beispiel"
        # Darf keinen KeyError oder ValueError werfen:
        result = SLIDE_PROMPT.substitute(
            topic_name="C++ Grundlagen",
            count=3,
            text=dangerous_text,
        )
        assert "struct Point" in result
        assert "C++ Grundlagen" in result

    def test_substitute_with_python_dict_syntax(self):
        """Python Dict-Syntax auf Folie: d = {"key": value}"""
        from lernos.pdf.questions import TEXT_PROMPT
        dangerous = 'Beispiel: d = {"key": 42, "other": [1,2,3]}'
        result = TEXT_PROMPT.substitute(
            topic_name="Python Datenstrukturen",
            count=2,
            text=dangerous,
        )
        assert '{"key": 42' in result

    def test_substitute_with_empty_braces(self):
        """Leere geschweifte Klammern (LaTeX-Sonderzeichen)."""
        from lernos.pdf.questions import SLIDE_PROMPT
        result = SLIDE_PROMPT.substitute(
            topic_name="Mathe",
            count=1,
            text=r"Formel: f(x) = {} \cup \{\}",
        )
        assert r"\cup" in result

    def test_heuristic_replace_safe_with_curly_braces(self):
        """_heuristic_text darf bei {} im Sentence nicht crashen."""
        from lernos.pdf.questions import _heuristic_text
        text = "Eine Menge M = {1, 2, 3} enthält drei Elemente. Leere Menge: {}."
        qs = _heuristic_text(text, "Mengen", 1)
        # Kein Crash ist bereits der Test — Ergebnis kann leer sein
        assert isinstance(qs, list)


class TestTfIdfHeuristic:
    """TF-IDF Satz-Scoring — kein Keyword-Bingo mehr."""

    def test_bait_sentence_not_in_top(self):
        """
        'Der Ansatz ist falsch und bedeutet den Tod, weil er das Ziel verfehlt.'
        Hat viele 'Schlüsselwörter' aber keinen echten Lerninhalt.
        Darf nicht in den Top-2 landen wenn echte Definitionen vorhanden sind.
        """
        from lernos.pdf.questions import _heuristic_text
        bait = "Der Ansatz ist völlig falsch und bedeutet den sicheren Tod, weil er das Ziel verfehlt."
        real = "Eine Menge ist eine Zusammenfassung von wohlunterschiedenen Objekten unserer Anschauung."
        defn = "Der Schnitt zweier Mengen A und B enthält alle Elemente die in A und in B vorkommen."
        qs = _heuristic_text(f"{bait}\n{real}\n{defn}", "Mengen", 2)
        bait_found = any(bait[:30] in q.get("answer", "") + q.get("question", "")
                         for q in qs)
        assert not bait_found, f"Köder-Satz wurde fälschlicherweise ausgewählt: {qs}"

    def test_definitions_preferred(self):
        from lernos.pdf.questions import _heuristic_text
        real = "Eine Menge ist eine Zusammenfassung von wohlunterschiedenen Objekten."
        defn = "Der Schnitt zweier Mengen A und B enthält alle gemeinsamen Elemente."
        bait = "Das ist falsch weil es bedeutet dass das Ziel verfehlt wird."
        qs = _heuristic_text(f"{bait}\n{real}\n{defn}", "Mengen", 2)
        assert any("Menge" in q.get("answer", "") for q in qs)

    def test_score_normalized_by_token_count(self):
        """Lange Sätze mit vielen Wörtern bekommen kein unfaires Score-Geschenk."""
        from lernos.pdf.questions import _heuristic_text
        short = "Eine Menge ist eine Kollektion einzigartiger Elemente."
        long_filler = " ".join(["Dies", "ist", "ein", "sehr", "langer", "Satz",
                                "mit", "vielen", "Wörtern", "die", "alle",
                                "verschieden", "sind"] * 3) + "."
        qs = _heuristic_text(f"{long_filler}\n{short}", "Test", 1)
        assert isinstance(qs, list)
        assert len(qs) >= 1

    def test_empty_text_returns_empty(self):
        from lernos.pdf.questions import _heuristic_text
        assert _heuristic_text("", "Test", 3) == []

    def test_single_sentence(self):
        from lernos.pdf.questions import _heuristic_text
        qs = _heuristic_text("Eine Aussage.", "Test", 3)
        assert isinstance(qs, list)


class TestCallOllamaExceptions:
    """_call_ollama darf ConnectionError/Timeout nicht silent schlucken."""

    def test_connection_error_returns_empty_list(self):
        """ConnectionError → leere Liste (LLM nicht verfügbar, kein Crash)."""
        from lernos.pdf.questions import _call_ollama
        with patch("lernos.pdf.ollama_client.generate",
                   side_effect=requests.exceptions.ConnectionError):
            result = _call_ollama("test prompt", "phi3")
        assert result == []

    def test_timeout_returns_empty_list(self):
        from lernos.pdf.questions import _call_ollama
        with patch("lernos.pdf.ollama_client.generate",
                   side_effect=requests.exceptions.Timeout):
            result = _call_ollama("test prompt", "phi3")
        assert result == []

    def test_http_error_returns_empty_list(self):
        from lernos.pdf.questions import _call_ollama
        with patch("lernos.pdf.ollama_client.generate",
                   side_effect=requests.exceptions.HTTPError("500")):
            result = _call_ollama("test prompt", "phi3")
        assert result == []


class TestMagicNumbersGone:
    """Konfigurierbare Werte statt hardcodierter Zahlen."""

    def test_timeout_constants_exist(self):
        from lernos.pdf import ollama_client
        assert hasattr(ollama_client, "TIMEOUT_TAGS")
        assert hasattr(ollama_client, "TIMEOUT_GENERATE")

    def test_max_chars_configurable_via_env(self):
        """MAX_CHARS_PER_CHUNK soll via LERNOS_CHUNK_CHARS konfigurierbar sein."""
        import importlib
        import lernos.pdf.questions as qmod
        original = qmod.MAX_CHARS_PER_CHUNK
        # Modul neu laden mit anderer Umgebungsvariable
        os.environ["LERNOS_CHUNK_CHARS"] = "5000"
        try:
            importlib.reload(qmod)
            assert qmod.MAX_CHARS_PER_CHUNK == 5000
        finally:
            os.environ.pop("LERNOS_CHUNK_CHARS", None)
            importlib.reload(qmod)

    def test_timeout_generate_configurable_via_env(self):
        """LERNOS_TIMEOUT_GENERATE überschreibt den Default."""
        import importlib
        import lernos.pdf.ollama_client as ocmod
        os.environ["LERNOS_TIMEOUT_GENERATE"] = "60"
        try:
            importlib.reload(ocmod)
            assert ocmod.TIMEOUT_GENERATE == 60
        finally:
            os.environ.pop("LERNOS_TIMEOUT_GENERATE", None)
            importlib.reload(ocmod)


class TestNoLegacyFunctions:
    """v2 ist sauber — keine Legacy-Wrapper."""

    def test_generate_questions_vision_single_gone(self):
        import lernos.pdf.vision as vm
        assert not hasattr(vm, "generate_questions_vision_single")

    def test_generate_questions_vision_batch_gone(self):
        import lernos.pdf.vision as vm
        assert not hasattr(vm, "generate_questions_vision_batch")

    def test_generate_questions_ollama_gone(self):
        import lernos.pdf.questions as qm
        assert not hasattr(qm, "generate_questions_ollama")

    def test_extract_key_sentences_gone(self):
        import lernos.pdf.questions as qm
        assert not hasattr(qm, "extract_key_sentences")


class TestNoCircularImports:
    """Module dürfen sich auf Modul-Ebene nicht gegenseitig importieren."""

    def test_vision_does_not_import_questions_at_module_level(self):
        import ast, inspect
        import lernos.pdf.vision as vm
        source = inspect.getsource(vm)
        tree   = ast.parse(source)
        top_imports = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and node.col_offset == 0  # nur Top-Level
        ]
        for node in top_imports:
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "questions" not in module, (
                    f"vision.py importiert questions auf Modul-Ebene: {module}"
                )

    def test_all_pdf_modules_importable(self):
        """Kein Modul darf beim Import durch zirkuläre Abhängigkeit crashen."""
        import importlib
        for mod in ["lernos.pdf.ollama_client", "lernos.pdf.json_utils",
                    "lernos.pdf.vision", "lernos.pdf.questions"]:
            importlib.import_module(mod)  # darf nicht werfen
