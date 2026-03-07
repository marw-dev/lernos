"""
LernOS — Terminal-Formatierung

Änderungen v1.4:
  - Globale Kontextflags: _quiet, _yes_all (gesetzt via cli.py --quiet/--yes)
  - confirm() respektiert --yes (gibt immer True zurück)
  - prompt() respektiert --yes (gibt immer Default zurück)
  - header/section/success/info/warn supprimiert bei --quiet
  - select() — Arrow-Key Navigation ohne externe Bibliothek (ANSI)
  - multiselect() — Checkbox-Liste mit Leertaste
"""
from __future__ import annotations
import os
import sys
import shutil

try:
    import colorama
    colorama.init()
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

# ANSI codes
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
ITALIC = "\033[3m"

BLACK   = "\033[30m"; RED     = "\033[31m"; GREEN   = "\033[32m"
YELLOW  = "\033[33m"; BLUE    = "\033[34m"; MAGENTA = "\033[35m"
CYAN    = "\033[36m"; WHITE   = "\033[37m"

BRIGHT_BLACK   = "\033[90m"; BRIGHT_RED     = "\033[91m"; BRIGHT_GREEN   = "\033[92m"
BRIGHT_YELLOW  = "\033[93m"; BRIGHT_BLUE    = "\033[94m"; BRIGHT_MAGENTA = "\033[95m"
BRIGHT_CYAN    = "\033[96m"; BRIGHT_WHITE   = "\033[97m"

BG_BLACK  = "\033[40m"; BG_BLUE  = "\033[44m"; BG_GREEN = "\033[42m"
BG_RED    = "\033[41m"; BG_DARK  = "\033[48;5;235m"; BG_DARKER = "\033[48;5;232m"


# ── Globale Flags (gesetzt von cli.py) ────────────────────────────────────────

_quiet:   bool = False   # Unterdrückt header/section/success/info/warn
_yes_all: bool = False   # Alle confirm/prompt geben Default zurück
_verbose: bool = False   # Aktiviert Python logging.DEBUG


def set_quiet(v: bool):   global _quiet;   _quiet   = v
def set_yes(v: bool):     global _yes_all; _yes_all = v
def set_verbose(v: bool):
    global _verbose; _verbose = v
    import logging
    level = logging.DEBUG if v else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s  %(name)s  %(message)s",
    )


# ── Farb-Utilities ────────────────────────────────────────────────────────────

def _no_color() -> bool:
    return not sys.stdout.isatty() or os.environ.get("NO_COLOR") is not None


def c(text: str, *codes: str) -> str:
    if _no_color():
        return text
    return "".join(codes) + text + RESET


def term_width() -> int:
    return shutil.get_terminal_size((80, 24)).columns


# ── Output-Funktionen (respektieren --quiet) ──────────────────────────────────

def hr(char: str = "─", color: str = BRIGHT_BLACK) -> None:
    if _quiet: return
    print(c(char * term_width(), color))


def header(title: str, subtitle: str = "") -> None:
    if _quiet: return
    w = term_width()
    print()
    print(c("┌" + "─" * (w - 2) + "┐", BRIGHT_BLACK))
    title_str = f"  {title}"
    print(c("│", BRIGHT_BLACK) + c(title_str.ljust(w - 2), BOLD, BRIGHT_WHITE) + c("│", BRIGHT_BLACK))
    if subtitle:
        sub_str = f"  {subtitle}"
        print(c("│", BRIGHT_BLACK) + c(sub_str.ljust(w - 2), DIM) + c("│", BRIGHT_BLACK))
    print(c("└" + "─" * (w - 2) + "┘", BRIGHT_BLACK))
    print()


def section(title: str) -> None:
    if _quiet: return
    print()
    print(c(f"  {title}", BOLD, BRIGHT_BLUE))
    print(c("  " + "─" * (len(title) + 2), BRIGHT_BLACK))


def success(msg: str) -> None:
    if _quiet: return
    print(c(f"  ✅  {msg}", BRIGHT_GREEN))


def error(msg: str) -> None:
    # Errors sind immer sichtbar (auch bei --quiet)
    print(c(f"  ❌  {msg}", BRIGHT_RED), file=sys.stderr)


def warn(msg: str) -> None:
    if _quiet: return
    print(c(f"  ⚠️   {msg}", BRIGHT_YELLOW))


def info(msg: str) -> None:
    if _quiet: return
    print(c(f"  ℹ️   {msg}", BRIGHT_CYAN))


def raw(msg: str) -> None:
    """Immer ausgeben — auch bei --quiet. Für Machine-Readable Output."""
    print(msg)


# ── Interaktion (respektieren --yes) ─────────────────────────────────────────

def prompt(msg: str, default: str = "") -> str:
    if _yes_all:
        return default
    hint = f" [{default}]" if default else ""
    try:
        val = input(c(f"\n  {msg}{hint}: ", BOLD, BRIGHT_CYAN)).strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def confirm(msg: str, default: bool = True) -> bool:
    if _yes_all:
        return True
    hint = "J/n" if default else "j/N"
    while True:
        val = prompt(f"{msg} ({hint})").lower()
        if val in ("", "j", "ja", "y", "yes"):
            return True if (val == "" and default) or val in ("j", "ja", "y", "yes") else False
        if val in ("n", "nein", "no"):
            return False


# ── TUI: Arrow-Key Select (ohne externe Bibliothek) ──────────────────────────

def _read_key() -> str:
    """Liest einen Tastendruck (inkl. Escape-Sequenzen für Pfeiltasten)."""
    import tty, termios
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            ch3 = sys.stdin.read(1)
            return f"\x1b{ch2}{ch3}"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def select(title: str, options: list[str], default: int = 0) -> int:
    """
    Interaktive Einzelauswahl mit Pfeiltasten.
    Gibt den Index der gewählten Option zurück.
    Fallback auf nummerische Eingabe wenn nicht im TTY (z.B. Pipe, --yes).

    Tasten: ↑/↓ navigieren, Enter bestätigen, q/ESC abbrechen (→ default)
    """
    if _yes_all or not _is_tty():
        # Nicht-interaktiver Fallback
        return default

    cursor = default
    UP    = "\x1b[A"
    DOWN  = "\x1b[B"
    ENTER = "\r"

    def _render(cur: int):
        # Vorherige Zeilen löschen
        sys.stdout.write(f"\033[{len(options) + 2}A\033[J")
        sys.stdout.write(c(f"\n  {title}\n", BOLD, BRIGHT_CYAN))
        for i, opt in enumerate(options):
            if i == cur:
                sys.stdout.write(c(f"  ❯ {opt}\n", BRIGHT_WHITE, BOLD))
            else:
                sys.stdout.write(c(f"    {opt}\n", DIM))
        sys.stdout.flush()

    # Initial render (keine Löschung)
    sys.stdout.write(c(f"\n  {title}\n", BOLD, BRIGHT_CYAN))
    for i, opt in enumerate(options):
        if i == cursor:
            sys.stdout.write(c(f"  ❯ {opt}\n", BRIGHT_WHITE, BOLD))
        else:
            sys.stdout.write(c(f"    {opt}\n", DIM))
    sys.stdout.flush()

    while True:
        try:
            key = _read_key()
        except Exception:
            break

        if key == UP:
            cursor = (cursor - 1) % len(options)
            _render(cursor)
        elif key == DOWN:
            cursor = (cursor + 1) % len(options)
            _render(cursor)
        elif key in (ENTER, " "):
            break
        elif key in ("\x1b", "q", "\x03"):
            cursor = default
            break

    print()
    return cursor


def multiselect(title: str, options: list[str],
                selected: list[int] | None = None) -> list[int]:
    """
    Interaktive Multi-Auswahl mit Pfeiltasten + Leertaste.
    Gibt Liste der ausgewählten Indices zurück.
    Fallback auf kommagetrennte Nummerneingabe wenn nicht im TTY.

    Tasten: ↑/↓ navigieren, LEERTASTE togglen, Enter bestätigen, a = alle, n = keine
    """
    if _yes_all or not _is_tty():
        return selected or list(range(len(options)))

    checked  = set(selected or [])
    cursor   = 0
    UP       = "\x1b[A"
    DOWN     = "\x1b[B"
    ENTER    = "\r"

    def _render(cur: int):
        sys.stdout.write(f"\033[{len(options) + 4}A\033[J")
        sys.stdout.write(c(f"\n  {title}\n", BOLD, BRIGHT_CYAN))
        sys.stdout.write(c("  LEERTASTE = togglen  |  a = alle  |  n = keine  |  ENTER = fertig\n", DIM))
        for i, opt in enumerate(options):
            box   = c("■", BRIGHT_CYAN) if i in checked else c("□", DIM)
            arrow = c("❯ ", BRIGHT_WHITE) if i == cur else "  "
            col   = BRIGHT_WHITE if i == cur else (BRIGHT_CYAN if i in checked else "")
            line  = c(opt, col) if col else opt
            sys.stdout.write(f"  {arrow}{box} {line}\n")
        sys.stdout.flush()

    # Initial render
    sys.stdout.write(c(f"\n  {title}\n", BOLD, BRIGHT_CYAN))
    sys.stdout.write(c("  LEERTASTE = togglen  |  a = alle  |  n = keine  |  ENTER = fertig\n", DIM))
    for i, opt in enumerate(options):
        box   = c("■", BRIGHT_CYAN) if i in checked else c("□", DIM)
        arrow = c("❯ ", BRIGHT_WHITE) if i == cursor else "  "
        col   = BRIGHT_WHITE if i == cursor else (BRIGHT_CYAN if i in checked else "")
        line  = c(opt, col) if col else opt
        sys.stdout.write(f"  {arrow}{box} {line}\n")
    sys.stdout.flush()

    while True:
        try:
            key = _read_key()
        except Exception:
            break
        if key == UP:
            cursor = (cursor - 1) % len(options)
            _render(cursor)
        elif key == DOWN:
            cursor = (cursor + 1) % len(options)
            _render(cursor)
        elif key == " ":
            if cursor in checked: checked.discard(cursor)
            else:                 checked.add(cursor)
            _render(cursor)
        elif key == "a":
            checked = set(range(len(options)))
            _render(cursor)
        elif key == "n":
            checked.clear()
            _render(cursor)
        elif key in (ENTER,):
            break
        elif key in ("\x1b", "\x03"):
            break

    print()
    return sorted(checked)


# ── Sonstige Formatierung ─────────────────────────────────────────────────────

def topic_state_line(topic) -> str:
    from lernos.db.topics import STATE_EMOJI, STATE_FROZEN, STATE_LEARNING, STATE_MASTERED, STATE_REVIEW, STATE_NEW
    state_colors = {
        STATE_NEW:      BRIGHT_BLACK, STATE_LEARNING: BRIGHT_RED,
        STATE_REVIEW:   BRIGHT_BLUE,  STATE_MASTERED: BRIGHT_GREEN,
        STATE_FROZEN:   BRIGHT_MAGENTA,
    }
    emoji     = STATE_EMOJI.get(topic.state, "  ")
    col       = state_colors.get(topic.state, WHITE)
    state_str = c(f"[{topic.state:<8}]", col)
    name_str  = c(topic.name, BOLD)
    mod_str   = c(f" ({topic.module})", DIM) if topic.module else ""
    ef_str    = c(f"  EF:{topic.ef:.2f}", DIM)
    due_str   = format_due(topic)
    return f"  {emoji} {state_str}  {name_str}{mod_str}{ef_str}  {due_str}"


def format_due(topic) -> str:
    days = topic.days_until_due
    if topic.state == "FROZEN":
        return c(f"❄️  bis {topic.frozen_until}", BRIGHT_MAGENTA)
    if days < 0:
        return c(f"⚠️  {abs(days)}d überfällig", BRIGHT_RED)
    if days == 0:
        return c("📅 heute fällig", BRIGHT_YELLOW)
    if days <= 3:
        return c(f"📅 in {days}d", YELLOW)
    return c(f"in {days}d", DIM)


def table(headers: list[str], rows: list[list[str]],
          col_widths: list[int] | None = None) -> None:
    if not rows:
        info("Keine Einträge.")
        return
    if col_widths is None:
        col_widths = [max(len(h), max((len(str(r[i])) for r in rows), default=0))
                      for i, h in enumerate(headers)]
    sep         = "  "
    header_line = sep.join(c(h.ljust(col_widths[i]), BOLD) for i, h in enumerate(headers))
    print(f"  {header_line}")
    print(c("  " + "─" * sum(col_widths) + "─" * (len(headers)-1) * len(sep), BRIGHT_BLACK))
    for row in rows:
        line = sep.join(str(row[i]).ljust(col_widths[i]) if i < len(row) else ""
                        for i in range(len(headers)))
        print(f"  {line}")


def progress_bar(value: float, max_val: float = 2.5, width: int = 20) -> str:
    filled = int((value / max_val) * width)
    bar    = "█" * filled + "░" * (width - filled)
    if value >= 2.0:   return c(bar, BRIGHT_GREEN)
    if value >= 1.6:   return c(bar, BRIGHT_YELLOW)
    return c(bar, BRIGHT_RED)


def box(lines: list[str], title: str = "", color: str = BRIGHT_BLACK) -> None:
    w = max((len(l) for l in lines), default=20) + 4
    if title: w = max(w, len(title) + 4)
    print(c(f"  ┌─ {title} " + "─" * max(0, w - len(title) - 4) + "┐", color))
    for line in lines:
        print(c("  │ ", color) + line.ljust(w - 2) + c(" │", color))
    print(c("  └" + "─" * w + "┘", color))
