"""
LernOS — Fragen-Generator v2

Architektur:
  - Ollama-URL/HTTP:  lernos.pdf.ollama_client (konfigurierbar via LERNOS_OLLAMA_URL)
  - JSON-Parsing:    lernos.pdf.json_utils   (Stack-basierter Brace-Matcher)
  - Vision-Check:    ollama_client.vision_available() mit @lru_cache
  - Kein globaler State, kein zirkulaerer Modul-Import
  - Heuristik:       TF-IDF Satz-Scoring (kein Keyword-Bingo)

Kernverbesserungen gegenüber v1:

1. Folientreue Verarbeitung:
   - generate_questions() akzeptiert Optional[List[PageInfo]] statt nur str
   - Folienweise Verarbeitung: pro Folie ~1-2 Fragen statt 8000-Zeichen-Blob
   - Chunk-Strategie: Folien in semantische Gruppen (max. ~2000 Tokens/Chunk)

2. Präsentations-Fallback (kein Ollama):
   - Bulletpoint-Splitter: erkennt auch "- ", "• ", Zeilenumbrüche als Trenner
   - Titel-basierte Fragen: "Was beschreibt Folie 'Titel'?"
   - Schlüsselwörter für Präsentationen ergänzt

3. Slide-Chunk-Strategie (LLM-Aufruf):
   - MAX_CHARS_PER_CHUNK = 2000 (statt text[:8000])
   - Folien werden gebündelt bis Limit erreicht
   - Pro Chunk: count-Anteil der Gesamtfragen
   - Ergebnis: mehrere kleinere, fokussierte LLM-Aufrufe statt einem großen

4. Slide-aware Prompt:
   - Erkennt ob Input eine Folienpräsentation ist
   - Passt Anweisungen an: "Pro Folie eine Frage" vs. "Fließtext-Analyse"
"""
from __future__ import annotations
import os
import math
import os
import string
import json
import logging
import re
from typing import TYPE_CHECKING, Optional

import requests
import requests.exceptions

from lernos.pdf import ollama_client
from lernos.pdf.ollama_client import generate as ollama_generate

if TYPE_CHECKING:
    from lernos.pdf.reader import PageInfo

log = logging.getLogger("lernos.pdf.questions")

# Kein globaler State mehr — lru_cache macht dasselbe thread-safe
def _vision_available() -> bool:
    """
    True wenn ein Vision-Modell in Ollama verfuegbar ist.
    Delegiert an ollama_client.vision_available() das lru_cache nutzt —
    kein mutierbarer globaler State, thread-safe.
    """
    try:
        from lernos.pdf.ollama_client import vision_available
        return vision_available()
    except Exception:
        return False

# Zeichen-Budget pro LLM-Chunk.
# HINWEIS: Zeichen ≠ Tokens. Tokenizerverhalten variiert je nach Modell
# (Phi-3, Llama-3, Mistral etc. tokenisieren unterschiedlich).
# Exakte Token-Zählung wäre möglich via tiktoken oder HuggingFace tokenizers,
# aber das zieht eine schwere Abhängigkeit nach sich.
# Aktuell: konservativer Richtwert ~4 Zeichen/Token für deutsche Texte.
# Wer das exakt haben will: LERNOS_CHUNK_CHARS als Umgebungsvariable setzen.
MAX_CHARS_PER_CHUNK: int = int(os.environ.get("LERNOS_CHUNK_CHARS", "2000"))


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

# string.Template statt .format() — verhindert KeyError wenn User-Text
# geschweifte Klammern enthält (z.B. C++-Code auf Vorlesungsfolien).
# $topic_name, $count, $text sind Template-Variablen.
# Literal-Dollar: $$ → wird zu $ im Output.
SLIDE_PROMPT = string.Template(
    "Du bist ein Lernassistent. Analysiere diese Präsentationsfolien"
    " zum Thema \"$topic_name\" und generiere genau $count Lernfragen"
    " mit Antworten.\n\n"
    "Jede Folie ist mit \"# Folientitel\" markiert, Stichpunkte mit \"- \".\n\n"
    "Regeln:\n"
    "- Jede Frage soll einem konkreten Folienthema zugeordnet sein\n"
    "- Frage zum Verständnis, nicht nur Fakten abfragen\n"
    "- Kurze, präzise Antworten (1-3 Sätze)\n"
    "- Verschiedene Typen: Definition, Bedeutung, Anwendung, Zusammenhang\n\n"
    "Antworte NUR mit einem JSON-Array:\n"
    "[\n"
    "  {\n"
    "    \"question\": \"...\",\n"
    "    \"answer\": \"...\",\n"
    "    \"difficulty\": 3,\n"
    "    \"type\": \"definition|application|comparison|reasoning\"\n"
    "  }\n"
    "]\n\n"
    "Folien:\n---\n$text\n---"
)

TEXT_PROMPT = string.Template(
    "Du bist ein Lernassistent. Analysiere den folgenden Textauszug"
    " aus dem Thema \"$topic_name\" und generiere $count Lernfragen"
    " mit Antworten.\n\n"
    "Regeln:\n"
    "- Verschiedene Fragetypen: Definition, Anwendung, Vergleich, Begründung\n"
    "- Antworten präzise aber vollständig (2-5 Sätze)\n"
    "- Schwierigkeit: 1=sehr leicht, 3=mittel, 5=sehr schwer\n\n"
    "Antworte NUR mit einem JSON-Array:\n"
    "[\n"
    "  {\n"
    "    \"question\": \"...\",\n"
    "    \"answer\": \"...\",\n"
    "    \"difficulty\": 3,\n"
    "    \"type\": \"definition|application|comparison|reasoning\"\n"
    "  }\n"
    "]\n\n"
    "Text:\n---\n$text\n---"
)


# ─────────────────────────────────────────────────────────────────────────────
# Hauptfunktion — öffentliche API
# ─────────────────────────────────────────────────────────────────────────────

def generate_questions(
    text:       str,
    topic_name: str,
    count:      int = 5,
    model:      str = "phi3",
    pages:      "Optional[list[PageInfo]]" = None,
    is_presentation: bool = False,
    use_vision: bool = False,
    vision_model: "Optional[str]" = None,
    pdf_path:   "Optional[str]" = None,
    vision_dpi: int = 96,
) -> tuple[list[dict], bool]:
    """
    Generiert Lernfragen aus Text oder Folien.

    Fallback-Kaskade (Präsentation erkannt):
      1. Vision-LLM  (llava/llama3.2-vision) — wenn use_vision=True oder auto-detect
      2. Text-LLM    (phi3) — folienweise Chunking
      3. Heuristik   — Bullet-Extraktion

    Fallback-Kaskade (Fließtext):
      1. Text-LLM    (phi3)
      2. Heuristik   — Schlüsselsatz-Extraktion

    Args:
        text:            Vollständiger Text (Rückwärtskompatibilität)
        topic_name:      Name des Topics
        count:           Gewünschte Anzahl Fragen
        model:           Ollama-Text-Modell (z.B. "phi3")
        pages:           Optional List[PageInfo] — seitenweise Struktur
        is_presentation: Hinweis ob Präsentation (aktiviert Slide-Modus)
        use_vision:      Vision-Modell explizit aktivieren
        vision_model:    Spezifisches Vision-Modell (None = auto)
        pdf_path:        Pfad zur Original-PDF (für Vision-Pipeline)
        vision_dpi:      DPI für PDF-Rendering (96 oder 150)

    Returns:
        (fragen, ai_generated)
        ai_generated: True wenn Ollama (Text oder Vision) genutzt wurde
    """
    use_pages  = pages is not None and len(pages) > 0
    slide_mode = use_pages and (is_presentation or _detect_slide_content(pages))

    # ── Pfad 1: Vision-LLM ───────────────────────────────────────────────────
    # Bedingungen: pdf_path vorhanden + (use_vision=True ODER auto-Präsentation)
    if pdf_path and (use_vision or (slide_mode and _vision_available())):
        try:
            # Lazy import — verhindert zirkulaere Abhaengigkeit beim Laden des Moduls.
            # questions.py und vision.py importieren sich nie auf Modul-Ebene.
            from lernos.pdf.vision import generate_questions_from_pdf_vision
            qs, used_model, _page_analyses = generate_questions_from_pdf_vision(
                filepath=pdf_path,
                topic_name=topic_name,
                count=count,
                model=vision_model,
                dpi=vision_dpi,
            )
            if qs:
                log.info("Vision-Pfad: %d Fragen via %s", len(qs), used_model)
                return qs[:count], True
            log.debug("Vision-Pfad lieferte keine Fragen — weiter mit Text-LLM")
        except Exception as e:
            log.warning("Vision-Pfad fehlgeschlagen: %s — weiter mit Text-LLM", e)

    # ── Pfad 2: Text-LLM ─────────────────────────────────────────────────────
    questions = _generate_with_ollama(
        text=text, topic_name=topic_name, count=count, model=model,
        pages=pages if use_pages else None, slide_mode=slide_mode,
    )
    if questions:
        return questions[:count], True

    # ── Pfad 3: Heuristik ────────────────────────────────────────────────────
    questions = _extract_heuristic(
        text=text, topic_name=topic_name, count=count,
        pages=pages if use_pages else None, slide_mode=slide_mode,
    )
    return questions[:count], False


# ─────────────────────────────────────────────────────────────────────────────
# LLM-Verarbeitung
# ─────────────────────────────────────────────────────────────────────────────

def _generate_with_ollama(
    text: str, topic_name: str, count: int, model: str,
    pages: "Optional[list[PageInfo]]", slide_mode: bool,
) -> list[dict]:
    """
    Sendet Chunks an Ollama und sammelt Fragen.

    Strategie:
    - Präsentation: Folien in 2000-Zeichen-Chunks aufteilen, pro Chunk
      anteilige Anzahl Fragen generieren
    - Fließtext: wie bisher, aber begrenzt auf MAX_CHARS_PER_CHUNK * 2
    """
    try:
        from lernos.pdf.ollama_client import list_models
        if not list_models():
            return []
    except requests.exceptions.ConnectionError:
        log.info("Ollama nicht erreichbar — überspringe Text-LLM")
        return []
    except Exception as e:
        log.warning("Ollama-Check fehlgeschlagen: %s", e)
        return []

    all_questions = []

    if slide_mode and pages:
        # Folientreue Chunk-Strategie
        chunks = _make_slide_chunks(pages, MAX_CHARS_PER_CHUNK)
        log.debug("Slide-Mode: %d Chunks für %d Folien", len(chunks), len(pages))

        for chunk_idx, chunk_text in enumerate(chunks):
            # Anteil der Fragen pro Chunk (aufgerundet beim letzten)
            chunk_count = max(1, count // len(chunks))
            if chunk_idx == len(chunks) - 1:
                chunk_count = max(1, count - len(all_questions))

            qs = _call_ollama(
                prompt=SLIDE_PROMPT.substitute(
                    topic_name=topic_name,
                    count=chunk_count,
                    text=chunk_text,
                ),
                model=model,
            )
            all_questions.extend(qs)
            log.debug("Chunk %d: %d Fragen generiert", chunk_idx + 1, len(qs))

    else:
        # Fließtext-Strategie: bereinigter Text, begrenzt
        clean = _select_best_text(text, pages)
        qs = _call_ollama(
            prompt=TEXT_PROMPT.substitute(
                topic_name=topic_name,
                count=count,
                text=clean[:MAX_CHARS_PER_CHUNK * 2],
            ),
            model=model,
        )
        all_questions.extend(qs)

    return all_questions


def _call_ollama(prompt: str, model: str) -> list[dict]:
    """
    Einzelner Ollama Text-LLM-Aufruf.
    URL kommt aus ollama_client (LERNOS_OLLAMA_URL / LERNOS_OLLAMA_HOST / default).
    JSON-Parsing via json_utils (kein find/rfind String-Slicing).
    """
    try:
        from lernos.pdf.ollama_client import generate as ollama_generate
        from lernos.pdf.json_utils import parse_questions
        raw = ollama_generate(model=model, prompt=prompt, timeout=120, format="json")
        return parse_questions(raw)
    except requests.exceptions.ConnectionError:
        log.warning("Ollama nicht erreichbar (%s) — kein Text-LLM verfügbar",
                    ollama_client.generate_url())
        return []
    except requests.exceptions.Timeout:
        log.warning("Ollama-Timeout — Modell zu langsam oder überlastet")
        return []
    except requests.exceptions.RequestException as e:
        log.warning("Ollama HTTP-Fehler: %s", e)
        return []
    except Exception as e:
        # Unerwartetes — auf WARNING, nicht debug, damit es im Log auftaucht
        log.warning("_call_ollama unerwarteter Fehler %s: %s", type(e).__name__, e)
        raise   # unbekannte Fehler weiter nach oben propagieren


def _make_slide_chunks(pages: "list[PageInfo]", max_chars: int) -> list[str]:
    """
    Gruppiert Folien in Text-Chunks die max_chars nicht überschreiten.
    Jeder Chunk enthält den strukturierten Text (Titel + Bullets) der Folien.
    Mindestens 1 Folie pro Chunk.
    """
    chunks      = []
    current     = []
    current_len = 0

    for page in pages:
        if page.is_empty:
            continue
        page_text = page.structured_text
        page_len  = len(page_text)

        # Wenn diese Folie allein schon > max_chars: eigener Chunk
        if page_len > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            # Folie kürzen
            chunks.append(page_text[:max_chars])
            continue

        # Folie passt noch in aktuellen Chunk
        if current_len + page_len <= max_chars:
            current.append(page_text)
            current_len += page_len
        else:
            # Chunk abschließen, neue starten
            chunks.append("\n\n".join(current))
            current = [page_text]
            current_len = page_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [""]


def _select_best_text(text: str, pages: "Optional[list[PageInfo]]") -> str:
    """Wählt den besten Textblock für Fließtext-Extraktion."""
    if pages:
        # Nimm structured_text der besten Seiten (höchste Zeichenzahl)
        sorted_pages = sorted(pages, key=lambda p: p.char_count, reverse=True)
        return "\n\n".join(p.structured_text for p in sorted_pages[:5])
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Heuristischer Fallback
# ─────────────────────────────────────────────────────────────────────────────

def _extract_heuristic(
    text: str, topic_name: str, count: int,
    pages: "Optional[list[PageInfo]]",
    slide_mode: bool,
) -> list[dict]:
    """
    Fallback ohne Ollama.

    Präsentation: Folientitel + Bulletpoints → direkte Fragen
    Fließtext: Schlüsselsatz-Extraktion (verbessert mit Bulletpoint-Splitter)
    """
    if slide_mode and pages:
        return _heuristic_slides(pages, topic_name, count)
    return _heuristic_text(text, topic_name, count)


def _heuristic_slides(
    pages: "list[PageInfo]", topic_name: str, count: int,
) -> list[dict]:
    """
    Für Präsentationen: Folientitel + Bullets → Frage-Antwort-Paare.

    Strategie:
    - Jede Folie mit Titel + ≥1 Bullet → "Was besagt Folie 'Titel'?"
    - Antwort: alle Bullets der Folie
    - Folien ohne Titel aber mit Bullets: generischer Template
    """
    questions = []
    templates_title = [
        'Was sind die Kernpunkte der Folie "{title}" im Thema {topic}?',
        'Erkläre den Inhalt der Folie "{title}".',
        'Was wird auf der Folie "{title}" erläutert?',
        'Welche Aussagen macht die Folie "{title}"?',
        'Fasse die Folie "{title}" in eigenen Worten zusammen.',
    ]
    templates_notitle = [
        'Welche Stichpunkte werden auf Folie {num} zum Thema {topic} genannt?',
        'Was listet Folie {num} auf?',
        'Erkläre die Punkte von Folie {num}.',
    ]

    non_empty = [p for p in pages if not p.is_empty and (p.bullets or p.text)]

    for i, page in enumerate(non_empty):
        if len(questions) >= count:
            break

        if not page.bullets and not page.text:
            continue

        answer = "\n".join(f"• {b}" for b in page.bullets) if page.bullets else page.text
        if not answer.strip():
            continue

        if page.title and len(page.title) > 3:
            tmpl = templates_title[i % len(templates_title)]
            # f-String statt .format() — page.title kann {} enthalten (C-Structs etc.)
            q = tmpl.replace("{title}", str(page.title)).replace("{topic}", str(topic_name)).replace("{num}", str(page.number))
        else:
            tmpl = templates_notitle[i % len(templates_notitle)]
            q = tmpl.replace("{topic}", str(topic_name)).replace("{num}", str(page.number))

        questions.append({
            "question":   q,
            "answer":     answer.strip(),
            "difficulty": 2 + (i % 3),  # Variation: 2, 3, 4
            "type":       "extraction",
        })

    return questions


def _heuristic_text(text: str, topic_name: str, count: int) -> list[dict]:
    """
    Fallback fuer Fliesstext: TF-IDF-artiges Satz-Scoring.

    Ersetzt das alte Keyword-Bingo (PRIORITY_WORDS) durch ein echtes
    statistisches Modell:

    TF  (Term Frequency):  Anteil eines Wortes am Satz — normalisiert
        auf Satzlaenge, damit kurze Stichpunkte nicht bevorteilt werden.
    IDF (Inverse Doc Freq): Woerter die in fast allen Saetzen vorkommen
        (Stoppwoerter) bekommen niedrigen Score — seltene inhaltliche
        Begriffe bekommen hohen Score.

    Ergebnis: "Der Ansatz ist voellig falsch und bedeutet den sicheren Tod,
    weil er das Ziel verfehlt" bekommt KEINEN Bonus mehr nur weil es
    "bedeutet", "weil" und "Ziel" enthaelt.
    """
    # Saetze aufteilen: Satzzeichen + Bullets + Absaetze
    raw_sents = re.split(
        r"(?<=[.!?])\s+"
        r"|(?:\n\s*[•·▪▸►▶❯◦‣⁃∙○●■□\-\*]\s+)"
        r"|(?:\n{2,})",
        text,
    )
    # Zeilenumbrueche innerhalb kurzer Abschnitte als Trenner
    expanded = []
    for s in raw_sents:
        lines = s.split("\n")
        short = [l for l in lines if l.strip() and len(l.strip()) < 100]
        if len(short) >= 2 and len(short) / max(len(lines), 1) > 0.7:
            expanded.extend(lines)
        else:
            expanded.append(s)
    sentences = [s.strip() for s in expanded if len(s.strip()) > 10]

    if not sentences:
        return []

    # Tokenisierung: Kleinbuchstaben, nur Woerter >= 3 Zeichen
    _STOPWORDS = {
        "und", "oder", "der", "die", "das", "ein", "eine", "ist", "sind",
        "hat", "haben", "wird", "werden", "auch", "mit", "von", "fuer",
        "auf", "bei", "aus", "als", "aber", "wenn", "falls", "weil", "da",
        "sich", "nicht", "nur", "noch", "auch", "sehr", "mehr", "hier",
        "this", "that", "the", "and", "for", "with", "are", "was", "has",
    }

    def tokenize(s: str) -> list[str]:
        return [w for w in re.findall(r"[a-zA-ZäöüÄÖÜß]{3,}", s.lower())
                if w not in _STOPWORDS]

    # IDF berechnen: wie selten ist ein Wort ueber alle Saetze hinweg?
    n = len(sentences)
    doc_freq: dict[str, int] = {}
    tok_sents = [tokenize(s) for s in sentences]
    for toks in tok_sents:
        for w in set(toks):
            doc_freq[w] = doc_freq.get(w, 0) + 1

    def idf(word: str) -> float:
        df = doc_freq.get(word, 0)
        if df == 0:
            return 0.0
        return math.log((n + 1) / (df + 1))  # smoothed IDF

    # TF-IDF Score pro Satz: Summe der TF*IDF aller Tokens
    scored = []
    for sent, toks in zip(sentences, tok_sents):
        if not toks:
            scored.append((0.0, sent))
            continue
        tf_counts: dict[str, int] = {}
        for w in toks:
            tf_counts[w] = tf_counts.get(w, 0) + 1
        score = sum(
            (cnt / len(toks)) * idf(w)
            for w, cnt in tf_counts.items()
        )
        # Normalisierung: Score durch Anzahl der Tokens teilen.
        # Verhindert dass lange Saetze mit vielen seltenen Woertern
        # automatisch hoeheren Score bekommen als kurze Definitionen.
        # Bonus fuer praegnante Stichpunkte (15-120 Zeichen): typisch fuer
        # Folientext der informationsdicht ist.
        if toks:
            score /= len(toks)
        if 15 <= len(sent) <= 120:
            score *= 1.3
        scored.append((score, sent))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [s for _, s in scored[:count]]

    templates = [
        "Was bedeutet der folgende Punkt zum Thema '{topic}'?\n\"{sentence}\"",
        "Erklaere in eigenen Worten: \"{sentence}\"",
        "Welcher Zusammenhang wird beschrieben: \"{sentence}\"?",
        "Warum gilt oder trifft zu: \"{sentence}\"?",
        "Nenne ein Beispiel fuer: \"{sentence}\"",
    ]

    return [
        {
            "question":   (templates[i % len(templates)]
                          .replace("{topic}", str(topic_name))
                          .replace("{sentence}", sentence[:200])),
            "answer":     sentence,
            "difficulty": 3,
            "type":       "extraction",
        }
        for i, sentence in enumerate(top)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────

def _detect_slide_content(pages: "list[PageInfo]") -> bool:
    """
    Erkennt ob der Inhalt eine Präsentation ist.
    Kriterien: kurze Seiten + hoher Bullet-Anteil.
    """
    if not pages:
        return False
    non_empty = [p for p in pages if not p.is_empty]
    if len(non_empty) < 2:
        return False
    avg_chars = sum(p.char_count for p in non_empty) / len(non_empty)
    bullet_pages = sum(1 for p in non_empty if p.bullets)
    return avg_chars < 400 or (bullet_pages / len(non_empty) > 0.5)


def _parse_questions_json(raw: str) -> list[dict]:
    """
    Wrapper um json_utils.parse_questions.
    Intern noch von einigen Stellen genutzt — delegiert vollstaendig.
    """
    from lernos.pdf.json_utils import parse_questions
    return parse_questions(raw)


# ── Versionsinformation ──────────────────────────────────────────────────────
# generate_questions_ollama() und extract_key_sentences() wurden in v2 entfernt.
# Direkte Ollama-Aufrufe: lernos.pdf.ollama_client.generate()
# Heuristik direkt:       _heuristic_text() (intern)
