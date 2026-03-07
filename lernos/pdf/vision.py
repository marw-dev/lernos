"""
LernOS — Vision-Pipeline v2

Verarbeitet PDF-Seiten als Bilder mit lokalen Vision-LLMs (Ollama).
Erkennt Handschrift und ignoriert sie — nur gedruckter Folientext
und Fachdiagramme fließen in die Lernfragen ein.

Abhängigkeiten:
    pip install pdf2image Pillow
    sudo apt install poppler-utils   # Linux
    brew install poppler             # macOS

Konfiguration (Ollama-URL):
    export LERNOS_OLLAMA_URL=http://mein-server:11434
    Oder: export LERNOS_OLLAMA_HOST=mein-server
    Default: lokaler Ollama-Server (konfigurierbar via lernos.pdf.ollama_client)

Architektur:
    - Ollama-URL/HTTP ausschliesslich via lernos.pdf.ollama_client
    - JSON-Parsing ausschliesslich via lernos.pdf.json_utils
    - KEIN Import von lernos.pdf.questions (kein zirkulaerer Import)
    - Ollama JSON-Mode (format="json") als primaere Strategie
    - Iterative JPEG-Kompression bis unter MAX_B64_CHARS
"""
from __future__ import annotations

import base64
import io
import logging
import os
import shutil
import string
from pathlib import Path
from typing import TYPE_CHECKING, Optional

# Optionale Abhängigkeiten — müssen installiert sein (siehe requirements.txt)
# ImportError wird in pdf_to_images() mit klarer Installationsanleitung geworfen
try:
    from pdf2image import convert_from_path
    from pdf2image.exceptions import PDFInfoNotInstalledError
    _PDF2IMAGE_OK = True
except ImportError:
    _PDF2IMAGE_OK = False

try:
    from PIL import Image as _PILImage  # noqa: F401 — für TYPE_CHECKING
    _PILLOW_OK = True
except ImportError:
    _PILLOW_OK = False

import requests
import requests.exceptions
from lernos.pdf import ollama_client
from lernos.pdf.ollama_client import (
    generate as ollama_generate,
    get_available_vision_model,
    vision_available,
)
from lernos.pdf.json_utils import parse_slide_result

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

log = logging.getLogger("lernos.pdf.vision")

MAX_VISION_PAGES    = 10
DPI_STANDARD        = 96
DPI_QUALITY         = 150
MAX_B64_CHARS       = 500_000
_JPEG_QUALITY_START = 80
_JPEG_QUALITY_MIN   = 20
_JPEG_QUALITY_STEP  = 15

# ─────────────────────────────────────────────────────────────────────────────
# Prompt-Design
#
# Kern-Prinzipien:
#   1. Folie selbst entscheidet wie viele Fragen sie verdient (0-3)
#   2. Titelfolien, Gliederungen, leere Folien → immer 0 Fragen
#   3. Formeln, Beweise, Rechnungen → höchste Priorität (können direkt geprüft werden)
#   4. Fließtext-Definitionen → mittlere Priorität
#   5. Feste per_page-Verteilung ist abgeschafft — der Lernwert der Folie entscheidet
# ─────────────────────────────────────────────────────────────────────────────

SLIDE_PROMPT = string.Template(
    "Du analysierst eine Vorlesungsfolie zum Thema \"$topic_name\".\n\n"

    "SCHRITT 1 — Klassifiziere den Folientyp (wähle genau einen):\n"
    "  title_only:   Nur Titel, Themenüberschrift, Kapiteltrenner — KEIN Lerninhalt\n"
    "  outline:      Gliederung, Inhaltsverzeichnis, Agenda — KEIN Lerninhalt\n"
    "  math_formula: Formeln, Ableitungen, Beweise, Rechnungen, Gleichungen\n"
    "  diagram:      Graphen, Schaltkreise, Flussdiagramme, Zeichnungen mit Beschriftung\n"
    "  content:      Erklärender Text, Definitionen, Bullet-Points mit Lerninhalt\n"
    "  mixed:        Kombination aus Formeln/Diagrammen UND erklärendem Text\n"
    "  handwriting:  Überwiegend handgeschriebene Notizen (ungleichmäßige Liniendicke)\n\n"

    "SCHRITT 2 — Bewerte den Lernwert (0-10):\n"
    "  0:  title_only, outline, handwriting, leere Folie\n"
    "  1-3: Wenig Inhalt, allgemeine Einführung ohne konkrete Fakten\n"
    "  4-6: Solide Definitionen oder einfache Zusammenhänge\n"
    "  7-9: Formeln, Beweise, Diagramme die direkt prüfbar sind\n"
    "  10: Kernsatz des Themas, zentrale Formel, fundamentaler Algorithmus\n\n"

    "SCHRITT 3 — Generiere Lernfragen:\n"
    "  Lernwert 0:   questions: []  (keine Fragen — Titelfolie/Gliederung/leer)\n"
    "  Lernwert 1-3: 0-1 Fragen\n"
    "  Lernwert 4-6: 1-2 Fragen\n"
    "  Lernwert 7-9: 2-3 Fragen\n"
    "  Lernwert 10:  3 Fragen\n\n"

    "PRIORITÄTEN bei Fragetypen (wichtig → weniger wichtig):\n"
    "  1. Formeln berechnen/herleiten: \"Berechne X wenn Y\", \"Leite Z her aus...\"\n"
    "  2. Diagramm interpretieren: \"Was zeigt der Graph wenn...\"\n"
    "  3. Konzept anwenden: \"Warum gilt X?\", \"Welche Bedingung muss für Y erfüllt sein?\"\n"
    "  4. Definieren: \"Was ist X?\" — nur wenn kein Formel-/Anwendungs-Aspekt vorhanden\n\n"

    "Antworte NUR mit diesem JSON:\n"
    "{\n"
    '  "page_type": {\n'
    '    "slide_class": "title_only|outline|math_formula|diagram|content|mixed|handwriting",\n'
    '    "learning_value": 0,\n'
    '    "has_formula": false,\n'
    '    "has_diagram": false,\n'
    '    "has_handwriting": false,\n'
    '    "content_summary": ""\n'
    "  },\n"
    '  "questions": [\n'
    "    {\n"
    '      "question": "...",\n'
    '      "answer": "...",\n'
    '      "difficulty": 3,\n'
    '      "type": "formula|calculation|diagram|application|definition|reasoning"\n'
    "    }\n"
    "  ]\n"
    "}"
)

_DEFAULT_PAGE_TYPE: dict = {
    "slide_class":     "content",
    "learning_value":  5,
    "has_formula":     False,
    "has_diagram":     False,
    "has_handwriting": False,
    "content_summary": "",
    # Legacy-Felder für Abwärtskompatibilität mit _format_slide_status
    "has_printed_text":      True,
    "has_technical_diagram": False,
    "has_decorative_image":  False,
    "handwriting_note":      "",
}


def pdf_to_images(
    filepath:  str,
    dpi:       int = DPI_STANDARD,
    max_pages: int = MAX_VISION_PAGES,
    page_nums: Optional[list[int]] = None,
) -> list["PILImage"]:
    """PDF-Seiten -> PIL-Images. Wirft ImportError/RuntimeError mit Installationsanleitung."""
    if not _PDF2IMAGE_OK:
        raise ImportError(
            "pdf2image fehlt.\n"
            "  pip install pdf2image\n"
            "  sudo apt install poppler-utils   # Linux\n"
            "  brew install poppler             # macOS\n"
            "Oder: lernos setup --vision"
        )

    if not shutil.which("pdftoppm"):
        raise RuntimeError(
            "Poppler (pdftoppm) fehlt.\n"
            "  Linux:   sudo apt install poppler-utils\n"
            "  macOS:   brew install poppler\n"
            "  Windows: https://github.com/oschwartz10612/poppler-windows/releases\n"
            "Oder: lernos setup --vision"
        )

    if not Path(filepath).exists():
        raise FileNotFoundError(f"PDF nicht gefunden: {filepath}")

    try:
        if page_nums:
            images = []
            for pn in page_nums[:max_pages]:
                imgs = convert_from_path(filepath, dpi=dpi, fmt="jpeg",
                                         first_page=pn, last_page=pn)
                images.extend(imgs)
        else:
            images = convert_from_path(filepath, dpi=dpi, fmt="jpeg",
                                        last_page=max_pages)
        log.debug("PDF->Images: %d Seiten @ %d DPI", len(images), dpi)
        return images
    except PDFInfoNotInstalledError:
        raise RuntimeError("Poppler nicht gefunden. sudo apt install poppler-utils")
    except Exception as e:
        raise RuntimeError(f"PDF-zu-Bild Konvertierung fehlgeschlagen: {e}")


def image_to_base64(image: "PILImage") -> str:
    """
    PIL-Image -> Base64-JPEG-String.

    Iterative Kompression: startet bei _JPEG_QUALITY_START und reduziert
    in Schritten bis unter MAX_B64_CHARS oder bis _JPEG_QUALITY_MIN.
    Als letzter Ausweg: Bild auf 50% skalieren.
    Das Limit wird immer eingehalten — keine Hoffnungs-getriebene Entwicklung.
    """
    if image.mode in ("RGBA", "P", "LA"):
        image = image.convert("RGB")

    quality = _JPEG_QUALITY_START
    while quality >= _JPEG_QUALITY_MIN:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        if len(b64) <= MAX_B64_CHARS:
            if quality < _JPEG_QUALITY_START:
                log.debug("Bild komprimiert auf Qualitaet %d (%d Zeichen)", quality, len(b64))
            return b64
        log.debug("Qualitaet %d: %d > %d Zeichen — reduziere", quality, len(b64), MAX_B64_CHARS)
        quality -= _JPEG_QUALITY_STEP

    # Absoluter Notfall: Bild skalieren
    log.warning("MAX_B64_CHARS selbst bei min. Qualitaet ueberschritten — skaliere auf 50%%")
    w, h = image.size
    small = image.resize((w // 2, h // 2))
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=_JPEG_QUALITY_MIN, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def process_slide(
    image:      "PILImage",
    topic_name: str,
    model:      str,
    count:      int = 1,   # Maximal-Hint; LLM entscheidet anhand learning_value
    page_num:   int = 1,
) -> dict:
    """
    Verarbeitet eine Folie in einem einzigen Ollama-Aufruf.

    Das Modell klassifiziert die Folie (title_only / math_formula / content / ...)
    und entscheidet selbst anhand des Lernwerts (0-10) wie viele Fragen sinnvoll
    sind. Titelfolien und Gliederungen geben 0 Fragen zurück.

    Returns: { page_type: {...}, questions: [...], page_num: int }
    """
    _empty = {"page_type": dict(_DEFAULT_PAGE_TYPE), "questions": [], "page_num": page_num}

    b64    = image_to_base64(image)
    prompt = SLIDE_PROMPT.substitute(topic_name=topic_name)

    try:
        raw = ollama_generate(
            model=model, prompt=prompt, images=[b64],
            timeout=120, format="json",
        )
    except requests.exceptions.ConnectionError:
        log.warning("Folie %d: Ollama nicht erreichbar (%s)", page_num, ollama_client.generate_url())
        return {**_empty, "page_num": page_num}
    except requests.exceptions.Timeout:
        log.warning("Folie %d: Ollama-Timeout — Vision-Modell zu langsam?", page_num)
        return {**_empty, "page_num": page_num}
    except requests.exceptions.RequestException as e:
        log.warning("Folie %d: HTTP-Fehler: %s", page_num, e)
        return {**_empty, "page_num": page_num}
    except Exception as e:
        log.warning("Folie %d: unerwarteter Fehler: %s: %s", page_num, type(e).__name__, e)
        return {**_empty, "page_num": page_num}

    if not raw:
        return {**_empty, "page_num": page_num}

    result = parse_slide_result(raw, page_num=page_num,
                                default_page_type=dict(_DEFAULT_PAGE_TYPE))

    # Legacy-Felder aus neuem Schema ableiten (Abwärtskompatibilität)
    pt = result["page_type"]
    pt.setdefault("has_printed_text",
                  pt.get("slide_class") not in ("handwriting", "title_only", "outline"))
    pt.setdefault("has_technical_diagram", pt.get("has_diagram", False))
    pt.setdefault("has_decorative_image",  False)
    pt.setdefault("handwriting_note",
                  "Handschrift erkannt" if pt.get("has_handwriting") else "")

    log.debug(
        "Folie %d: class=%s lv=%d formula=%s -> %d Fragen",
        page_num,
        pt.get("slide_class", "?"),
        pt.get("learning_value", -1),
        pt.get("has_formula"),
        len(result["questions"]),
    )
    return result


def _format_slide_status(result: dict) -> str:
    """Lesbare Status-Zeile für CLI-Ausgabe mit Lernwert-Anzeige."""
    pt, pn, nq = result["page_type"], result["page_num"], len(result["questions"])

    slide_class    = pt.get("slide_class", "content")
    learning_value = pt.get("learning_value", -1)

    CLASS_ICONS = {
        "title_only":   "📋 Titelfolie",
        "outline":      "📑 Gliederung",
        "math_formula": "∑  Formel/Rechnung",
        "diagram":      "📊 Diagramm",
        "content":      "📄 Text",
        "mixed":        "📄+∑  Text+Formel",
        "handwriting":  "✍️  Handschrift",
    }
    label  = CLASS_ICONS.get(slide_class, f"📄 {slide_class}")
    lv_str = f" [LW={learning_value}]" if learning_value >= 0 else ""
    skip   = "  → übersprungen" if slide_class in ("title_only", "outline", "handwriting") else ""
    q_str  = f"  [{nq} Frage{'n' if nq != 1 else ''}]" if nq else "  [keine Fragen]"

    return f"  Folie {pn}: {label}{lv_str}{skip}{q_str}"


def generate_questions_from_pdf_vision(
    filepath:   str,
    topic_name: str,
    count:      int  = 5,
    model:      Optional[str] = None,
    dpi:        int  = DPI_STANDARD,
    max_pages:  int  = MAX_VISION_PAGES,
    verbose:    bool = True,
) -> tuple[list[dict], str, list[dict]]:
    """
    PDF -> Lernfragen via Vision-Modell.

    Dynamische Fragenverteilung statt fester per_page-Aufteilung:
      - Jede Folie bewertet sich selbst (Lernwert 0-10)
      - Titelfolien/Gliederungen: 0 Fragen
      - Formelreiche Folien: bis 3 Fragen
      - Priorisierung: formula > calculation > diagram > application > definition
      - Weiches Limit: Verarbeitung stoppt wenn count*2 Fragen gesammelt wurden

    Returns: (questions, model_used, slide_results)
    """
    if model is None:
        model = get_available_vision_model()
    if model is None:
        log.info("Kein Vision-Modell verfügbar")
        return [], "none", []

    images = pdf_to_images(filepath, dpi=dpi, max_pages=max_pages)
    if not images:
        return [], model, []

    log.info("Vision: %d Folien, Modell=%s, DPI=%d", len(images), model, dpi)

    slide_results: list[dict] = []
    all_questions: list[dict] = []

    for i, image in enumerate(images):
        result = process_slide(image, topic_name, model, page_num=i + 1)
        slide_results.append(result)
        all_questions.extend(result["questions"])

        if verbose:
            log.info(_format_slide_status(result))

        # Weiches Limit: verhindert Endlos-Verarbeitung bei langen Skripten
        if len(all_questions) >= count * 2:
            log.info("  → Weich-Limit erreicht (%d Fragen) — Rest übersprungen",
                     len(all_questions))
            break

    # Statistik
    skipped  = sum(1 for r in slide_results
                   if r["page_type"].get("slide_class") in ("title_only", "outline"))
    hw_count = sum(1 for r in slide_results if r["page_type"].get("has_handwriting"))
    formulas = sum(1 for r in slide_results if r["page_type"].get("has_formula"))

    if skipped:
        log.info("  → %d Titelfolien/Gliederungen übersprungen", skipped)
    if hw_count:
        log.info("  → %d Folien mit Handschrift ignoriert", hw_count)
    if formulas:
        log.info("  → %d Formel-/Rechenfolien (hohe Priorität)", formulas)

    # Priorisierung: Formel-/Diagrammfragen vor Definitionen
    _PRIO = {"formula": 0, "calculation": 1, "diagram": 2,
             "application": 3, "reasoning": 4, "definition": 5}
    all_questions.sort(key=lambda q: _PRIO.get(q.get("type", ""), 6))

    log.info("Vision: %d Fragen für '%s'", min(len(all_questions), count), topic_name)
    return all_questions[:count], model, slide_results


def check_vision_dependencies() -> dict:
    """Prueft alle Vision-Pipeline-Abhaengigkeiten."""
    from lernos.pdf.ollama_client import get_base_url, list_models
    result: dict = {
        "pdf2image": False, "poppler": False, "pillow": False,
        "ollama": False, "vision_model": None,
        "ollama_url": get_base_url(), "errors": [],
    }
    try:
        import pdf2image  # noqa: F401
        result["pdf2image"] = True
    except ImportError:
        result["errors"].append("pdf2image fehlt  ->  pip install pdf2image")
    if shutil.which("pdftoppm"):
        result["poppler"] = True
    else:
        result["errors"].append("Poppler fehlt  ->  sudo apt install poppler-utils")
    try:
        import PIL  # noqa: F401
        result["pillow"] = True
    except ImportError:
        result["errors"].append("Pillow fehlt  ->  pip install Pillow")
    models = list_models()
    if models is not None:
        result["ollama"] = True
        result["vision_model"] = get_available_vision_model()
    else:
        result["errors"].append("Ollama nicht erreichbar  ->  ollama serve")
    return result
