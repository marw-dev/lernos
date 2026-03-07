"""
LernOS — Haupt-CLI-Einstiegspunkt v1.5

Globale Flags (vor dem Subbefehl):
  --verbose / -v    Python logging.DEBUG (Ollama, PDF, HTTP)
  --quiet   / -q    Nur Fehler + Machine-Readable-Output
  --yes     / -y    Alle Prompts mit Default/Ja beantworten
"""
from __future__ import annotations
import click

from lernos import ui
from lernos.cmd.add          import cmd_add
from lernos.cmd.anki         import cmd_import_anki
from lernos.cmd.backup       import cmd_backup, cmd_restore
from lernos.cmd.completion   import cmd_install_completion
from lernos.cmd.doc          import cmd_doc
from lernos.cmd.edit         import cmd_edit, cmd_edit_batch
from lernos.cmd.misc         import (
    cmd_config, cmd_delete, cmd_diagnose, cmd_edge, cmd_export, cmd_freeze,
    cmd_graph, cmd_import_csv, cmd_install_scheduler, cmd_list,
    cmd_notify, cmd_stats, cmd_unfreeze,
)
from lernos.cmd.review       import cmd_review
from lernos.cmd.setup        import cmd_setup
from lernos.cmd.search       import cmd_search
from lernos.cmd.undo         import cmd_undo


@click.group()
@click.version_option(version="1.8.2", prog_name="LernOS")
@click.option("--verbose", "-v", is_flag=True,
              help="Debug-Logging (Ollama, PDF, HTTP)")
@click.option("--quiet",   "-q", is_flag=True,
              help="Nur Fehler ausgeben (für Skripte/Pipes)")
@click.option("--yes",     "-y", is_flag=True,
              help="Alle Bestätigungsprompts mit Ja beantworten")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, quiet: bool, yes: bool):
    """LernOS — Intelligentes Lern-Betriebssystem

    \b
    Globale Flags (vor dem Befehl):
      lernos -v doc attach skript.pdf   # Verbose: Ollama + PDF-Logs
      lernos -q list --format json      # Quiet: nur JSON
      lernos -y delete "altes Thema"    # Kein Prompt
      lernos -qy import-csv topics.csv  # Kombiniert
    """
    ctx.ensure_object(dict)
    ui.set_verbose(verbose)
    ui.set_quiet(quiet)
    ui.set_yes(yes)


# ── Topics ────────────────────────────────────────────────────────────────────
cli.add_command(cmd_add)
cli.add_command(cmd_edit)
cli.add_command(cmd_edit_batch)
cli.add_command(cmd_delete)
cli.add_command(cmd_review)
cli.add_command(cmd_undo)
cli.add_command(cmd_list)
cli.add_command(cmd_freeze)
cli.add_command(cmd_unfreeze)
cli.add_command(cmd_search)

# ── Graph + Planung ───────────────────────────────────────────────────────────
cli.add_command(cmd_graph)
cli.add_command(cmd_export)
cli.add_command(cmd_edge)
cli.add_command(cmd_stats)
cli.add_command(cmd_diagnose)

# ── Dokumente ─────────────────────────────────────────────────────────────────
cli.add_command(cmd_doc)

# ── Import ────────────────────────────────────────────────────────────────────
cli.add_command(cmd_import_csv)
cli.add_command(cmd_import_anki)

# ── Backup / Restore ─────────────────────────────────────────────────────────
cli.add_command(cmd_backup)
cli.add_command(cmd_restore)

# ── System ────────────────────────────────────────────────────────────────────
cli.add_command(cmd_setup)
cli.add_command(cmd_notify)
cli.add_command(cmd_config)
cli.add_command(cmd_install_scheduler)
cli.add_command(cmd_install_completion)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
