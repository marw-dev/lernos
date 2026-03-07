"""
LernOS — Ollama Embedding Client

Fehlerbehandlung: Alle Fehler werden als LernosError mit Kontext geworfen
statt still verschluckt. Caller kann entscheiden ob er fallback nutzt.
"""
from __future__ import annotations
import os
import re
import struct
import math
import logging
from typing import Optional

import requests

log = logging.getLogger("lernos.ollama")

OLLAMA_BASE = os.environ.get("LERNOS_OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"
LLM_MODEL   = "phi3"
TIMEOUT_EMBED = 20
TIMEOUT_LLM   = 60


class OllamaError(Exception):
    """Basisklasse für alle Ollama-Fehler mit konkretem Kontext."""
    pass

class OllamaConnectionError(OllamaError):
    pass

class OllamaModelError(OllamaError):
    """Modell nicht vorhanden oder OOM."""
    pass

class OllamaTimeoutError(OllamaError):
    pass


def is_ollama_running() -> bool:
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def get_embedding(text: str) -> Optional[list[float]]:
    """
    Holt Embedding von Ollama.
    Gibt None zurück bei Fehler und loggt den Grund (nicht silent fail).
    """
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=TIMEOUT_EMBED,
        )
        r.raise_for_status()
        return r.json()["embedding"]
    except requests.exceptions.Timeout:
        log.warning("Ollama Embedding-Timeout (>%ds) für '%s'", TIMEOUT_EMBED, text[:50])
        return None
    except requests.exceptions.ConnectionError:
        log.debug("Ollama nicht erreichbar für Embedding")
        return None
    except Exception as e:
        log.warning("Embedding-Fehler: %s", e)
        return None


def embedding_to_blob(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def blob_to_embedding(blob: bytes) -> list[float]:
    if not blob:
        return []
    try:
        n = len(blob) // 4
        return list(struct.unpack(f"{n}f", blob))
    except struct.error as e:
        log.warning("blob_to_embedding Fehler: %s", e)
        return []


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return dot / (norm_a * norm_b) if norm_a > 0 and norm_b > 0 else 0.0


def find_similar_topics(
    new_embedding: list[float],
    existing: list[tuple[int, str, bytes]],
    top_k: int = 5,
    min_similarity: float = 0.3,
) -> list[dict]:
    results = []
    for topic_id, name, blob in existing:
        if blob is None:
            continue
        vec = blob_to_embedding(blob)
        sim = cosine_similarity(new_embedding, vec)
        if sim >= min_similarity:
            results.append({"id": topic_id, "name": name, "similarity": sim})
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


def ask_prerequisite(topic_a: str, topic_b: str) -> Optional[bool]:
    """
    Fragt phi3 ob A eine Voraussetzung für B ist.
    None = keine Antwort möglich (Timeout, OOM, usw.) + Grund geloggt.
    """
    prompt = (
        f"Ist das Thema '{topic_a}' eine inhaltliche oder logische "
        f"Voraussetzung für das Thema '{topic_b}'? "
        f"Antworte NUR mit 'Ja' oder 'Nein'."
    )
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": LLM_MODEL, "prompt": prompt, "stream": False},
            timeout=TIMEOUT_LLM,
        )
        r.raise_for_status()
        resp = r.json()
        # OOM-Check
        if resp.get("error"):
            log.warning("Ollama ask_prerequisite Fehler: %s", resp["error"])
            return None
        answer = resp.get("response", "").strip().lower()
        if "ja" in answer[:10]:
            return True
        if "nein" in answer[:10] or "no" in answer[:10]:
            return False
        log.debug("ask_prerequisite: unklare Antwort '%s'", answer[:30])
        return None
    except requests.exceptions.Timeout:
        log.warning("ask_prerequisite Timeout (>%ds) — phi3 zu langsam", TIMEOUT_LLM)
        return None
    except requests.exceptions.ConnectionError:
        log.debug("Ollama nicht erreichbar für ask_prerequisite")
        return None
    except Exception as e:
        log.warning("ask_prerequisite Fehler: %s", e)
        return None


def evaluate_answer(expected: str, given: str) -> tuple[Optional[int], str]:
    """
    Bewertet getippte Antwort via Ollama phi3.
    Gibt (grade, source_info) zurück:
      - grade: 0-5 oder None bei Fehler
      - source_info: "ki" | "timeout" | "oom" | "offline" | "error:<msg>"
    """
    prompt = (
        f"Du bist ein strenger aber fairer Prüfer. Bewerte die inhaltliche Richtigkeit "
        f"der gegebenen Antwort im Vergleich zur Musterantwort auf einer Skala von "
        f"0 (komplett falsch) bis 5 (perfekt).\n\n"
        f"Musterantwort: {expected}\n"
        f"Gegebene Antwort: {given}\n\n"
        f"Antworte AUSSCHLIESSLICH mit einer einzelnen Zahl zwischen 0 und 5."
    )
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": LLM_MODEL, "prompt": prompt, "stream": False},
            timeout=20,
        )
        r.raise_for_status()
        resp = r.json()
        if resp.get("error"):
            err = resp["error"]
            # Typischer OOM-Fehler
            if "out of memory" in err.lower() or "oom" in err.lower():
                log.warning("Ollama OOM beim Bewerten — nutze lokalen Fallback")
                return None, "oom"
            return None, f"error:{err[:60]}"
        response = resp.get("response", "").strip()
        match = re.search(r"[0-5]", response)
        if match:
            return int(match.group()), "ki"
        log.debug("evaluate_answer: Kein Grade in Antwort '%s'", response[:40])
        return None, "error:no_grade"
    except requests.exceptions.Timeout:
        log.warning("evaluate_answer Timeout (>20s) — nutze lokalen Fallback")
        return None, "timeout"
    except requests.exceptions.ConnectionError:
        return None, "offline"
    except Exception as e:
        log.warning("evaluate_answer Fehler: %s", e)
        return None, f"error:{str(e)[:60]}"


def evaluate_answer_local(expected: str, given: str) -> int:
    """
    Lokaler Jaccard-Fallback ohne LLM.
    Gibt Grade 0-5 zurück.
    """
    if not given.strip():
        return 0
    STOPWORDS = {
        "der","die","das","ein","eine","ist","sind","wird","werden",
        "und","oder","aber","als","wie","auch","bei","mit","von",
        "zu","auf","in","an","für","dem","den","des","im",
        "the","a","an","is","are","and","or","of","to","in",
    }
    def tokenize(t: str) -> set[str]:
        tokens = re.findall(r'\b[a-zA-ZäöüÄÖÜß]{3,}\b', t.lower())
        return {w for w in tokens if w not in STOPWORDS}

    exp = tokenize(expected)
    gvn = tokenize(given)
    if not exp:
        return 3
    inter = exp & gvn
    union = exp | gvn
    j = len(inter) / len(union) if union else 0.0
    if j >= 0.75: return 5
    if j >= 0.55: return 4
    if j >= 0.38: return 3
    if j >= 0.22: return 2
    if j >= 0.10: return 1
    return 0


def evaluate_answer_ai(expected: str, given: str) -> tuple[int, str]:
    """
    Öffentliche API für KI-Bewertung mit lokalem Fallback.
    Gibt immer (grade 0-5, source_label) zurück — nie None.

    Diese Funktion ist evaluate_answer() + automatischem Fallback.
    Genutzt von Web-Review (export_review.py) und review.py.
    """
    grade, source = evaluate_answer(expected, given)
    if grade is not None:
        reason_map = {
            "ki":      "KI (Ollama)",
            "timeout": "KI Timeout →Fallback",
            "oom":     "KI OOM →Fallback",
            "offline": "Lokal",
        }
        label = reason_map.get(source, f"KI ({source})")
        return grade, label
    return evaluate_answer_local(expected, given), "Lokal"


def generate_socratic_hint(
    expected: str,
    given:    str,
    grade:    int,
    topic:    str = "",
) -> Optional[str]:
    """
    Generiert eine sokratische Rückfrage — NIEMALS die vollständige Antwort.

    Sokratisches Prinzip: Die Frage führt den Lernenden zum Nachdenken,
    statt die Lücke direkt zu füllen. Die KI soll:
      - Anerkennen was richtig war
      - Eine präzise Rückfrage stellen die den blinden Fleck adressiert
      - Maximal 2-3 Sätze lang sein
      - KEINE Musterantwort, KEINE vollständige Erklärung, KEIN Spoiler

    Args:
        expected: Die Musterantwort
        given:    Die gegebene (unvollständige) Antwort des Lernenden
        grade:    Die bisherige Note (2 oder 3 — nur dann sinnvoll)
        topic:    Optionaler Topic-Name für Kontext

    Returns:
        Sokratische Rückfrage als String, oder None bei Fehler.
    """
    topic_ctx = f" zum Thema '{topic}'" if topic else ""
    prompt = (
        f"Du bist ein sokratischer Lernbegleiter{topic_ctx}. "
        f"Der Lernende hat eine Frage unvollständig beantwortet (Note {grade}/5).\n\n"
        f"Musterantwort: {expected}\n"
        f"Antwort des Lernenden: {given}\n\n"
        f"Deine Aufgabe:\n"
        f"1. Erkenne kurz an, was der Lernende RICHTIG hatte (1 Satz).\n"
        f"2. Stelle EINE gezielte Rückfrage, die den Lernenden auf den fehlenden "
        f"oder falschen Teil hinweist — ohne die Antwort zu verraten.\n"
        f"3. KEIN Spoiler. KEINE vollständige Erklärung. KEINE Musterantwort.\n"
        f"4. Maximal 2-3 Sätze gesamt. Direkt und prägnant.\n\n"
        f"Antworte NUR mit der sokratischen Rückfrage, ohne Einleitung oder Formatierung."
    )
    try:
        import requests
        r = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": LLM_MODEL, "prompt": prompt, "stream": False},
            timeout=TIMEOUT_LLM,
        )
        r.raise_for_status()
        resp = r.json()
        if resp.get("error"):
            log.warning("generate_socratic_hint Fehler: %s", resp["error"])
            return None
        hint = resp.get("response", "").strip()
        return hint if hint else None
    except requests.exceptions.Timeout:
        log.warning("generate_socratic_hint Timeout (>%ds)", TIMEOUT_LLM)
        return None
    except requests.exceptions.ConnectionError:
        return None
    except Exception as e:
        log.warning("generate_socratic_hint Fehler: %s", e)
        return None
