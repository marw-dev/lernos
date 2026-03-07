"""
LernOS — Ollama HTTP-Client

Einzige Quelle für Ollama-Konfiguration und HTTP-Aufrufe in der PDF-Pipeline.

Konfiguration (Priorität hoch → niedrig):
  1. LERNOS_OLLAMA_URL  — vollständige Base-URL, z.B. http://gpu-server:11434
  2. LERNOS_OLLAMA_HOST — nur Host, Port optional, z.B. gpu-server oder gpu-server:11435
  3. Default            — http://localhost:11434

Beispiele:
  export LERNOS_OLLAMA_URL=http://192.168.1.50:11434
  export LERNOS_OLLAMA_HOST=gpu-server:11435
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Optional

import requests
import requests.exceptions

log = logging.getLogger("lernos.pdf.ollama_client")


# ─────────────────────────────────────────────────────────────────────────────
# URL-Auflösung
# ─────────────────────────────────────────────────────────────────────────────

def get_base_url() -> str:
    """
    Gibt die konfigurierte Ollama-Base-URL zurück.
    Liest LERNOS_OLLAMA_URL → LERNOS_OLLAMA_HOST → default.
    Trailing-Slash wird normalisiert.
    """
    if url := os.environ.get("LERNOS_OLLAMA_URL", "").strip():
        return url.rstrip("/")

    if host := os.environ.get("LERNOS_OLLAMA_HOST", "").strip():
        # Host kann "hostname" oder "hostname:port" sein
        if "://" not in host:
            host = f"http://{host}"
        return host.rstrip("/")

    return "http://localhost:11434"


def tags_url()     -> str: return f"{get_base_url()}/api/tags"
def generate_url() -> str: return f"{get_base_url()}/api/generate"
def chat_url()     -> str: return f"{get_base_url()}/api/chat"


# ─────────────────────────────────────────────────────────────────────────────
# Modell-Erkennung
# ─────────────────────────────────────────────────────────────────────────────

KNOWN_VISION_MODELS = [
    "llama3.2-vision",
    "llama3.2-vision:11b",
    "llava-phi3",
    "llava",
    "llava:13b",
    "llava:34b",
    "bakllava",
    "moondream",
]


# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration — alle Magic Numbers an einem Ort
# ─────────────────────────────────────────────────────────────────────────────
TIMEOUT_TAGS     = int(os.environ.get("LERNOS_TIMEOUT_TAGS",     "3"))    # Sekunden für /api/tags
TIMEOUT_GENERATE = int(os.environ.get("LERNOS_TIMEOUT_GENERATE", "120"))  # Sekunden für /api/generate


def list_models(timeout: int = TIMEOUT_TAGS) -> list[str]:
    """Gibt alle installierten Ollama-Modellnamen zurück (oder [] bei Fehler)."""
    try:
        r = requests.get(tags_url(), timeout=timeout)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except requests.exceptions.ConnectionError:
        log.debug("Ollama nicht erreichbar: %s", tags_url())
    except requests.exceptions.Timeout:
        log.debug("Ollama Tags-Anfrage Timeout (%ds)", TIMEOUT_TAGS)
    except requests.exceptions.RequestException as e:
        log.debug("Ollama Tags-Fehler: %s", e)
    return []


def is_ollama_running(timeout: int = 3) -> bool:
    """True wenn Ollama erreichbar ist."""
    return bool(list_models(timeout=timeout) is not None)


@lru_cache(maxsize=1)
def get_available_vision_model() -> Optional[str]:
    """
    Bestes verfügbares Vision-Modell (gecacht via lru_cache).

    lru_cache macht genau das was _VISION_AVAILABLE global tat —
    aber thread-safe, ohne mutierbaren globalen State, und mit
    cache_clear() falls man den Cache invalidieren möchte.
    """
    models     = list_models()
    short_names = {n.split(":")[0] for n in models}
    for candidate in KNOWN_VISION_MODELS:
        if candidate in models or candidate.split(":")[0] in short_names:
            log.debug("Vision-Modell: %s", candidate)
            return candidate
    return None


def vision_available() -> bool:
    """True wenn mindestens ein Vision-Modell installiert ist."""
    return get_available_vision_model() is not None


# ─────────────────────────────────────────────────────────────────────────────
# HTTP-Calls
# ─────────────────────────────────────────────────────────────────────────────

def generate(
    model:   str,
    prompt:  str,
    images:  Optional[list[str]] = None,   # Base64-Strings
    timeout: int = TIMEOUT_GENERATE,
    format:  Optional[str] = None,          # "json" für Ollama JSON-Mode
) -> str:
    """
    Sendet einen /api/generate Request an Ollama.

    Args:
        model:   Modellname
        prompt:  Eingabe-Prompt
        images:  Liste von Base64-JPEG-Strings (für Vision-Modelle)
        timeout: Timeout in Sekunden
        format:  "json" aktiviert Ollaamas nativen JSON-Modus —
                 das Modell gibt garantiert valides JSON zurück.
                 Nur nutzen wenn der Prompt explizit JSON anfordert.

    Returns:
        Antwort-String des Modells, oder "" bei Fehler.

    Raises:
        requests.HTTPError:      HTTP-Fehler vom Server
        requests.ConnectionError: Ollama nicht erreichbar
        requests.Timeout:        Zeitüberschreitung
    """
    payload: dict[str, Any] = {
        "model":  model,
        "prompt": prompt,
        "stream": False,
    }
    if images:
        payload["images"] = images
    if format:
        payload["format"] = format

    r = requests.post(generate_url(), json=payload, timeout=timeout)
    r.raise_for_status()

    resp = r.json()
    if err := resp.get("error"):
        log.warning("Ollama error for model %s: %s", model, err)
        return ""

    return resp.get("response", "")
