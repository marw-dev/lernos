"""
LernOS — app add (User-Version mit phi3-Schiedsrichter)
"""
from __future__ import annotations
import sqlite3
import click
from lernos import ui
from lernos.completion_helpers import complete_topic_names, complete_due_topic_names, complete_module_names
from lernos.db.topics import (create_edge, create_topic, get_all_edges, get_all_topics, get_topic_by_name)
from lernos.ollama.embed import (ask_prerequisite, embedding_to_blob, find_similar_topics, get_embedding, is_ollama_running)

AUTO_THRESHOLD = 0.78

def _would_create_cycle(edges: list, from_id: int, to_id: int) -> bool:
    adj: dict[int, list[int]] = {}
    for e in edges:
        adj.setdefault(e.to_id, []).append(e.from_id)
    stack = [to_id]; visited: set[int] = set()
    while stack:
        curr = stack.pop()
        if curr == from_id: return True
        if curr in visited: continue
        visited.add(curr); stack.extend(adj.get(curr, []))
    adj_fwd: dict[int, list[int]] = {}
    for e in edges:
        adj_fwd.setdefault(e.from_id, []).append(e.to_id)
    stack = [to_id]; visited = set()
    while stack:
        curr = stack.pop()
        if curr == from_id: return True
        if curr in visited: continue
        visited.add(curr); stack.extend(adj_fwd.get(curr, []))
    return False

@click.command("add")
@click.argument("name")
@click.option("--module", "-m", default="", help="Modul/Fach")
@click.option("--desc", "-d", default="", help="Kurze Beschreibung")
@click.option("--auto", is_flag=True, help="Automatische Kantenerstellung")
@click.option("--auto-threshold", default=AUTO_THRESHOLD, type=float)
def cmd_add(name: str, module: str, desc: str, auto: bool, auto_threshold: float):
    """Neues Topic anlegen und in den Wissensgraph einhängen."""
    from lernos.db.schema import startup
    conn = startup()
    existing = get_topic_by_name(conn, name)
    if existing:
        ui.warn(f"Topic '{name}' existiert bereits (ID={existing.id})")
        return
    if not module:
        module = ui.prompt("Modul/Fach", "")
    ui.header(f"Neues Topic: {name}", f"Modul: {module or '—'}")
    topic = create_topic(conn, name, module, desc)
    ui.success(f"Topic '{name}' angelegt (ID={topic.id})")
    ollama_ok = is_ollama_running()
    if ollama_ok:
        ui.info("Hole Embedding von Ollama...")
        emb = get_embedding(name)
        if emb:
            from lernos.db.topics import update_topic_embedding
            update_topic_embedding(conn, topic.id, embedding_to_blob(emb))
            ui.success(f"Embedding: {len(emb)} Dimensionen")
            all_topics = get_all_topics(conn)
            candidates_raw = [(t.id, t.name, t.embedding) for t in all_topics
                              if t.id != topic.id and t.embedding is not None]
            if candidates_raw:
                ui.section("Semantisch ähnliche Topics")
                candidates = find_similar_topics(emb, candidates_raw, top_k=5, min_similarity=0.35)
                all_edges = get_all_edges(conn)
                _run_edge_dialog(conn, topic, candidates, auto, auto_threshold, all_edges)
            else:
                ui.info("Keine anderen Topics mit Embeddings.")
        else:
            ui.warn("Embedding-Fehler — manueller Edge-Dialog.")
            _manual_edge_dialog(conn, topic)
    else:
        ui.warn("Ollama nicht verfügbar — manueller Edge-Dialog.")
        _manual_edge_dialog(conn, topic)
    print()
    ui.success(f"✨ Topic '{name}' vollständig hinzugefügt!")
    _show_summary(conn, topic)

def _run_edge_dialog(conn, new_topic, candidates, auto, threshold, all_edges):
    edges_created = 0
    for cand in candidates:
        sim = cand["similarity"]; cname = cand["name"]; cid = cand["id"]
        sim_bar = ui.progress_bar(sim, max_val=1.0, width=15)
        print(f"\n  {ui.c('►', ui.BRIGHT_CYAN)} {ui.c(cname, ui.BOLD)}: {sim_bar} {ui.c(f'{sim:.2f}', ui.DIM)}")

        def _check_and_create(f_id, t_id, w):
            nonlocal edges_created
            if _would_create_cycle(all_edges, f_id, t_id):
                print(f"    {ui.c('⚠ Übersprungen — würde Zykel erzeugen', ui.BRIGHT_YELLOW)}")
                return False
            create_edge(conn, f_id, t_id, weight=w, confirmed=True)
            edges_created += 1
            return True

        if sim >= threshold and auto:
            if _check_and_create(cid, new_topic.id, sim):
                print(f"    {ui.c('→ Automatisch verknüpft (Hohe Ähnlichkeit)', ui.BRIGHT_GREEN)}")
            continue
        if sim >= 0.40:
            print(f"    {ui.c('🧠 Befrage KI nach semantischem Zusammenhang...', ui.DIM)}")
            is_prereq = ask_prerequisite(cname, new_topic.name)
            if is_prereq is True:
                if _check_and_create(cid, new_topic.id, max(sim, 0.6)):
                    print(f"    {ui.c('✨ KI sagt JA → Automatisch verknüpft!', ui.BRIGHT_GREEN)}")
                continue
            elif is_prereq is False:
                print(f"    {ui.c('→ KI sagt NEIN', ui.DIM)}")
                if auto: continue
            else:
                print(f"    {ui.c('→ KI unsicher oder Timeout', ui.DIM)}")
                if auto: continue
        elif auto:
            print(f"    {ui.c(f'Ähnlichkeit {sim:.2f} < 0.40 — übersprungen', ui.DIM)}")
            continue

        print(f"    Ist {ui.c(cname, ui.BOLD)} Voraussetzung für {ui.c(new_topic.name, ui.BOLD)}?")
        choice = ui.prompt("  [j]a / [n]ein / [u]mgekehrt / [s]kip", "n").lower()
        if choice in ("j", "ja", "y"):
            if _check_and_create(cid, new_topic.id, sim):
                ui.success(f"Kante: {cname} → {new_topic.name}")
        elif choice in ("u", "umgekehrt"):
            if _check_and_create(new_topic.id, cid, sim):
                ui.success(f"Kante: {new_topic.name} → {cname}")

    if edges_created:
        ui.success(f"{edges_created} Kante(n) angelegt")

def _manual_edge_dialog(conn, new_topic):
    """
    Interaktiver Edge-Dialog mit Pfeiltasten-Navigation.
    Fallback auf Nummerneingabe wenn nicht im TTY (z.B. --yes oder Pipe).
    """
    all_topics = get_all_topics(conn)
    others     = [t for t in all_topics if t.id != new_topic.id]
    if not others: return
    all_edges  = get_all_edges(conn)

    ui.section("Manuelle Verknüpfung")

    # Optionen für Multiselect aufbereiten
    labels = [f"{t.name}  {ui.c(f'({t.module or '—'}) [{t.state}]', ui.DIM)}"
              for t in others[:30]]
    if not labels: return

    # Interaktive Multiauswahl (Pfeiltasten + Leertaste)
    chosen_indices = ui.multiselect(
        f"Voraussetzungen für '{new_topic.name}' auswählen (LEERTASTE = togglen)",
        labels,
    )

    if not chosen_indices:
        ui.info("Keine Verknüpfungen angelegt.")
        return

    for idx in chosen_indices:
        prereq = others[idx]
        # Richtung bestimmen
        dir_options = [
            f"{prereq.name}  →  {new_topic.name}  (Voraussetzung)",
            f"{new_topic.name}  →  {prereq.name}  (umgekehrt)",
        ]
        dir_idx = ui.select(f"Richtung für '{prereq.name}'", dir_options)

        weight_str = ui.prompt("Kantengewicht (0.1-1.0)", "0.6")
        try:
            weight = max(0.1, min(1.0, float(weight_str)))
        except ValueError:
            weight = 0.6

        if dir_idx == 0:
            if not _would_create_cycle(all_edges, prereq.id, new_topic.id):
                create_edge(conn, prereq.id, new_topic.id, weight=weight, confirmed=True)
                ui.success(f"Kante: {prereq.name} → {new_topic.name}  (Gewicht: {weight:.2f})")
            else:
                ui.warn(f"Kante {prereq.name} → {new_topic.name} würde Zykel erzeugen — übersprungen.")
        else:
            if not _would_create_cycle(all_edges, new_topic.id, prereq.id):
                create_edge(conn, new_topic.id, prereq.id, weight=weight, confirmed=True)
                ui.success(f"Kante: {new_topic.name} → {prereq.name}  (Gewicht: {weight:.2f})")
            else:
                ui.warn(f"Kante würde Zykel erzeugen — übersprungen.")

def _show_summary(conn, topic):
    from lernos.db.topics import get_edges_for_topic
    edges = get_edges_for_topic(conn, topic.id)
    ui.section("Zusammenfassung")
    print(f"  {ui.c('Name:', ui.BOLD)}    {topic.name}")
    print(f"  {ui.c('Modul:', ui.BOLD)}   {topic.module or '—'}")
    print(f"  {ui.c('Kanten:', ui.BOLD)}  {len(edges['incoming'])} eingehend, {len(edges['outgoing'])} ausgehend")
    print()
