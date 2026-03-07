# LernOS 📚

**Intelligentes Lern-Betriebssystem** — Spaced Repetition + Wissensgraph + semantische Verknüpfung.

Lokal. Offline. Kein Account. Keine Cloud.

---

## Installation

```bash
cd lernos
pip install -e .
```

Danach steht `lernos` global als CLI-Befehl zur Verfügung.

**Abhängigkeiten:** Python ≥ 3.10, click, requests, colorama, pdfplumber, pdfminer.six, pdf2image

**Optional (für semantische Verknüpfung und KI-Bewertung):**
```bash
# Ollama installieren: https://ollama.com
ollama pull nomic-embed-text   # Embeddings (768-dim)
ollama pull phi3               # Voraussetzungs-Schiedsrichter + Antwortbewertung

# Optional: Vision-Modelle für Präsentations-PDFs
ollama pull llava              # empfohlen, ~4 GB
ollama pull llava-phi3         # kleiner, ~3 GB
ollama pull llama3.2-vision    # beste Qualität, ~8 GB
```

**Optional (für Vision-Modus bei Präsentationen):**
```bash
# Poppler für PDF-zu-Bild-Rendering
sudo apt install poppler-utils    # Linux
brew install poppler              # macOS
```

---

## Schnellstart

```bash
# Topics hinzufügen
lernos add "Grenzwerte" --module "Analysis I"
lernos add "Stetigkeit" --module "Analysis I"
lernos add "Differenzierbarkeit" --module "Analysis I"

# Kante: Stetigkeit ist Voraussetzung für Differenzierbarkeit
lernos edge add "Stetigkeit" "Differenzierbarkeit" --weight 0.95

# Review starten (2 Eingaben: Konfidenz + Grade)
lernos review

# PDF-Skript als Lernmaterial anhängen
lernos doc attach "Grenzwerte" analysis_skript.pdf

# Präsentation anhängen mit Vision-Modell (erkennt Layout, Diagramme, Formeln)
lernos doc attach "Kettenregel" vorlesung.pdf --vision

# Fragen-basiertes Review (aus PDF generiert)
lernos review --questions

# Web-Review im Browser (Terminal-Ästhetik, KaTeX, Syntax-Highlighting)
lernos review --web

# Alle Topics anzeigen (paginiert)
lernos list

# Volltextsuche
lernos search "Kettenregel"

# Wissensgraph im Browser
lernos graph

# Prüfungsplan (topologisch sortiert)
lernos export --module "Analysis I" --days 14

# Anki-Deck importieren
lernos import-anki deck.apkg

# Letztes Review rückgängig machen
lernos undo

# Streak und Lernfortschritt ansehen
lernos stats

# Tiefenanalyse eines einzelnen Topics
lernos diagnose "Kettenregel"

# Topologische Review-Reihenfolge (Voraussetzungen zuerst)
lernos review --all --fix-order
```

---

## Befehle

### Kern-Review

| Befehl | Beschreibung |
|--------|-------------|
| `lernos review` | Nächstes fälliges Topic (Standard-Modus) |
| `lernos review <n>` | Bestimmtes Topic (Fuzzy-Search) |
| `lernos review --all` | Alle fälligen Topics in einer Session |
| `lernos review --module X` | Nur Topics eines bestimmten Moduls |
| `lernos review --active` | Active-Recall: Antwort eintippen, KI bewertet |
| `lernos review --questions` | Fragen-Modus: aus angehängten PDFs |
| `lernos review --limit 10` | Maximal N Karten pro Session |
| `lernos review --time 20` | Zeitlimit in Minuten |
| `lernos review --web` | Web-Review im Browser (Port 5700) |
| `lernos review --web --port 8080` | Web-Review auf eigenem Port |
| `lernos review --web --output ~/r.html` | Web-Interface als HTML-Datei speichern |
| `lernos review --all --fix-order` | Topologische Reihenfolge (Voraussetzungen zuerst) |
| `lernos review --all --fix-order --active` | Topo-Reihenfolge + Active-Recall |

### Topics verwalten

| Befehl | Beschreibung |
|--------|-------------|
| `lernos add <n>` | Neues Topic + Embedding + Edge-Dialog |
| `lernos add <n> --auto` | Automatische Kantenerstellung via Semantic Search |
| `lernos edit <n>` | Name, Modul oder Beschreibung eines Topics ändern |
| `lernos edit <n> --name X --module Y` | Felder direkt ohne interaktiven Dialog setzen |
| `lernos edit-batch` | Massenbearbeitung: ganzes Modul umbenennen oder Zustand setzen |
| `lernos undo <n>` | Letztes Review eines Topics rückgängig machen |
| `lernos list` | Alle Topics (automatisch paginiert) |
| `lernos list --due` | Nur fällige Topics |
| `lernos list --state REVIEW` | Nach Zustand filtern |
| `lernos list --page 2` | Seite 2 anzeigen |
| `lernos delete <n>` | Topic + alle Kanten, PDFs, Fragen löschen |
| `lernos freeze <n>` | Topic N Tage pausieren (Standard: 6) |
| `lernos unfreeze <n>` | Topic sofort reaktivieren |

### Suche & Import

| Befehl | Beschreibung |
|--------|-------------|
| `lernos search "Begriff"` | Volltextsuche: Topics, Fragen, PDFs |
| `lernos search "Begriff" --in-pdfs` | Auch PDF-Volltexte durchsuchen |
| `lernos search "Begriff" --module X` | Suche auf Modul begrenzen |
| `lernos import-csv datei.csv` | Topics aus CSV importieren |
| `lernos import-anki deck.apkg` | Anki-Deck importieren (Karten + Decks) |
| `lernos import-anki deck.apkg --dry-run` | Vorschau ohne Import |

### PDF-Dokumente

| Befehl | Beschreibung |
|--------|-------------|
| `lernos doc attach <topic> datei.pdf` | PDF anhängen + Fragen generieren |
| `lernos doc attach ... --count 10` | Anzahl generierter Fragen festlegen |
| `lernos doc attach ... --model phi3` | Ollama Text-Modell wählen |
| `lernos doc attach ... --vision` | Vision-Modell für Präsentations-PDFs (auto-erkennt) |
| `lernos doc attach ... --vision-model llava` | Spezifisches Vision-Modell wählen |
| `lernos doc attach ... --dpi 150` | Rendering-Auflösung (72 / 96 / 150, Standard: 96) |
| `lernos doc list <topic>` | Alle PDFs eines Topics anzeigen |
| `lernos doc open <topic>` | Angehängte PDF im System-Viewer öffnen |
| `lernos doc questions <topic>` | Generierte Fragen anzeigen |
| `lernos doc questions --regenerate` | Fragen neu generieren |
| `lernos doc questions --regenerate --vision` | Neu generieren mit Vision-Modell |
| `lernos doc review <topic>` | Fragen-Session (unabhängig von SM-2) |
| `lernos doc remove <doc_id>` | Dokument + Fragen entfernen |

### Graph & Planung

| Befehl | Beschreibung |
|--------|-------------|
| `lernos graph` | D3.js Wissensgraph im Browser öffnen |
| `lernos export --module X` | Topologisch sortierter Prüfungsplan |
| `lernos export --days 14` | Priorität für N Tage bis zur Prüfung |
| `lernos edge add A B` | Kante anlegen (A → Voraussetzung für B) |
| `lernos edge list <n>` | Alle Kanten eines Topics |
| `lernos edge delete A B` | Kante löschen |
| `lernos edge cleanup` | Schwache Kanten via Vektorähnlichkeit finden |
| `lernos stats` | 7-Tage-Statistiken inkl. Heatmap |
| `lernos stats --month` | 30-Tage-Statistiken |
| `lernos diagnose <topic>` | Tiefenanalyse: EF-Verlauf, Konfidenz-Matrix, Empfehlung |

### Backup & Wiederherstellung

| Befehl | Beschreibung |
|--------|-------------|
| `lernos backup` | Datenbank + PDFs als ZIP sichern |
| `lernos backup --output ~/backup.zip` | Zieldatei festlegen |
| `lernos restore backup.zip` | Backup wiederherstellen |

### Benachrichtigungen & Konfiguration

| Befehl | Beschreibung |
|--------|-------------|
| `lernos notify` | Tagesplan via Telegram senden |
| `lernos notify --dry-run` | Vorschau ohne Senden |
| `lernos config` | Telegram, Ollama, Speicherpfade konfigurieren |
| `lernos config --show` | Aktuelle Konfiguration anzeigen |
| `lernos config --test-telegram` | Telegram-Verbindung testen |
| `lernos install-scheduler` | systemd-Timer (Linux) oder LaunchAgent (macOS) |
| `lernos install-completion` | Shell-Tab-Completion installieren (bash/zsh/fish) |

**Fuzzy-Search:** Alle Befehle mit Topic-Namen unterstützen unscharfe Eingabe:
```bash
lernos review taylr       # → findet "Taylorreihen"
lernos freeze difbar      # → findet "Differenzierbarkeit"
lernos edge list ketten   # → findet "Kettenregel"
```

---

## Review-Workflow

Jedes Review benötigt nur **2 Eingaben**:

1. **Konfidenz vorher** (1–5): Wie sicher warst du, bevor du die Antwort gesehen hast?
2. **Grade** (0–5): Wie gut war deine Antwort, nachdem du die Musterantwort gesehen hast?

Die Korrektheit (`grade ≥ 3`) wird intern abgeleitet — keine dritte Eingabe nötig.

### Modi

| Modus | Befehl | Besonderheit |
|-------|--------|-------------|
| Standard | `lernos review` | 2 Eingaben, schnell |
| Active-Recall | `lernos review --active` | Antwort eintippen → KI oder Lokal bewertet |
| Fragen | `lernos review --questions` | KI-generierte Fragen aus PDFs |
| Fragen (standalone) | `lernos doc review <topic>` | Fragen-Session ohne SM-2-Update |
| Web-Review | `lernos review --web` | Browser-Interface mit KaTeX + Syntax-Highlighting |

Im **Active-Recall-Modus** bewertet LernOS deine getippte Antwort automatisch:
- Mit Ollama (phi3): Semantische Bewertung, zeigt `(KI ✓)` an
- Ohne Ollama / bei Timeout: Lokaler Jaccard-Fallback, zeigt `(Lokal)` oder `(KI Timeout →Fallback)` an
- Bei Ollama OOM: Zeigt `(KI OOM →Fallback)` — kein stilles Versagen

### Topologische Reihenfolge (--fix-order)

```bash
lernos review --all --fix-order              # Voraussetzungen vor abhängigen Topics
lernos review --all --fix-order --active     # Kombinierbar mit allen anderen Flags
lernos review --module "Analysis I" --fix-order --all
```

Normalerweise sortiert LernOS nach Fälligkeitsdatum (SM-2). Mit `--fix-order` wird stattdessen topologisch sortiert: Topics die Voraussetzung für andere sind, erscheinen zuerst.

Beispiel ohne `--fix-order` (SM-2-Priorität):
```
1. Differenzierbarkeit  [LEARNING, heute fällig]
2. Stetigkeit           [REVIEW, gestern fällig]
3. Grenzwerte           [NEW]
```

Beispiel mit `--fix-order` (Wissensgraph-Reihenfolge):
```
1. Grenzwerte           → Voraussetzung für Stetigkeit
2. Stetigkeit           → Voraussetzung für Differenzierbarkeit
3. Differenzierbarkeit  → kann jetzt sinnvoll gelernt werden
```

Bei gleicher topologischer Position entscheidet weiterhin die SM-2-Priorität (LEARNING vor REVIEW vor NEW). Zykeln im Graphen werden graceful behandelt — alle Topics werden zurückgegeben.

### Web-Review

```bash
lernos review --web                    # Standard-Modus, Port 5700
lernos review --web --active           # Active-Recall im Browser
lernos review --web --questions        # PDF-Fragen im Browser
lernos review --web --port 8080        # Eigener Port
lernos review --web --output ~/r.html  # Interface als Datei speichern
```

Das Web-Interface nutzt eine Terminal-Ästhetik (Tokyo Night, Monospace) und bietet:
- **KaTeX** für LaTeX-Formeln (`$...$`, `$$...$$`)
- **Highlight.js** für Code-Blöcke mit Syntax-Highlighting
- **Markdown-Rendering** für Fragen und Antworten
- Vollständige Tastatursteuerung (1–5 für Konfidenz, 0–5 für Grade, Enter für Weiter)
- SM-2-Feedback mit EF-Visualisierung und Kaskadeninfo

---

## Architektur

### Zustandsmaschine

```
NEW → REVIEW → MASTERED → FROZEN (6d) ⟳
  ↘       ↓                ↓
   LEARNING ←──────────────┘
```

| Zustand | Bedeutung | Bedingung für Wechsel |
|---------|-----------|----------------------|
| NEW | Noch nie gelernt | — |
| LEARNING | Aktives Lernen (Fehler) | grade < 3 oder NEW + falsch |
| REVIEW | Planmäßige Wiederholung | LEARNING + grade ≥ 3 + ≥ 2 Wdh. |
| MASTERED | Gemeistert | Intervall ≥ 21d **und** EF ≥ 2.0 |
| FROZEN | Pausiert | manuell via `lernos freeze` |

### SM-2 + Confidence-Interval-Modell

Pro Review werden **2 Werte** abgefragt:

| Kombination | Effekt auf Grade |
|-------------|-----------------|
| Hohe Konfidenz (4–5) + falsch (grade < 3) | **−2** (Overconfidence-Strafe) |
| Niedrige/mittlere Konfidenz + falsch | −1 |
| Richtig (grade ≥ 3) | kein Modifier |

### Ease-Hell-Schutz

Topics die wiederholt auf LEARNING zurückfallen, erhalten einen weicheren EF-Boden:

- **Standard-Boden:** EF ≥ 1.3
- **Nach ≥ 3 Rückfällen:** EF-Boden steigt auf 1.5 (Ease-Hell-Dämpfung)
- **Recovery-Boost:** Grade 5 bei EF < 2.0 gibt +0.05 extra

### Kaskadierende Wiederholung (gestaffelt, 1 Ebene)

Wenn Topic A auf LEARNING fällt, werden nur direkte Abhängigkeiten berührt:

| Kantengewicht | Effekt auf abhängiges Topic B |
|---------------|------------------------------|
| ≥ 0.6 | B → REVIEW |
| ≥ 0.8 + B ist MASTERED | B → LEARNING |

**Wichtig:** Nur eine Ebene tief — C wird erst berührt wenn B beim nächsten Review ebenfalls fehlschlägt. FROZEN-Topics werden nie kaskadiert.

### Semantische Verknüpfung (mit Ollama)

Beim `lernos add` wird automatisch:
1. Embedding via `nomic-embed-text` geholt (768 Dimensionen)
2. Top-5 ähnlichste Topics per Kosinus-Ähnlichkeit gefunden
3. Ab Ähnlichkeit ≥ 0.40: phi3 als Schiedsrichter befragt
4. Ab Ähnlichkeit ≥ 0.78 + `--auto`: direkte Verknüpfung ohne Rückfrage

Vor jeder Kantenerstellung: Zykel-Erkennung via DFS (verhindert A→B→C→A).

---

## PDF-Integration

### Text-basierte PDFs (Standard)

```bash
# PDF anhängen — generiert automatisch Fragen (phi3 oder Heuristik)
lernos doc attach "Kettenregel" analysis_skript.pdf --count 10

# Fragen anzeigen
lernos doc questions "Kettenregel"

# Fragen-Review (standalone)
lernos doc review "Kettenregel"

# Fragen in Haupt-Review einbinden
lernos review "Kettenregel" --questions

# PDF im System-Viewer öffnen
lernos doc open "Kettenregel"
```

### Präsentations-PDFs (Vision-Modus)

Vorlesungsfolien haben oft wenig Text pro Seite, Stichpunkte ohne Satzzeichen und komplexe Layouts mit Diagrammen. LernOS erkennt solche PDFs automatisch und wählt die beste Verarbeitungsstrategie.

```bash
# Vision-Modell: "sieht" das Folienlayout wie ein Mensch
lernos doc attach "Analysis" vorlesung.pdf --vision

# Höhere Auflösung für Formeln und Diagramme
lernos doc attach "Analysis" vorlesung.pdf --vision --dpi 150

# Spezifisches Vision-Modell wählen
lernos doc attach "Analysis" vorlesung.pdf --vision --vision-model llava-phi3

# Fragen regenerieren mit Vision
lernos doc questions "Analysis" --regenerate --vision
```

### Fallback-Kaskade bei Fragen-Generierung

LernOS wählt automatisch die beste verfügbare Methode:

```
Präsentation erkannt:
  1. Vision-LLM  → llava/llama3.2-vision sieht Layout + Formeln + Diagramme
  2. Text-LLM    → phi3 mit folienweisem Chunking (à 2000 Zeichen)
  3. Heuristik   → Folientitel + Bulletpoints → strukturierte Fragen

Fließtext:
  1. Text-LLM    → phi3
  2. Heuristik   → Schlüsselsatz-Extraktion
```

### Verfügbare Vision-Modelle (Priorität)

| Modell | Größe | Stärke |
|--------|-------|--------|
| `llama3.2-vision` | ~8 GB | Beste Qualität, aktuell |
| `llava-phi3` | ~3 GB | Klein, schnell |
| `llava` | ~4 GB | Klassisch, weit verbreitet |
| `llava:13b` | ~8 GB | Größere Variante |

### Fehlerbehandlung

| Fehler | Meldung | Lösung |
|--------|---------|--------|
| Passwortschutz | `PDFPasswordError` | `qpdf --decrypt input.pdf output.pdf` |
| Gescannte PDF | `PDFEmptyError` | `ocrmypdf datei.pdf datei_ocr.pdf` |
| Korrupte Datei | `PDFCorruptError` | Datei neu herunterladen |

LernOS warnt automatisch wenn:
- Wenig Text pro Seite erkannt wird (`📊 Präsentation erkannt — nutze folienweise Verarbeitung`)
- LaTeX-Formeln im Text gefunden werden
- Das Dokument bildlastig erscheint (< 80 Zeichen/Seite)

---

## Anki-Import

```bash
# Deck importieren (Front = Topic-Name, Back = Beschreibung, Deck = Modul)
lernos import-anki Mathematik.apkg

# Vorschau ohne Import
lernos import-anki Mathematik.apkg --dry-run

# Modul-Namen überschreiben
lernos import-anki deck.apkg --module "Analysis II"

# Nur erste 50 Karten
lernos import-anki deck.apkg --limit 50
```

**Unterstützt:**
- Anki 2.x und Anki 21 (`.anki2`, `.anki21`)
- Cloze-Felder (`{{c1::Begriff}}` → `Begriff`)
- HTML-Bereinigung (Tags, Entities, `<br>`)
- Deck-Hierarchien (`Mathe::Analysis::Grenzwerte` → Modul: `Grenzwerte`)
- Tags als Metadaten

**Nicht unterstützt:** Medien (Bilder, Audio) — nur Text wird importiert.

---

## Topics bearbeiten

### Einzelnes Topic

```bash
# Interaktiver Dialog
lernos edit "Grenzwerte"

# Direkt ohne Dialog
lernos edit "Grenzwerte" --name "Grenzwerte & Limes" --module "Analysis II"
lernos edit "Grenzwerte" --desc "Formale Definition via epsilon-delta"
```

### Massenbearbeitung

```bash
# Modul umbenennen (alle Topics)
lernos edit-batch --rename-module --module-old "Analysis I" --module-new "Analysis"

# Zustand aller Topics eines Moduls setzen
lernos edit-batch --module "Analysis" --state REVIEW

# Mit Bestätigung überspringen
lernos edit-batch --module "Analysis" --state NEW --yes
```

### Review rückgängig machen

```bash
# Letztes Review eines Topics zurücksetzen (bis zu 60 Minuten rückwirkend)
lernos undo "Grenzwerte"

# Zeitfenster anpassen (in Minuten)
lernos undo "Grenzwerte" --max-age 120
```

---

## Volltextsuche

```bash
# Basis-Suche (Topics + Fragen)
lernos search "Eigenwert"

# Auch PDF-Volltexte durchsuchen
lernos search "Eigenwert" --in-pdfs

# Auf Modul begrenzen
lernos search "Kettenregel" --module "Analysis I"

# Mehr Ergebnisse
lernos search "Ableitung" --limit 50
```

Ergebnisse werden nach Relevanz sortiert (Name-Treffer > Fragen-Treffer > PDF-Treffer) und mit hervorgehobenen Kontext-Snippets angezeigt.

---

## lernos diagnose

```bash
lernos diagnose "Kettenregel"
lernos diagnose diff   # Fuzzy-Search
```

Zeigt eine Tiefenanalyse eines einzelnen Topics:

- **EF-Verlauf** als Sparkline (▁▂▃▄▅▆▇█) über die letzten 20 Reviews — Stagnation und Verbesserungen sofort sichtbar
- **Konfidenz-Matrix** (5×2): Konfidenz 1–5 × Korrektheit. Deckt Overconfidence-Muster auf ("Du schätzt dich oft 5/5 ein, liegst aber nur 30% richtig")
- **EF-Trend**: Vergleich der letzten 3 Reviews mit den vorherigen 3 — steigt oder fällt die Leichtigkeit?
- **Meistgenutzte Fragen** aus PDFs (Proxy für "oft falsch beantwortet")
- **Klare Empfehlung**: KRITISCH / ACHTUNG / HINWEIS / GUT — basierend auf EF, Resets, Korrektheit, Lernpause

Typische Diagnose-Ausgabe:
```
🔬 Diagnose: Kettenregel
Analysis I  |  LEARNING  |  EF:1.42  |  Intervall:1d

── Übersicht ───────────────────────────────────────
  Reviews gesamt:   12
  Korrektheit:      ████████░░░░░░░░░░░░ 42%  (5/12)
  Ø Konfidenz:      3.8/5
  Learning-Resets:  4
  Nächste Fälligkeit: heute

── EF-Verlauf (neueste → älteste) ─────────────────
  EF    ▁▂▁▂▃▂▁▂▃▂  → aktuell 1.42

── Konfidenz vs. Korrektheit ───────────────────────
  5/5   0 richtig   4 falsch   ░░░░░░░░░░░░  0%  ⚠ Overconfidence!

── Empfehlung ──────────────────────────────────────
  [KRITISCH] EF=1.42 — Ease-Hell. Übe täglich bis EF > 2.0.
  [ACHTUNG]  4 Learning-Resets — Beschreibung überarbeiten?
```

## Statistiken & Heatmap

```bash
lernos stats          # Letzte 7 Tage
lernos stats --month  # Letzte 30 Tage
lernos stats --all    # Gesamter Zeitraum
```

Zeigt:
- Korrektheit, Sessions, Ø Konfidenz, beste Lernzeit
- **🔥 Lern-Streak**: aktueller Streak (Tage in Folge), Rekord, 7-Tage-Minikalender
- **14-Tage-Aktivitäts-Heatmap** im Terminal (`░▒▓█`)
- 14-Tage-Prognose fälliger Reviews (Balkengraph)
- Zustandsverteilung aller Topics
- Schwierigste Topics (niedrigster EF)

Der Streak zählt jeden Tag an dem mindestens eine Review-Session stattgefunden hat. Wenn heute noch nicht gelernt wurde, erscheint eine Warnung: `🔥🔥🔥  3 Tag(e) — heute noch lernen!`

---

## Wissensgraph

```bash
lernos graph
```

Öffnet eine interaktive D3.js-Visualisierung im Browser:

- **Force-Directed Layout** mit Modul-Clustering (jedes Modul bildet eine Insel)
- **Farbkodierung** nach Lernzustand
- **Modul-Filter** (Checkboxen): einzelne Module ein-/ausblenden
- **Cross-Modul-Kanten** gestrichelt dargestellt
- **Tooltips** mit EF, Intervall, Fälligkeit, Wiederholungen, Dokument-Anzahl
- **PDF-Indikator** (blauer Punkt) wenn Dokumente angehängt
- Labels ein-/ausblenden (Button „Labels")
- Zoom, Drag, Layout-Neuberechnung

---

## Backup & Wiederherstellung

```bash
# Backup erstellen (Datenbank + alle PDFs als ZIP)
lernos backup
lernos backup --output ~/LernOS_Backup_2025.zip

# Backup wiederherstellen
lernos restore LernOS_Backup_2025.zip
```

Rolling-Backups werden außerdem täglich automatisch in `~/.lernos_backups/` angelegt (max. 5 Versionen, älteste wird überschrieben).

---

## Tab-Completion

```bash
# Completion installieren
lernos install-completion          # interaktiver Dialog (bash/zsh/fish)
lernos install-completion --shell zsh
lernos install-completion --print-only   # Ausgabe ohne Installation

# Shell neu laden
source ~/.bashrc   # oder ~/.zshrc
```

Nach der Installation vervollständigt Tab alle Befehle, Flags und Topic-Namen.

---

## Konfiguration & Sync

```bash
lernos config
```

Konfigurierbar:
- **Telegram Bot Token + Chat-ID** für Benachrichtigungen
- **Ollama URL** (Standard: `http://localhost:11434`)
- **Ollama Modell** (Standard: `phi3`)
- **Datenbankpfad** (`db_path`) — für Sync via Nextcloud, Dropbox, iCloud
- **Dokumente-Verzeichnis** (`docs_path`) — für Sync der angehängten PDFs

### Sync-Einrichtung (Nextcloud-Beispiel)

```bash
lernos config
# → Datenbankpfad: ~/Nextcloud/LernOS/lernos.db
# → Dokumente-Verzeichnis: ~/Nextcloud/LernOS/docs

# Bestehende Daten einmalig verschieben
mv ~/.lernosdb ~/Nextcloud/LernOS/lernos.db
cp -r ~/.lernos_docs ~/Nextcloud/LernOS/docs
```

---

## Tägliche Benachrichtigungen

```bash
lernos config               # Telegram-Bot einrichten
lernos config --test-telegram
lernos notify --dry-run     # Vorschau
lernos notify               # Senden
```

### Linux (systemd)

```bash
lernos install-scheduler
systemctl --user daemon-reload
systemctl --user enable --now lernos.timer
```

### macOS (LaunchAgent)

```bash
lernos install-scheduler    # erstellt plist interaktiv
launchctl load ~/Library/LaunchAgents/com.lernos.daily.plist
```

Der Timer läuft täglich um 08:00 Uhr, reaktiviert abgelaufene FROZEN-Topics und sendet eine priorisierte Telegram-Zusammenfassung (LEARNING-Topics zuerst).

---

## Datenbank & Backup

```bash
~/.lernosdb          # Datenbank (Standard)
~/.lernos_docs/      # Angehängte PDFs (Standard)
~/.lernos_backups/   # Rolling-Backups (täglich, max. 5 Versionen)

# Manuell einsehen
sqlite3 ~/.lernosdb "SELECT name, state, ef, interval_d, due_date FROM topics;"
```

### Datenbankstruktur (Schema v2)

| Tabelle | Inhalt |
|---------|--------|
| `topics` | Topics mit SM-2-Feldern, Embedding, `learning_resets` |
| `edges` | Gerichtete Kanten (Voraussetzungen) mit Gewicht |
| `sessions` | Review-History (Grade, Konfidenz, EF-Verlauf) |
| `documents` | Angehängte PDF-Dateien mit Volltext + Seitenstruktur |
| `generated_questions` | KI-generierte Fragen + Antworten |
| `notifications` | Telegram-Versand-Log |

---

## Prüfungsplan

```bash
lernos export --module "Analysis I" --days 14
```

Topologische Sortierung (Kahn's Algorithmus) kombiniert mit SM-2-Priorität:

```
LEARNING (SEHR HOCH) → NEW (HOCH) → REVIEW (MITTEL) → MASTERED (NIEDRIG)
```

---

## Tests

```bash
pip install pytest
python3 -m pytest tests/ -v
```

**39 Unit-Tests** (Core SM-2) **+ 28 Feature-Tests** (Streak, Diagnose, Topo-Sort): SM-2, Zustandsmaschine, Kaskaden, Zykel-Erkennung, Freeze/Unfreeze, Fuzzy-Search, Topic-CRUD, Kanten-Verwaltung, topologische Sortierung, Prüfungsplan-Prioritäten.

---

## Lizenz

MIT
