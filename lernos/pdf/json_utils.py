"""
LernOS — JSON-Extraktion aus LLM-Antworten

Zwei Strategien, in Prioritäts-Reihenfolge:

1. Ollama JSON-Mode  (format="json")
   Das Modell gibt garantiert valides JSON zurück — kein Parsing nötig.
   Bevorzugte Strategie wenn das Modell es unterstützt.

2. Regex-Extraktion  (Fallback)
   Sucht nach ```json ... ``` Codeblöcken, dann nach dem äußersten
   { ... } bzw. [ ... ] mit einem Stack-basierten Brace-Matcher.
   Korrekt gegenüber verschachtelten Klammern und eingebettetem Text.

   Der alte Ansatz (raw.find("{") / raw.rfind("}")) ist FALSCH:
   - "{" und "}" können im umgebenden Text vorkommen
   - Ein Code-Beispiel wie "f(x) = {1 wenn x > 0}" zerreißt den Parser
   Stack-Matcher löst das in O(n) ohne Regex-Backtracking.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Union

log = logging.getLogger("lernos.pdf.json_utils")


# ─────────────────────────────────────────────────────────────────────────────
# Stack-basierter Brace-Matcher
# ─────────────────────────────────────────────────────────────────────────────

def _extract_balanced(text: str, open_ch: str, close_ch: str) -> str | None:
    """
    Findet den ersten vollständig balancierten open_ch...close_ch Block
    der als valides JSON parsebar ist.

    Warum "der als valides JSON parsebar ist":
        Ein Text wie 'result is {bad} here. {"key": 1}' enthält zwei
        balancierte Blöcke. Der erste ("{bad}") ist kein valides JSON.
        Wir iterieren weiter und geben den ersten validen zurück.

    Korrekt bei:
        - verschachtelten Klammern
        - Strings mit Klammern darin ("key {with braces}")
        - unbalancierten Blöcken vor dem eigentlichen JSON
        - Markdown-Text mit { im Fließtext

    Algorithmus: Stack-basierter Matcher mit JSON-Validierung als Filter.
    O(n) — kein Regex-Backtracking.
    """
    depth = 0
    start = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue

        if ch == "\\" and in_string:
            escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == open_ch:
            if depth == 0:
                start = i
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i + 1]
                try:
                    json.loads(candidate)
                    return candidate   # erstes valides JSON
                except json.JSONDecodeError:
                    # Dieser Block ist kein valides JSON — weitersuchen
                    start = -1

    return None


def extract_json_object(text: str) -> str | None:
    """Extrahiert das erste balancierte JSON-Objekt { ... } aus text."""
    # Priorität 1: ```json ... ``` Codeblock
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Priorität 2: Stack-basierter Matcher
    return _extract_balanced(text, "{", "}")


def extract_json_array(text: str) -> str | None:
    """Extrahiert das erste balancierte JSON-Array [ ... ] aus text."""
    # Priorität 1: ```json ... ``` Codeblock
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Priorität 2: Stack-basierter Matcher
    return _extract_balanced(text, "[", "]")


# ─────────────────────────────────────────────────────────────────────────────
# Öffentliche Parse-Funktionen
# ─────────────────────────────────────────────────────────────────────────────

def parse_object(raw: str) -> dict | None:
    """
    Parst ein JSON-Objekt aus einer LLM-Antwort.
    Gibt None zurück wenn kein valides Objekt gefunden wird.
    """
    fragment = extract_json_object(raw)
    if not fragment:
        log.debug("parse_object: kein JSON-Objekt gefunden")
        return None
    try:
        result = json.loads(fragment)
        if not isinstance(result, dict):
            return None
        return result
    except json.JSONDecodeError as e:
        log.debug("parse_object JSON-Fehler: %s (fragment: %.60s)", e, fragment)
        return None


def parse_array(raw: str) -> list | None:
    """
    Parst ein JSON-Array aus einer LLM-Antwort.
    Gibt None zurück wenn kein valides Array gefunden wird.
    """
    fragment = extract_json_array(raw)
    if not fragment:
        log.debug("parse_array: kein JSON-Array gefunden")
        return None
    try:
        result = json.loads(fragment)
        if not isinstance(result, list):
            return None
        return result
    except json.JSONDecodeError as e:
        log.debug("parse_array JSON-Fehler: %s (fragment: %.60s)", e, fragment)
        return None


def parse_questions(raw: str) -> list[dict]:
    """
    Parst eine Liste von Frage-Dicts aus einer LLM-Antwort.
    Validiert jedes Item: muss 'question' und 'answer' haben.
    Gibt immer eine Liste zurück (leer bei Fehler).
    """
    data = parse_array(raw)
    if data is None:
        return []

    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question", "")).strip()
        a = str(item.get("answer",   "")).strip()
        if not q or not a:
            continue
        result.append({
            "question":   q,
            "answer":     a,
            "difficulty": max(1, min(5, int(item.get("difficulty", 3)))),
            "type":       str(item.get("type", "general")),
        })
    return result


def parse_slide_result(raw: str, page_num: int, default_page_type: dict) -> dict:
    """
    Parst das kombinierte Folienergebnis:
      { "page_type": {...}, "questions": [...] }

    Gibt immer ein vollständiges Dict zurück.
    Bei Parse-Fehlern: leere questions, default page_type.
    """
    obj = parse_object(raw)

    if obj is None:
        log.debug("Folie %d: kein JSON-Objekt — versuche questions-Array direkt", page_num)
        # Letzter Versuch: vielleicht hat das Modell nur ein Array geliefert
        qs = parse_questions(raw)
        return {"page_type": default_page_type, "questions": qs, "page_num": page_num}

    page_type = obj.get("page_type", {})
    raw_qs    = obj.get("questions", [])

    # questions validieren (können direkt im Objekt sein)
    if isinstance(raw_qs, list):
        valid_qs = []
        for item in raw_qs:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question", "")).strip()
            a = str(item.get("answer",   "")).strip()
            if q and a:
                valid_qs.append({
                    "question":   q,
                    "answer":     a,
                    "difficulty": max(1, min(5, int(item.get("difficulty", 3)))),
                    "type":       str(item.get("type", "general")),
                })
    else:
        valid_qs = []

    return {
        "page_type": {**default_page_type, **page_type},
        "questions": valid_qs,
        "page_num":  page_num,
    }
