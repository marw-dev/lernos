"""
LernOS — PDF Reader v2 — Präsentations-bewusste Extraktion

Kernverbesserungen gegenüber v1:

1. Seitenstruktur erhalten:
   - Extrahiert jede Seite separat als PageInfo-Objekt
   - PDFInfo.pages: List[PageInfo] statt flachem full_text-String
   - Rückwärtskompatibel: full_text = alle Seiten joined (für existierenden Code)

2. Layout-basierte Extraktion (pdfplumber layout=True):
   - Rekonstruiert optische Reihenfolge statt interner PDF-Reihenfolge
   - Erkennt eingerückte Bulletpoints durch Whitespace-Erhalt
   - Deutlich besser bei mehrspaltigem Layout

3. Präsentations-Erkennung:
   - Heuristik: < 100 Zeichen/Seite + > 2 Seiten → is_presentation = True
   - Folientitel-Extraktion: erste nicht-leere Zeile pro Seite als title
   - Bulletpoints aus eingerückten Zeilen rekonstruieren

4. Folientext-Normalisierung:
   - Bulletpoint-Normalisierung: verschiedene Unicode-Bullets → "- "
   - Einrückung erhalten für Hierarchie-Information
   - Seitenzahlen und Logos (kurze Standalone-Zeilen) gefiltert
"""
from __future__ import annotations
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("lernos.pdf")


# ─────────────────────────────────────────────────────────────────────────────
# Fehlerklassen
# ─────────────────────────────────────────────────────────────────────────────

class PDFError(Exception):
    pass

class PDFPasswordError(PDFError):
    pass

class PDFEmptyError(PDFError):
    pass

class PDFCorruptError(PDFError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Datenstrukturen
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PageInfo:
    """Eine einzelne Seite / Folie aus der PDF."""
    number:      int           # 1-basiert
    title:       str           # Erste nicht-leere Zeile (oft Folientitel)
    text:        str           # Vollständiger Seitentext (bereinigt)
    bullets:     list[str]     # Extrahierte Bulletpoints (normalisiert)
    char_count:  int
    is_empty:    bool          # < 10 Zeichen nach Bereinigung

    @property
    def structured_text(self) -> str:
        """
        Gibt strukturierten Text zurück — Titel + Bullets oder plain text.
        Ideal für LLM-Prompts: kompakter und kontextuell klarer als raw text.
        """
        if not self.title and not self.bullets:
            return self.text
        parts = []
        if self.title:
            parts.append(f"# {self.title}")
        if self.bullets:
            parts.extend(f"- {b}" for b in self.bullets)
        elif self.text:
            # Text ohne erkannte Bullets
            parts.append(self.text)
        return "\n".join(parts)


@dataclass
class PDFInfo:
    filepath:     str
    filename:     str
    page_count:   int
    file_size:    int
    full_text:    str           # Alle Seiten joined (Rückwärtskompatibilität)
    text_excerpt: str
    pages:        list[PageInfo] = field(default_factory=list)
    is_presentation: bool = False   # True wenn Folienpräsentation erkannt
    warnings:     list[str] = field(default_factory=list)

    @property
    def structured_pages(self) -> list[PageInfo]:
        """Gibt nur nicht-leere Seiten zurück."""
        return [p for p in self.pages if not p.is_empty]


# ─────────────────────────────────────────────────────────────────────────────
# Hauptfunktion
# ─────────────────────────────────────────────────────────────────────────────

def extract_pdf(filepath: str, max_chars: int = 50_000) -> PDFInfo:
    """
    Extrahiert Text aus PDF mit Seitenstruktur.

    Gibt PDFInfo zurück mit:
      - pages:   List[PageInfo] — eine Seite pro Folie/Seite
      - full_text: str — alle Seiten joined (Rückwärtskompatibilität)
      - is_presentation: bool — Präsentations-Heuristik

    Wirft PDFError-Unterklassen bei bekannten Fehlern.
    """
    filepath = os.path.abspath(filepath)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Datei nicht gefunden: {filepath}")
    if os.path.getsize(filepath) == 0:
        raise PDFCorruptError(f"Datei ist leer (0 Bytes): {filepath}")

    filename  = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)
    warnings  = []

    # Extraktion mit Seitenstruktur
    pages, page_count, method = _extract_pages_pdfplumber(filepath, max_chars)

    if not pages:
        pages, page_count, method = _extract_pages_pdfminer(filepath, max_chars)

    # Leer-Check
    non_empty = [p for p in pages if not p.is_empty]
    if not non_empty:
        if _is_password_protected(filepath):
            raise PDFPasswordError(
                f"'{filename}' ist passwortgeschützt. "
                "Bitte entsperre die Datei zuerst (z.B. mit qpdf --decrypt)."
            )
        raise PDFEmptyError(
            f"Kein Text aus '{filename}' extrahierbar. "
            "Die PDF ist wahrscheinlich ein gescanntes Bild. "
            "OCR wird nicht unterstützt. Tipp: ocrmypdf für OCR-Vorverarbeitung."
        )

    # Rückwärtskompatibilität: full_text aus Seiten zusammenbauen
    full_text = "\n\n".join(
        p.structured_text for p in non_empty
    )[:max_chars]

    # Präsentations-Erkennung
    avg_chars = sum(p.char_count for p in non_empty) / len(non_empty)
    is_presentation = avg_chars < 300 and len(non_empty) >= 3

    if is_presentation:
        log.debug("Präsentations-Modus erkannt: %.0f Zeichen/Seite", avg_chars)

    # Heuristische Warnungen
    import re as _re
    formula_patterns = [r"\\frac", r"\\sum", r"\\int", r"\$\$", r"\\begin{"]
    for pat in formula_patterns:
        if _re.search(pat, full_text):
            warnings.append(
                "⚠ LaTeX-Formeln erkannt — mathematische Notation kann beschädigt sein."
            )
            break

    if avg_chars < 80 and len(non_empty) > 2:
        warnings.append(
            f"⚠ Wenig Text ({avg_chars:.0f} Zeichen/Seite) — "
            "bildlastige Präsentation. Fragen könnten unvollständig sein."
        )

    if is_presentation:
        warnings.append(
            f"📊 Präsentation erkannt ({len(non_empty)} Folien) — "
            "nutze folienweise Verarbeitung für bessere Fragen."
        )

    excerpt = full_text[:500].strip() + ("…" if len(full_text) > 500 else "")

    return PDFInfo(
        filepath=filepath, filename=filename,
        page_count=page_count, file_size=file_size,
        full_text=full_text, text_excerpt=excerpt,
        pages=pages, is_presentation=is_presentation,
        warnings=warnings,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Extraktion — pdfplumber mit layout=True
# ─────────────────────────────────────────────────────────────────────────────

def _extract_pages_pdfplumber(filepath: str, max_chars: int) -> tuple[list[PageInfo], int, str]:
    """
    Extrahiert Seiten mit pdfplumber layout=True.
    layout=True: pdfplumber rekonstruiert die optische Lesereihenfolge
    statt der internen PDF-Objektreihenfolge. Behält Einrückungen bei.
    """
    try:
        import pdfplumber
        pages    = []
        total    = 0

        with pdfplumber.open(filepath) as pdf:
            n = len(pdf.pages)
            for i, page in enumerate(pdf.pages, 1):
                # layout=True: optische Reihenfolge, Einrückungen erhalten
                try:
                    raw = page.extract_text(layout=True) or ""
                except Exception:
                    # Fallback ohne layout falls Parameter nicht unterstützt
                    raw = page.extract_text() or ""

                page_info = _build_page_info(i, raw)
                pages.append(page_info)
                total += page_info.char_count

                if total >= max_chars:
                    log.debug("max_chars Limit bei Seite %d/%d erreicht", i, n)
                    break

        return pages, n, "pdfplumber"

    except Exception as e:
        err = str(e).lower()
        if "password" in err or "encrypted" in err:
            raise PDFPasswordError(
                f"PDF ist passwortgeschützt: {os.path.basename(filepath)}"
            ) from e
        log.debug("pdfplumber fehlgeschlagen: %s", e)
        return [], 0, ""


def _extract_pages_pdfminer(filepath: str, max_chars: int) -> tuple[list[PageInfo], int, str]:
    """Fallback-Extraktion via pdfminer (ohne layout-Verbesserung)."""
    try:
        from pdfminer.high_level import extract_pages as pm_extract_pages
        from pdfminer.layout import LTTextBox, LTTextLine, LTAnon
        from pdfminer.high_level import extract_text

        pages  = []
        total  = 0

        page_list = list(pm_extract_pages(filepath))
        n = len(page_list)

        for i, layout in enumerate(page_list, 1):
            # Textboxen der Reihe nach (y-Koordinate absteigend = oben→unten)
            boxes = sorted(
                [el for el in layout if isinstance(el, LTTextBox)],
                key=lambda b: -b.y1,
            )
            lines = []
            for box in boxes:
                for obj in box:
                    if hasattr(obj, 'get_text'):
                        t = obj.get_text().strip()
                        if t:
                            lines.append(t)

            raw = "\n".join(lines)
            page_info = _build_page_info(i, raw)
            pages.append(page_info)
            total += page_info.char_count
            if total >= max_chars:
                break

        return pages, n, "pdfminer"

    except Exception as e:
        err = str(e).lower()
        if "password" in err or "encrypt" in err:
            raise PDFPasswordError(
                f"PDF ist passwortgeschützt: {os.path.basename(filepath)}"
            ) from e
        log.debug("pdfminer fehlgeschlagen: %s", e)
        return [], 0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Seiten-Verarbeitung
# ─────────────────────────────────────────────────────────────────────────────

# Unicode Bullet-Zeichen die normalisiert werden
_BULLET_RE = re.compile(
    r"^[\s]*"                          # führende Leerzeichen
    r"(?:[•·▪▸►▶❯◦‣⁃∙○●■□►–—\-\*])"  # Bullet-Zeichen
    r"\s+",                            # Trennleerzeichen
    re.MULTILINE,
)

# Einrückung als Bullet-Indikator (>= 3 Leerzeichen am Zeilenbegin)
_INDENT_RE = re.compile(r"^( {3,}|\t+)(\S.*)$", re.MULTILINE)

# Seitenzahl-Pattern: Standalone-Zahl oder "N / M" Format
_PAGENUM_RE = re.compile(
    r"^\s*\d+\s*(?:[/|]\s*\d+)?\s*$",
    re.MULTILINE,
)


def _build_page_info(number: int, raw: str) -> PageInfo:
    """
    Verarbeitet rohen Seitentext zu einer strukturierten PageInfo.

    Schritte:
    1. Bereinigung (Seitenzahlen, überflüssige Whitespace)
    2. Zeilen in Titel und Bullets klassifizieren
    3. Bulletpoints normalisieren
    """
    if not raw.strip():
        return PageInfo(
            number=number, title="", text="",
            bullets=[], char_count=0, is_empty=True,
        )

    # 1. Seitenzahlen entfernen
    text = _PAGENUM_RE.sub("", raw)
    text = _clean_text(text)

    if not text.strip():
        return PageInfo(
            number=number, title="", text="",
            bullets=[], char_count=0, is_empty=True,
        )

    lines = text.split("\n")
    lines = [l.rstrip() for l in lines if l.strip()]

    # 2. Titel: erste nicht-leere Zeile (oft die Folienbeschriftung)
    title = lines[0] if lines else ""
    # Wenn Titel extrem lang → wahrscheinlich kein Titel sondern Fließtext
    if len(title) > 120:
        title = ""

    # 3. Bulletpoints extrahieren
    bullets = _extract_bullets(lines[1:] if title else lines)

    # 4. Wenn keine Bullets erkannt aber Einrückungen vorhanden:
    #    Eingerückte Zeilen als Bullets behandeln
    if not bullets:
        bullets = _extract_indented(lines[1:] if title else lines)

    # Normalisierter plain text (Bullets als "-" Zeilen)
    plain = title + ("\n" + "\n".join(f"- {b}" for b in bullets) if bullets else
                     "\n" + "\n".join(lines[1:] if title else lines))

    return PageInfo(
        number=number,
        title=title,
        text=plain.strip(),
        bullets=bullets,
        char_count=len(plain),
        is_empty=False,
    )


def _extract_bullets(lines: list[str]) -> list[str]:
    """Erkennt explizite Bullet-Zeichen und normalisiert sie."""
    bullets = []
    for line in lines:
        # Explizite Bulletpoints
        if _BULLET_RE.match(line):
            cleaned = _BULLET_RE.sub("", line).strip()
            if cleaned and len(cleaned) > 3:
                bullets.append(cleaned)
    return bullets


def _extract_indented(lines: list[str]) -> list[str]:
    """
    Behandelt eingerückte Zeilen als Bulletpoints.
    Erkennt Folienstruktur: kurze Zeilen die nicht wie Fließtext aussehen.
    """
    # Heuristik: wenn mehr als 50% der Zeilen kürzer als 80 Zeichen sind
    # und keine langen Fließtextsätze vorhanden — Folienpräsentation
    short = sum(1 for l in lines if len(l) < 80)
    if not lines or short / len(lines) < 0.6:
        return []

    bullets = []
    for line in lines:
        s = line.strip()
        if s and len(s) > 4 and not s.endswith(":"):
            # Sieht aus wie ein Stichpunkt (keine Überschrift)
            bullets.append(s)
    return bullets


def _clean_text(text: str) -> str:
    import re as _re
    text = _re.sub(r"[ \t]+", " ", text)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    # Seitenzahlen "12 / 42" entfernen
    text = _re.sub(r'\b\d+\s*/\s*\d+\b', '', text)
    # Standalone Zahlen am Zeilenanfang (Folien-Nummerierung)
    text = _re.sub(r'^\s*\d+\s*$', '', text, flags=_re.MULTILINE)
    return text.strip()


def _is_password_protected(filepath: str) -> bool:
    try:
        with open(filepath, "rb") as f:
            header = f.read(8192)
        return b"/Encrypt" in header
    except Exception:
        return False
