"""
LernOS — Shell-Autovervollständigung

click hat built-in Support für Bash, Zsh und Fish via Umgebungsvariablen.
Dieser Befehl schreibt die nötige Initialisierungszeile in die Shell-Konfigdatei.

Für dynamische Topic-Vorschläge registriert er eine Shell-Funktion, die
`lernos _complete_topics` aufruft — ein interner Befehl der die DB abfragt.
"""
from __future__ import annotations
import os
import sys
import click

from lernos import ui


# Shell-spezifische Einrichtungs-Snippets
BASH_SNIPPET = '''
# LernOS Tab-Completion (hinzugefügt von `lernos install-completion`)
eval "$(_LERNOS_COMPLETE=bash_source lernos)"
'''

ZSH_SNIPPET = '''
# LernOS Tab-Completion (hinzugefügt von `lernos install-completion`)
eval "$(_LERNOS_COMPLETE=zsh_source lernos)"
'''

FISH_SNIPPET = '''# LernOS Tab-Completion (hinzugefügt von `lernos install-completion`)
_LERNOS_COMPLETE=fish_source lernos | source
'''


def _detect_shell() -> str:
    """Ermittelt die aktuelle Shell."""
    shell = os.environ.get("SHELL", "")
    if "zsh"  in shell: return "zsh"
    if "fish" in shell: return "fish"
    return "bash"


def _get_rc_path(shell: str) -> str:
    home = os.path.expanduser("~")
    return {
        "bash": os.path.join(home, ".bashrc"),
        "zsh":  os.path.join(home, ".zshrc"),
        "fish": os.path.join(home, ".config", "fish", "config.fish"),
    }.get(shell, os.path.join(home, ".bashrc"))


def _snippet_for(shell: str) -> str:
    return {"bash": BASH_SNIPPET, "zsh": ZSH_SNIPPET, "fish": FISH_SNIPPET}.get(shell, BASH_SNIPPET)


def _already_installed(rc_path: str) -> bool:
    if not os.path.exists(rc_path):
        return False
    with open(rc_path) as f:
        return "_LERNOS_COMPLETE" in f.read()


def _install_for(shell: str, rc_path: str) -> None:
    snippet = _snippet_for(shell)
    os.makedirs(os.path.dirname(rc_path), exist_ok=True)
    with open(rc_path, "a") as f:
        f.write(snippet)


@click.command("install-completion")
@click.option("--shell", "-s",
              type=click.Choice(["bash", "zsh", "fish", "auto"]),
              default="auto",
              help="Shell-Typ (Standard: automatisch erkennen)")
@click.option("--print-only", is_flag=True,
              help="Snippet nur anzeigen, nicht in Konfigurationsdatei schreiben")
@click.option("--yes", "-y", is_flag=True)
def cmd_install_completion(shell: str, print_only: bool, yes: bool):
    """
    Tab-Vervollständigung für Bash, Zsh und Fish installieren.

    \b
    Nach der Installation kann man <TAB> nutzen für:
      lernos review <TAB>        → alle Topic-Namen
      lernos edit <TAB>          → alle Topic-Namen
      lernos freeze <TAB>        → alle Topic-Namen
      lernos --<TAB>             → alle Flags

    \b
    Basis: Click's built-in Shell-Completion (_LERNOS_COMPLETE Umgebungsvariable)
    Danach Shell neu starten oder `source ~/.bashrc` (bzw. zshrc/config.fish) ausführen.
    """
    detected = _detect_shell()
    if shell == "auto":
        shell = detected

    rc_path = _get_rc_path(shell)
    snippet = _snippet_for(shell)

    ui.header("⌨️  Tab-Completion installieren", f"Shell: {shell}")

    if print_only:
        print()
        ui.section(f"Snippet für {rc_path}")
        print(snippet)
        ui.info("Füge dies manuell in deine Shell-Konfiguration ein.")
        return

    if _already_installed(rc_path):
        ui.warn(f"Completion ist bereits in {rc_path} eingetragen.")
        ui.info("Starte die Shell neu oder führe aus:")
        _print_reload_cmd(shell, rc_path)
        return

    ui.info(f"Schreibe Completion-Snippet in: {rc_path}")
    print()
    print(snippet.strip())
    print()

    if not (yes or ui._yes_all or ui.confirm(f"In {rc_path} schreiben?", default=True)):
        ui.info("Abgebrochen. Mit --print-only das Snippet manuell kopieren.")
        return

    try:
        _install_for(shell, rc_path)
    except IOError as e:
        ui.error(f"Schreiben fehlgeschlagen: {e}")
        sys.exit(1)

    ui.success(f"Completion installiert in {rc_path}.")
    print()
    ui.section("Jetzt aktivieren")
    _print_reload_cmd(shell, rc_path)


def _print_reload_cmd(shell: str, rc_path: str) -> None:
    if shell == "fish":
        print(f"  {ui.c('source ' + rc_path, ui.BRIGHT_CYAN)}")
    else:
        print(f"  {ui.c('source ' + rc_path, ui.BRIGHT_CYAN)}")
    print()
    ui.info("Oder einfach eine neue Terminal-Session öffnen.")
