"""
LernOS — Setup: Abhängigkeiten prüfen und installieren.

Prüft und installiert:
  Python-Pakete:   pdfplumber, pdfminer.six, pdf2image, Pillow
  System-Tools:    Poppler (pdftoppm) — für PDF→Bild-Konvertierung
  Empfohlen:       ocrmypdf — für OCR-Vorverarbeitung gescannter PDFs
  Ollama:          LLM-Backend + Vision-Modell-Check

System-Packages (Poppler, ocrmypdf) können nicht via pip installiert werden.
Das Skript zeigt die richtigen Befehle für jede Plattform.
"""
from __future__ import annotations
import platform
import shutil
import subprocess
import sys

import click
from lernos import ui


# ── Plattform-Erkennung ────────────────────────────────────────────────────────

def _platform() -> str:
    s = platform.system()
    return {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(s, "linux")


def _pkg_manager() -> str:
    if _platform() != "linux":
        return ""
    for pm in ["apt", "apt-get", "dnf", "pacman", "zypper"]:
        if shutil.which(pm):
            return pm
    return "apt"


def _system_install_cmd(pkg_map: dict) -> str:
    """Gibt den richtigen System-Installationsbefehl zurück."""
    plat = _platform()
    pm   = _pkg_manager()
    if plat == "linux":
        pkg = pkg_map.get("apt", pkg_map.get("linux", ""))
        return f"sudo {pm} install -y {pkg}"
    elif plat == "macos":
        pkg = pkg_map.get("brew", pkg_map.get("macos", ""))
        return f"brew install {pkg}"
    elif plat == "windows":
        return pkg_map.get("windows", "")
    return ""


SYSTEM_DEPS = [
    {
        "name":    "Poppler (pdftoppm)",
        "binary":  "pdftoppm",
        "desc":    "PDF→Bild-Konvertierung (für --vision benötigt)",
        "required": False,   # nur für Vision
        "install": {
            "apt":     "poppler-utils",
            "brew":    "poppler",
            "windows": "https://github.com/oschwartz10612/poppler-windows/releases",
        },
    },
    {
        "name":    "ocrmypdf",
        "binary":  "ocrmypdf",
        "desc":    "OCR-Vorverarbeitung für gescannte PDFs (optional)",
        "required": False,
        "install": {
            "apt":     "ocrmypdf",
            "brew":    "ocrmypdf",
            "windows": "pip install ocrmypdf",
        },
    },
    {
        "name":    "Tesseract",
        "binary":  "tesseract",
        "desc":    "OCR-Engine (benötigt von ocrmypdf)",
        "required": False,
        "install": {
            "apt":     "tesseract-ocr tesseract-ocr-deu",
            "brew":    "tesseract",
            "windows": "https://github.com/UB-Mannheim/tesseract/wiki",
        },
    },
]

PYTHON_DEPS = [
    ("pdfplumber",  "pdfplumber>=0.9",   "PDF Text-Extraktion",       True),
    ("pdfminer",    "pdfminer.six",       "PDF Text-Fallback",         True),
    ("pdf2image",   "pdf2image>=1.16",    "PDF→Bild (Vision)",         False),
    ("PIL",         "Pillow>=9.0",        "Bildverarbeitung (Vision)",  False),
]


@click.command("setup")
@click.option("--check-only", is_flag=True,
              help="Nur prüfen, nichts installieren")
@click.option("--vision", is_flag=True,
              help="Vision-Abhängigkeiten als erforderlich behandeln")
def cmd_setup(check_only: bool, vision: bool):
    """
    Alle Abhängigkeiten prüfen und fehlende Python-Pakete installieren.

    \b
    Geprüft wird:
      • Python-Pakete (pdfplumber, pdf2image, Pillow, pdfminer.six)
      • Poppler     — sudo apt install poppler-utils
      • ocrmypdf    — sudo apt install ocrmypdf  (empfohlen für gescannte PDFs)
      • Ollama      — Vision-Modell-Verfügbarkeit

    \b
    Beispiele:
      lernos setup               # Prüfen + Python-Pakete installieren
      lernos setup --check-only  # Nur anzeigen, nichts tun
      lernos setup --vision      # Vision-Deps als Pflicht markieren
    """
    ui.header("🛠  LernOS Setup", f"Plattform: {platform.system()} · Python {sys.version.split()[0]}")

    missing_pip  = []
    missing_sys  = []
    warnings     = []
    all_critical = True

    # ── Python-Pakete ──────────────────────────────────────────────────────────
    ui.section("Python-Pakete")
    for module, pip_pkg, desc, required in PYTHON_DEPS:
        is_required = required or (vision and "Vision" in desc)
        try:
            __import__(module)
            print(f"  {ui.c('✅', ui.BRIGHT_GREEN)} {pip_pkg:<28} {ui.c(desc, ui.DIM)}")
        except ImportError:
            if is_required:
                print(f"  {ui.c('❌', ui.BRIGHT_RED)} {pip_pkg:<28} {ui.c(desc, ui.DIM)}")
                missing_pip.append(pip_pkg)
                all_critical = False
            else:
                print(f"  {ui.c('⚠️ ', ui.BRIGHT_YELLOW)} {pip_pkg:<28} {ui.c(desc + ' (optional)', ui.DIM)}")
                missing_pip.append(pip_pkg)  # optional aber trotzdem installieren

    # ── System-Tools ──────────────────────────────────────────────────────────
    ui.section("System-Tools")
    for dep in SYSTEM_DEPS:
        binary    = dep["binary"]
        found     = bool(shutil.which(binary))
        is_req    = dep["required"] or (vision and "Vision" in dep["desc"])

        if found:
            ver = _tool_version(binary)
            print(f"  {ui.c('✅', ui.BRIGHT_GREEN)} {dep['name']:<28} {ui.c(ver, ui.DIM)}")
        else:
            cmd = _system_install_cmd(dep["install"])
            if is_req:
                print(f"  {ui.c('❌', ui.BRIGHT_RED)} {dep['name']:<28} {ui.c(dep['desc'], ui.DIM)}")
                all_critical = False
            else:
                print(f"  {ui.c('⚠️ ', ui.BRIGHT_YELLOW)} {dep['name']:<28} {ui.c(dep['desc'], ui.DIM)}")

            if cmd:
                if _platform() == "windows" and cmd.startswith("http"):
                    print(f"     {ui.c('→ Download:', ui.DIM)} {ui.c(cmd, ui.BRIGHT_CYAN)}")
                else:
                    print(f"     {ui.c('→', ui.DIM)} {ui.c(cmd, ui.BRIGHT_CYAN)}")
                missing_sys.append((dep["name"], cmd))

    # ── Ollama ────────────────────────────────────────────────────────────────
    ui.section("Ollama & Modelle")
    try:
        import requests
        r      = requests.get("http://localhost:11434/api/tags", timeout=3)
        models = {m["name"] for m in r.json().get("models", [])}
        short  = ", ".join(sorted(models)[:5]) or "(keine)"
        print(f"  {ui.c('✅', ui.BRIGHT_GREEN)} Ollama läuft             {ui.c(short[:55], ui.DIM)}")

        from lernos.pdf.vision import get_available_vision_model
        vm = get_available_vision_model()
        if vm:
            print(f"  {ui.c('✅', ui.BRIGHT_GREEN)} Vision-Modell:           {ui.c(vm, ui.BRIGHT_CYAN)}")
        else:
            print(f"  {ui.c('⚠️ ', ui.BRIGHT_YELLOW)} Kein Vision-Modell installiert")
            for line in [
                "ollama pull llava              # ~4 GB, weit verbreitet",
                "ollama pull llava-phi3         # ~3 GB, kompakter",
                "ollama pull llama3.2-vision    # ~8 GB, beste Qualität",
            ]:
                print(f"     {ui.c('→', ui.DIM)} {ui.c(line, ui.BRIGHT_CYAN)}")
            warnings.append("Vision-Modell fehlt — lernos doc attach --vision nicht verfügbar")

    except Exception:
        print(f"  {ui.c('❌', ui.BRIGHT_RED)} Ollama nicht erreichbar")
        print(f"     {ui.c('→', ui.DIM)} {ui.c('ollama serve', ui.BRIGHT_CYAN)}")
        warnings.append("Ollama läuft nicht — alle KI-Features deaktiviert")

    # ── Fehlende Python-Pakete installieren ───────────────────────────────────
    if missing_pip and not check_only:
        print()
        ui.section(f"Installiere {len(missing_pip)} Python-Paket(e)")
        for pkg in missing_pip:
            _pip_install(pkg)

    # ── System-Pakete: Anleitung ──────────────────────────────────────────────
    if missing_sys and not check_only:
        print()
        ui.section("System-Pakete (manuell installieren)")
        ui.info("Folgende System-Pakete können nicht automatisch installiert werden:")
        print()
        for name, cmd in missing_sys:
            print(f"  {ui.c(name, ui.BOLD)}")
            if _platform() == "windows" and cmd.startswith("http"):
                print(f"    {ui.c(cmd, ui.BRIGHT_CYAN)}")
            else:
                print(f"    {ui.c(f'$ {cmd}', ui.BRIGHT_CYAN)}")
            print()

    # ── Warnungen ─────────────────────────────────────────────────────────────
    if warnings:
        print()
        ui.section("Hinweise")
        for w in warnings:
            print(f"  {ui.c('→', ui.BRIGHT_YELLOW)} {w}")

    # ── Ergebnis ──────────────────────────────────────────────────────────────
    print()
    if all_critical and not missing_pip:
        ui.success("Alle Abhängigkeiten vorhanden — LernOS ist einsatzbereit! 🚀")
    elif not all_critical:
        ui.error("Fehlende kritische Pakete. Bitte die Hinweise oben beachten.")
        sys.exit(1)
    else:
        ui.success("Kern-Setup OK. Optionale Pakete wurden installiert/angezeigt.")
    print()


def _pip_install(pkg: str) -> None:
    print(f"  📦 {ui.c(pkg, ui.BRIGHT_CYAN)}...", end=" ", flush=True)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg,
             "--break-system-packages", "--quiet"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print(ui.c("✅", ui.BRIGHT_GREEN))
        else:
            err = (r.stderr or r.stdout).strip().split("\n")[-1][:80]
            print(ui.c(f"❌ {err}", ui.BRIGHT_RED))
    except Exception as e:
        print(ui.c(f"❌ {e}", ui.BRIGHT_RED))


def _tool_version(binary: str) -> str:
    for flag in ["--version", "-v", "-V"]:
        try:
            r = subprocess.run([binary, flag], capture_output=True, text=True, timeout=5)
            out = (r.stdout + r.stderr).strip()
            if out:
                return out.split("\n")[0][:50]
        except Exception:
            continue
    return ""
