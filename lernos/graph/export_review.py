"""
LernOS — Web-Review HTML + lokaler Review-Server

Architektur:
  lernos review --web
    → startet ReviewServer (http.server + BaseHTTPRequestHandler)
    → öffnet file://... im Browser
    → Browser pollt GET /api/card  → bekommt aktuelle Karte als JSON
    → Browser postet POST /api/grade {grade, confidence}
    → Server wendet SM-2 an, schickt neues feedback JSON
    → Browser zeigt Feedback, pollt nächste Karte
    → Server sendet {"done": true} wenn Session beendet

Design:
  - Vollständiges Terminal-Aesthetic (Monospace, grüne Akzente, Scanlines)
  - KaTeX für LaTeX-Formeln: $x^2$, $$\frac{d}{dx}$$
  - Highlight.js für Code-Blöcke: ```python ... ```
  - Marked.js für Markdown in Beschreibungen
  - Keyboard-only bedienbar: 1-5 für Grade, c für Konfidenz, ENTER aufdecken
  - Active-Recall: Textarea mit CTRL+ENTER zum Absenden
"""
from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# HTML / CSS / JS — das vollständige Review-Interface
# ─────────────────────────────────────────────────────────────────────────────

REVIEW_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LernOS — Review</title>

<!-- KaTeX für Formeln -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css">
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js"></script>
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/contrib/auto-render.min.js"></script>

<!-- Highlight.js für Code -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/tokyo-night-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>

<!-- Marked für Markdown -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>

<style>
/* ── Reset + Terminal Base ────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:        #0a0e14;
  --bg2:       #0d1117;
  --bg3:       #161b22;
  --border:    #21262d;
  --border2:   #30363d;
  --green:     #39d353;
  --green-dim: #196127;
  --cyan:      #58a6ff;
  --yellow:    #e3b341;
  --red:       #f85149;
  --purple:    #bc8cff;
  --text:      #e6edf3;
  --text-dim:  #8b949e;
  --text-dim2: #484f58;
  --mono:      'JetBrains Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace;

  /* State colors */
  --s-new:      #475569;
  --s-learning: #dc2626;
  --s-review:   #2563eb;
  --s-mastered: #16a34a;
  --s-frozen:   #7c3aed;
}

html, body {
  height: 100%; background: var(--bg);
  color: var(--text); font-family: var(--mono);
  font-size: 14px; line-height: 1.6;
  overflow-x: hidden;
}

/* Subtle scanlines */
body::before {
  content: ''; position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background: repeating-linear-gradient(
    to bottom,
    transparent 0px, transparent 3px,
    rgba(0,0,0,0.03) 3px, rgba(0,0,0,0.03) 4px
  );
}

/* ── Layout ───────────────────────────────────────────────────────────────── */
#app {
  position: relative; z-index: 1;
  max-width: 860px; margin: 0 auto;
  padding: 0 20px 120px;
  min-height: 100vh;
  display: flex; flex-direction: column;
}

/* ── Header ───────────────────────────────────────────────────────────────── */
#topbar {
  position: sticky; top: 0; z-index: 100;
  background: rgba(10,14,20,0.96); backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border);
  padding: 12px 0; display: flex; align-items: center;
  justify-content: space-between; gap: 16px;
}

#topbar .logo {
  font-size: 16px; font-weight: 700; letter-spacing: -0.5px;
  color: var(--text);
}
#topbar .logo span { color: var(--green); }

#topbar .session-info {
  display: flex; align-items: center; gap: 20px;
  font-size: 12px; color: var(--text-dim);
}

#progress-bar-wrap {
  width: 180px; height: 4px;
  background: var(--border2); border-radius: 2px; overflow: hidden;
}
#progress-bar-fill {
  height: 100%; background: var(--green);
  border-radius: 2px; transition: width 0.4s ease;
}

#timer { font-variant-numeric: tabular-nums; }
#mode-badge {
  padding: 2px 8px; border-radius: 3px;
  background: var(--border2); font-size: 11px;
  color: var(--text-dim); letter-spacing: 0.5px;
}

/* ── Prompt Line (à la terminal) ──────────────────────────────────────────── */
.prompt-line {
  font-size: 12px; color: var(--green-dim);
  margin-top: 24px; margin-bottom: 6px;
}
.prompt-line::before { content: '$ '; color: var(--green); }

/* ── Card ─────────────────────────────────────────────────────────────────── */
#card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
  flex: 1;
}

/* Card header bar */
#card-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 18px;
  background: var(--bg3);
  border-bottom: 1px solid var(--border);
}

#card-title {
  font-size: 20px; font-weight: 700; letter-spacing: -0.3px;
  color: var(--text); flex: 1;
}

.card-meta {
  display: flex; align-items: center; gap: 12px;
  font-size: 12px; color: var(--text-dim);
}

.state-badge {
  padding: 3px 10px; border-radius: 3px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.5px;
  color: #fff;
}

#card-body { padding: 24px 24px 20px; }

/* Meta row: module, EF, interval */
#meta-row {
  display: flex; gap: 20px; flex-wrap: wrap;
  font-size: 12px; color: var(--text-dim);
  margin-bottom: 20px; padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
#meta-row .meta-item { display: flex; gap: 6px; }
#meta-row .meta-label { color: var(--text-dim2); }
#meta-row .meta-val   { color: var(--text-dim); font-weight: 600; }

/* Prerequisites */
#prereqs {
  font-size: 12px; color: var(--text-dim);
  margin-bottom: 16px; display: none;
}
#prereqs .pre-label { color: var(--text-dim2); margin-right: 6px; }
.prereq-chip {
  display: inline-block; padding: 1px 8px;
  border-radius: 3px; margin: 2px 3px 2px 0;
  font-size: 11px; font-weight: 600;
  background: var(--border2); color: var(--text-dim);
}

/* ── Content Area (Question / Answer) ────────────────────────────────────── */
#content-front, #content-back {
  font-size: 15px; line-height: 1.8; color: var(--text);
  min-height: 80px;
}

/* Markdown rendered content */
.md-content h1, .md-content h2, .md-content h3 {
  color: var(--cyan); margin: 16px 0 8px; font-weight: 700;
}
.md-content p  { margin-bottom: 12px; }
.md-content ul, .md-content ol {
  padding-left: 24px; margin-bottom: 12px;
}
.md-content li { margin-bottom: 4px; }
.md-content strong { color: var(--yellow); }
.md-content em     { color: var(--purple); }
.md-content blockquote {
  border-left: 3px solid var(--green-dim);
  padding: 8px 16px; margin: 12px 0;
  background: var(--bg3); border-radius: 0 4px 4px 0;
  color: var(--text-dim);
}
.md-content table {
  width: 100%; border-collapse: collapse; margin: 12px 0;
  font-size: 13px;
}
.md-content th {
  background: var(--bg3); padding: 8px 12px;
  border: 1px solid var(--border2); color: var(--cyan);
  text-align: left;
}
.md-content td {
  padding: 7px 12px; border: 1px solid var(--border);
}
.md-content tr:nth-child(even) td { background: rgba(255,255,255,0.02); }
.md-content hr {
  border: none; border-top: 1px solid var(--border2); margin: 16px 0;
}

/* Code blocks (via highlight.js) */
.md-content pre {
  margin: 12px 0; border-radius: 4px;
  border: 1px solid var(--border2);
  overflow-x: auto; position: relative;
}
.md-content pre code {
  font-family: var(--mono) !important;
  font-size: 13px !important; padding: 14px 16px !important;
  display: block;
}
/* Language label */
.md-content pre::before {
  content: attr(data-lang);
  position: absolute; top: 6px; right: 10px;
  font-size: 10px; color: var(--text-dim2);
  font-family: var(--mono); letter-spacing: 0.5px;
  text-transform: uppercase;
}
/* Inline code */
.md-content code:not(pre code) {
  font-family: var(--mono); font-size: 13px;
  background: var(--bg3); color: var(--green);
  padding: 1px 6px; border-radius: 3px;
  border: 1px solid var(--border2);
}

/* KaTeX display math */
.katex-display {
  overflow-x: auto; overflow-y: hidden;
  padding: 10px 0;
}

/* ── Divider (Aufdecken) ──────────────────────────────────────────────────── */
#divider {
  display: none;
  margin: 20px 0;
  border: none; border-top: 1px dashed var(--border2);
  position: relative;
}
#divider::after {
  content: ' Musterantwort ';
  position: absolute; top: -9px; left: 50%;
  transform: translateX(-50%);
  background: var(--bg2);
  padding: 0 12px;
  font-size: 11px; color: var(--text-dim2);
  letter-spacing: 1px;
}
#content-back { display: none; }

/* ── Active Recall Input ──────────────────────────────────────────────────── */
#active-input-wrap {
  margin: 20px 0; display: none;
}
#active-input-wrap label {
  font-size: 11px; color: var(--text-dim2);
  display: block; margin-bottom: 6px;
  letter-spacing: 0.5px;
}
#active-input-wrap label::before { content: '// '; color: var(--green-dim); }

#active-textarea {
  width: 100%; min-height: 90px;
  background: var(--bg3); border: 1px solid var(--border2);
  border-radius: 4px; color: var(--text);
  font-family: var(--mono); font-size: 13px;
  padding: 10px 14px; resize: vertical;
  outline: none; transition: border-color 0.2s;
  line-height: 1.6;
}
#active-textarea:focus { border-color: var(--green-dim); }
#active-textarea::placeholder { color: var(--text-dim2); }

/* AI Grade suggestion */
#ai-suggestion {
  display: none; margin: 10px 0;
  padding: 8px 14px;
  background: var(--bg3); border: 1px solid var(--border2);
  border-radius: 4px; border-left: 3px solid var(--cyan);
  font-size: 12px; color: var(--text-dim);
}
#ai-suggestion .ai-grade { color: var(--cyan); font-weight: 700; }
#ai-suggestion .ai-source { color: var(--text-dim2); font-size: 11px; }

/* ── Input Panel ──────────────────────────────────────────────────────────── */
#input-panel {
  background: var(--bg);
  border-top: 1px solid var(--border);
  padding: 16px 0 0;
  position: sticky; bottom: 0; z-index: 50;
}

/* Konfidenz / Grade rows */
.input-row {
  display: flex; gap: 8px; align-items: center;
  margin-bottom: 10px; flex-wrap: wrap;
}
.input-label {
  font-size: 11px; color: var(--text-dim2);
  width: 120px; flex-shrink: 0;
  letter-spacing: 0.5px;
}
.input-label .step { color: var(--green); }

.key-btn {
  display: flex; flex-direction: column;
  align-items: center; gap: 2px;
  background: var(--bg3); border: 1px solid var(--border2);
  border-radius: 4px; padding: 8px 14px;
  cursor: pointer; transition: all 0.12s;
  min-width: 72px; font-family: var(--mono);
  color: var(--text-dim);
}
.key-btn:hover    { border-color: var(--green-dim); color: var(--text); }
.key-btn.active   { border-color: var(--green); background: rgba(57,211,83,0.08); color: var(--green); }
.key-btn.selected { border-color: var(--cyan); background: rgba(88,166,255,0.1); color: var(--cyan); }
.key-btn .key-num  { font-size: 16px; font-weight: 700; }
.key-btn .key-desc { font-size: 10px; color: inherit; text-align: center; line-height: 1.3; }

/* Reveal / Submit button */
#reveal-btn, #submit-btn {
  width: 100%; margin-top: 6px;
  padding: 12px;
  background: var(--bg3); border: 1px solid var(--green-dim);
  border-radius: 4px; color: var(--green);
  font-family: var(--mono); font-size: 13px; font-weight: 700;
  cursor: pointer; letter-spacing: 0.5px;
  transition: all 0.15s;
  display: flex; align-items: center; justify-content: center; gap: 10px;
}
#reveal-btn:hover, #submit-btn:hover {
  background: rgba(57,211,83,0.08);
  border-color: var(--green);
}
#submit-btn { display: none; }

.key-hint {
  font-size: 10px; color: var(--text-dim2);
  margin-left: 6px;
}
.key-hint kbd {
  background: var(--border2); border: 1px solid var(--border2);
  border-radius: 2px; padding: 1px 5px; font-family: var(--mono);
  font-size: 10px;
}

/* ── Feedback Panel ───────────────────────────────────────────────────────── */
#feedback {
  display: none;
  padding: 18px 24px;
  background: var(--bg2); border: 1px solid var(--border);
  border-radius: 6px; margin-top: 12px;
}

.fb-result {
  font-size: 18px; font-weight: 700; margin-bottom: 16px;
}
.fb-correct   { color: var(--green); }
.fb-wrong     { color: var(--red); }
.fb-overconf  { color: var(--yellow); }

.fb-grid {
  display: grid; grid-template-columns: auto 1fr;
  gap: 6px 20px; font-size: 13px;
  margin-bottom: 16px;
}
.fb-key { color: var(--text-dim2); }
.fb-val { color: var(--text-dim); }
.fb-val .arrow { color: var(--text-dim2); margin: 0 6px; }
.fb-val .from  { color: var(--text-dim2); }
.fb-val .to    { color: var(--text); font-weight: 600; }

/* EF bar */
.ef-bar-wrap { display: flex; align-items: center; gap: 8px; }
.ef-bar {
  height: 4px; border-radius: 2px;
  background: var(--border2); flex: 1; overflow: hidden;
}
.ef-bar-fill { height: 100%; border-radius: 2px; transition: width 0.5s; }

/* Cascade */
#cascade-info {
  display: none;
  margin-top: 14px; padding: 10px 14px;
  background: rgba(227,179,65,0.06);
  border: 1px solid rgba(227,179,65,0.2);
  border-radius: 4px; border-left: 3px solid var(--yellow);
}
#cascade-info .cascade-title {
  font-size: 12px; color: var(--yellow); font-weight: 700;
  margin-bottom: 8px;
}
.cascade-item {
  font-size: 12px; color: var(--text-dim); margin-bottom: 4px;
}
.cascade-item::before { content: '→ '; color: var(--yellow); }

#next-btn {
  width: 100%; margin-top: 14px;
  padding: 12px; background: var(--bg3);
  border: 1px solid var(--border2); border-radius: 4px;
  color: var(--text-dim); font-family: var(--mono);
  font-size: 13px; cursor: pointer;
  transition: all 0.15s;
  display: flex; align-items: center; justify-content: center; gap: 10px;
}
#next-btn:hover { border-color: var(--border); color: var(--text); }

/* ── Done Screen ──────────────────────────────────────────────────────────── */
#done-screen {
  display: none; flex: 1;
  flex-direction: column; align-items: center; justify-content: center;
  padding: 60px 20px; text-align: center;
}
#done-screen .done-logo {
  font-size: 48px; margin-bottom: 20px; filter: grayscale(0.3);
}
#done-screen h2 {
  font-size: 22px; font-weight: 700; margin-bottom: 8px; color: var(--text);
}
#done-screen .done-sub { font-size: 14px; color: var(--text-dim); margin-bottom: 30px; }

.done-stats {
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 1px; background: var(--border);
  border: 1px solid var(--border); border-radius: 6px;
  overflow: hidden; width: 100%; max-width: 480px; margin-bottom: 30px;
}
.done-stat {
  background: var(--bg2); padding: 16px;
  text-align: center;
}
.done-stat .ds-val { font-size: 28px; font-weight: 700; color: var(--green); }
.done-stat .ds-label { font-size: 11px; color: var(--text-dim2); margin-top: 4px; letter-spacing: 0.5px; }

#done-screen .close-hint {
  font-size: 12px; color: var(--text-dim2);
}
#done-screen .close-hint kbd {
  background: var(--border2); border: 1px solid var(--border2);
  border-radius: 2px; padding: 2px 6px;
}

/* ── Loading ──────────────────────────────────────────────────────────────── */
#loading {
  position: fixed; inset: 0; z-index: 200;
  background: var(--bg);
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 14px;
}
#loading .loading-logo {
  font-size: 18px; font-weight: 700; letter-spacing: -0.5px;
}
#loading .loading-logo span { color: var(--green); }
.blink { animation: blink 1s step-end infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
.loading-msg { font-size: 12px; color: var(--text-dim); }

/* ── Scrollbar ────────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border); }

/* ── Util ─────────────────────────────────────────────────────────────────── */
.hidden  { display: none !important; }
.dim     { color: var(--text-dim); }
.mono    { font-family: var(--mono); }
</style>
</head>

<body>

<!-- Loading Overlay -->
<div id="loading">
  <div class="loading-logo">Lern<span>OS</span></div>
  <div class="loading-msg">Verbinde mit Review-Server<span class="blink">_</span></div>
</div>

<div id="app">

  <!-- Topbar -->
  <div id="topbar">
    <div class="logo">Lern<span>OS</span> <span style="color:var(--text-dim2);font-weight:400">review</span></div>
    <div class="session-info">
      <span id="counter">0/0</span>
      <div id="progress-bar-wrap"><div id="progress-bar-fill" style="width:0%"></div></div>
      <span id="timer">00:00</span>
      <span id="mode-badge">—</span>
    </div>
  </div>

  <!-- Card Area -->
  <div id="card-wrap" style="margin-top:20px; flex:1; display:flex; flex-direction:column; gap:12px;">

    <div class="prompt-line" id="prompt-text">lernos review --web</div>

    <div id="card">
      <div id="card-header">
        <div id="card-title">—</div>
        <div class="card-meta">
          <span id="state-badge" class="state-badge">—</span>
        </div>
      </div>
      <div id="card-body">
        <div id="meta-row">
          <div class="meta-item"><span class="meta-label">Modul</span><span class="meta-val" id="m-module">—</span></div>
          <div class="meta-item"><span class="meta-label">EF</span><span class="meta-val" id="m-ef">—</span></div>
          <div class="meta-item"><span class="meta-label">Intervall</span><span class="meta-val" id="m-interval">—</span></div>
          <div class="meta-item"><span class="meta-label">Fällig</span><span class="meta-val" id="m-due">—</span></div>
          <div class="meta-item"><span class="meta-label">Wiederh.</span><span class="meta-val" id="m-reps">—</span></div>
        </div>
        <div id="prereqs">
          <span class="pre-label">Voraussetzungen:</span>
          <span id="prereq-chips"></span>
        </div>
        <div id="content-front" class="md-content"></div>
        <div id="active-input-wrap">
          <label>Deine Antwort  <span class="key-hint"><kbd>Ctrl+Enter</kbd> = senden</span></label>
          <textarea id="active-textarea" placeholder="Stichworte, Formeln, Code — alles erlaubt..."></textarea>
        </div>
        <hr id="divider">
        <div id="content-back" class="md-content"></div>
        <div id="ai-suggestion">
          Vorgeschlagene Bewertung: <span class="ai-grade" id="ai-grade-val">—</span>
          <span id="ai-grade-desc"></span>
          <span class="ai-source" id="ai-source-label"></span>
        </div>

        <!-- Sokratischer Dialog -->
        <div id="socratic-panel" style="display:none;margin-top:14px;">
          <div id="socratic-offer" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
            <span style="color:var(--yellow);💬">&#128172; Antwort unvollständig — sokratischer Tipp?</span>
            <button id="socratic-yes-btn" style="background:rgba(234,179,8,.12);border:1px solid var(--yellow);color:var(--yellow);padding:4px 12px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:13px;">Tipp holen</button>
            <button id="socratic-no-btn" style="background:transparent;border:1px solid var(--border);color:var(--text-dim2);padding:4px 12px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:13px;">Nein danke</button>
          </div>
          <div id="socratic-hint-wrap" style="display:none;margin-top:12px;">
            <div style="color:var(--yellow);font-size:12px;margin-bottom:6px;font-weight:700;">&#128161; Sokratische Rückfrage <span id="socratic-round-label" style="font-weight:400;color:var(--text-dim2);"></span></div>
            <div id="socratic-hint-text" style="color:var(--text);line-height:1.6;white-space:pre-wrap;border-left:2px solid var(--yellow);padding-left:12px;"></div>
            <div style="margin-top:10px;">
              <label style="font-size:12px;color:var(--text-dim2);display:block;margin-bottom:4px;">Verbesserte Antwort (Enter = überspringen):</label>
              <textarea id="socratic-textarea" rows="3" style="width:100%;box-sizing:border-box;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:8px;border-radius:4px;font-family:inherit;font-size:13px;resize:vertical;"></textarea>
              <div style="margin-top:6px;display:flex;gap:8px;">
                <button id="socratic-submit-btn" style="background:rgba(57,211,83,.12);border:1px solid var(--green);color:var(--green);padding:4px 14px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:13px;">Neu bewerten</button>
                <button id="socratic-skip-btn" style="background:transparent;border:1px solid var(--border);color:var(--text-dim2);padding:4px 12px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:13px;">Überspringen</button>
              </div>
            </div>
            <div id="socratic-result" style="display:none;margin-top:8px;font-size:13px;"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Input Panel -->
    <div id="input-panel">

      <!-- Phase 1: Konfidenz (vor Aufdecken) -->
      <div id="phase-confidence">
        <div class="input-row">
          <span class="input-label"><span class="step">1</span> / Konfidenz</span>
          <div id="conf-btns" style="display:flex;gap:8px;flex-wrap:wrap"></div>
          <span class="key-hint">Taste <kbd>1</kbd>–<kbd>5</kbd></span>
        </div>
        <button id="reveal-btn">
          <span>Aufdecken</span>
          <span class="key-hint"><kbd>Enter</kbd></span>
        </button>
      </div>

      <!-- Phase 2: Grade (nach Aufdecken) -->
      <div id="phase-grade" class="hidden">
        <div class="input-row">
          <span class="input-label"><span class="step">2</span> / Bewertung</span>
          <div id="grade-btns" style="display:flex;gap:8px;flex-wrap:wrap"></div>
          <span class="key-hint">Taste <kbd>0</kbd>–<kbd>5</kbd></span>
        </div>
        <button id="submit-btn">
          <span>Abschicken</span>
          <span class="key-hint"><kbd>Enter</kbd></span>
        </button>
      </div>
    </div>

    <!-- Feedback (nach Submit) -->
    <div id="feedback">
      <div class="fb-result" id="fb-result-line"></div>
      <div class="fb-grid" id="fb-grid"></div>
      <div id="cascade-info">
        <div class="cascade-title">⚡ Kaskade ausgelöst (1 Ebene)</div>
        <div id="cascade-items"></div>
      </div>
      <button id="next-btn">
        Nächste Karte <span class="key-hint"><kbd>Enter</kbd> oder <kbd>n</kbd></span>
      </button>
    </div>

  </div><!-- /card-wrap -->

  <!-- Done Screen -->
  <div id="done-screen">
    <div class="done-logo">🏁</div>
    <h2 id="done-title">Session abgeschlossen</h2>
    <div class="done-sub" id="done-sub"></div>
    <div class="done-stats" id="done-stats-grid"></div>
    <div class="close-hint">Tab schließen oder <kbd>q</kbd> drücken</div>
  </div>

</div><!-- /app -->

<script>
/* ═══════════════════════════════════════════════════════════════════════════
   LernOS Web-Review — Client
   ═══════════════════════════════════════════════════════════════════════════ */

const API      = '__API_BASE__';
const MODE     = '__MODE__';

/* ── State ─────────────────────────────────────────────────────────────── */
let card       = null;   // current card JSON from server
let phase      = 'conf'; // 'conf' | 'grade' | 'feedback' | 'done'
let selConf    = null;   // selected confidence value
let selGrade   = null;   // selected grade value
let sessionStart = Date.now();
let timerInterval = null;

const GRADE_DESC = {
  0:'Totaler Blackout', 1:'Falsch, bekannt', 2:'Falsch, erkannt',
  3:'Richtig, mühsam', 4:'Richtig, zögernd', 5:'Sofort + sicher'
};
const CONF_DESC = {
  1:'Gar nicht',2:'Kaum',3:'Mittel',4:'Ziemlich',5:'Völlig sicher'
};
const STATE_COLOR = {
  NEW:'#475569', LEARNING:'#dc2626', REVIEW:'#2563eb',
  MASTERED:'#16a34a', FROZEN:'#7c3aed'
};
const STATE_LABEL = {
  NEW:'NEU', LEARNING:'LERNEN', REVIEW:'REVIEW',
  MASTERED:'MASTERED', FROZEN:'FROZEN'
};

/* ── DOM refs ──────────────────────────────────────────────────────────── */
const $ = id => document.getElementById(id);

/* ── Timer ─────────────────────────────────────────────────────────────── */
function startTimer() {
  clearInterval(timerInterval);
  timerInterval = setInterval(() => {
    const s = Math.floor((Date.now() - sessionStart) / 1000);
    const mm = String(Math.floor(s/60)).padStart(2,'0');
    const ss = String(s%60).padStart(2,'0');
    document.getElementById('timer').textContent = `${mm}:${ss}`;
  }, 1000);
}

/* ── Render helpers ────────────────────────────────────────────────────── */
function renderMarkdown(text) {
  if (!text) return '';
  // marked already handles ```code``` blocks
  marked.setOptions({ breaks: true, gfm: true });
  return marked.parse(text);
}

function applyHighlight(el) {
  el.querySelectorAll('pre code').forEach(block => {
    hljs.highlightElement(block);
    // Extract language class for the label
    const cls = block.className.match(/language-(\w+)/);
    if (cls) block.parentElement.setAttribute('data-lang', cls[1]);
  });
}

function applyKatex(el) {
  if (window.renderMathInElement) {
    renderMathInElement(el, {
      delimiters: [
        {left:'$$', right:'$$', display:true},
        {left:'$',  right:'$',  display:false},
        {left:'\\[',right:'\\]',display:true},
        {left:'\\(',right:'\\)',display:false},
      ],
      throwOnError: false,
    });
  }
}

function renderContent(el, text) {
  el.innerHTML = renderMarkdown(text || '');
  applyHighlight(el);
  // KaTeX runs after highlight (highlight.js escapes inside <code>)
  setTimeout(() => applyKatex(el), 0);
}

/* ── Build key buttons ──────────────────────────────────────────────────── */
function buildButtons(containerId, range, descMap, onSelect) {
  const wrap = document.getElementById(containerId);
  wrap.innerHTML = '';
  for (const [k, desc] of Object.entries(descMap)) {
    if (parseInt(k) < range[0] || parseInt(k) > range[1]) continue;
    const btn = document.createElement('button');
    btn.className = 'key-btn';
    btn.dataset.val = k;
    btn.innerHTML = `<span class="key-num">${k}</span><span class="key-desc">${desc}</span>`;
    btn.addEventListener('click', () => onSelect(parseInt(k)));
    wrap.appendChild(btn);
  }
}

function selectBtn(containerId, val) {
  document.querySelectorAll(`#${containerId} .key-btn`).forEach(b => {
    b.classList.toggle('selected', parseInt(b.dataset.val) === val);
  });
}

/* ── Load card from server ──────────────────────────────────────────────── */
async function loadCard() {
  try {
    const res = await fetch(`${API}/card`);
    const data = await res.json();

    if (data.done) { showDone(data); return; }

    card = data;
    renderCard(card);
    document.getElementById('loading').style.display = 'none';
    document.getElementById('input-panel').style.display = 'block';

  } catch(e) {
    document.querySelector('#loading .loading-msg').textContent =
      'Verbindung verloren. Tab schließen und lernos review --web neu starten.';
  }
}

/* ── Render card ────────────────────────────────────────────────────────── */
function renderCard(c) {
  // Reset phase
  phase = 'conf'; selConf = null; selGrade = null;

  // Header
  document.getElementById('card-title').textContent = c.name;
  const badge = document.getElementById('state-badge');
  badge.textContent = STATE_LABEL[c.state] || c.state;
  badge.style.background = STATE_COLOR[c.state] || '#475569';

  // Meta
  document.getElementById('m-module').textContent   = c.module || '—';
  document.getElementById('m-ef').textContent       = c.ef.toFixed(2);
  document.getElementById('m-interval').textContent = c.interval_d + 'd';
  document.getElementById('m-reps').textContent     = c.repetitions;

  const due = c.days_until_due;
  const dueEl = document.getElementById('m-due');
  if (due === 0)      { dueEl.textContent = 'heute'; dueEl.style.color = 'var(--yellow)'; }
  else if (due < 0)   { dueEl.textContent = Math.abs(due)+'d überfällig'; dueEl.style.color = 'var(--red)'; }
  else                { dueEl.textContent = 'in '+due+'d'; dueEl.style.color = ''; }

  // Prereqs
  const prereqWrap = document.getElementById('prereqs');
  const chips = document.getElementById('prereq-chips');
  if (c.prereqs && c.prereqs.length > 0) {
    chips.innerHTML = c.prereqs.map(p =>
      `<span class="prereq-chip" style="border-left:3px solid ${STATE_COLOR[p.state]||'#475569'}"
             title="${p.state}">${p.name}</span>`
    ).join('');
    prereqWrap.style.display = 'block';
  } else {
    prereqWrap.style.display = 'none';
  }

  // Front content (question or topic name if no question)
  const frontEl = document.getElementById('content-front');
  renderContent(frontEl, c.question || ('**' + c.name + '**\n\n' + (c.description ? '' : '_Keine Beschreibung — nur Name als Karteikarte._')));

  // Back (hidden initially)
  document.getElementById('divider').style.display = 'none';
  const backEl = document.getElementById('content-back');
  backEl.style.display = 'none';
  backEl.innerHTML = '';

  // Active recall input
  const activeWrap = document.getElementById('active-input-wrap');
  if (MODE === 'active') {
    activeWrap.style.display = 'block';
    document.getElementById('active-textarea').value = '';
  } else {
    activeWrap.style.display = 'none';
  }

  // AI suggestion hidden
  document.getElementById('ai-suggestion').style.display = 'none';
  document.getElementById('socratic-panel').style.display = 'none';

  // Feedback hidden
  document.getElementById('feedback').style.display = 'none';

  // Phase: confidence
  showPhaseConf();

  // Prompt line
  document.getElementById('prompt-text').textContent =
    `lernos review "${c.name}"${MODE !== 'standard' ? ' --'+MODE : ''}`;

  // Progress
  document.getElementById('counter').textContent = `${c.idx}/${c.total}`;
  const pct = c.total > 0 ? ((c.idx - 1) / c.total * 100) : 0;
  document.getElementById('progress-bar-fill').style.width = pct + '%';
  document.getElementById('mode-badge').textContent = MODE;

  // Mode badge
  const modeBadge = document.getElementById('mode-badge');
  modeBadge.textContent = {standard:'standard', active:'active-recall', questions:'fragen'}[MODE] || MODE;
}

/* ── Phase transitions ──────────────────────────────────────────────────── */
function showPhaseConf() {
  document.getElementById('phase-confidence').classList.remove('hidden');
  document.getElementById('phase-grade').classList.add('hidden');
  document.getElementById('reveal-btn').style.display = 'flex';
  document.getElementById('submit-btn').style.display = 'none';
  buildButtons('conf-btns', [1,5], CONF_DESC, v => {
    selConf = v; selectBtn('conf-btns', v);
  });
}

function revealAnswer() {
  if (selConf === null) { selConf = 3; selectBtn('conf-btns', 3); }

  // Show answer
  document.getElementById('divider').style.display = 'block';
  const backEl = document.getElementById('content-back');

  if (card.question) {
    // Question-mode: answer is the answer field
    renderContent(backEl, card.answer || '_Keine Antwort hinterlegt._');
  } else {
    // Standard: description is the answer
    renderContent(backEl, card.description || '_Keine Beschreibung — vergib einen Grade basierend auf deiner Erinnerung._');
  }
  backEl.style.display = 'block';

  // If active mode: evaluate
  if (MODE === 'active') {
    const typed = document.getElementById('active-textarea').value.trim();
    if (typed) evaluateActiveAnswer(typed);
  }

  showPhaseGrade();
}

function showPhaseGrade() {
  phase = 'grade';
  document.getElementById('phase-confidence').classList.add('hidden');
  document.getElementById('phase-grade').classList.remove('hidden');
  document.getElementById('reveal-btn').style.display = 'none';
  document.getElementById('submit-btn').style.display = 'flex';
  buildButtons('grade-btns', [0,5], GRADE_DESC, v => {
    selGrade = v; selectBtn('grade-btns', v);
  });
}

let _socraticRound    = 0;
let _socraticMaxRounds = 2;
let _lastTyped         = '';
let _lastExpected      = '';
let _lastGrade         = 3;

async function evaluateActiveAnswer(typed) {
  _lastTyped    = typed;
  _lastExpected = card.description || card.answer || '';
  try {
    const res = await fetch(`${API}/evaluate`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ typed, expected: _lastExpected })
    });
    const data = await res.json();
    _lastGrade = data.grade;
    const sug = document.getElementById('ai-suggestion');
    document.getElementById('ai-grade-val').textContent = `[${data.grade}]`;
    document.getElementById('ai-grade-desc').textContent = GRADE_DESC[data.grade] || '';
    document.getElementById('ai-source-label').textContent = `(${data.source})`;
    sug.style.display = 'block';
    selGrade = data.grade; selectBtn('grade-btns', data.grade);

    // Sokratischen Dialog anbieten bei Note 2 oder 3
    const socPanel = document.getElementById('socratic-panel');
    if (data.grade === 2 || data.grade === 3) {
      _socraticRound = 0;
      document.getElementById('socratic-offer').style.display = 'flex';
      document.getElementById('socratic-hint-wrap').style.display = 'none';
      document.getElementById('socratic-result').style.display = 'none';
      socPanel.style.display = 'block';
    } else {
      socPanel.style.display = 'none';
    }
  } catch(e) { /* silent */ }
}

async function fetchSocraticHint() {
  _socraticRound++;
  document.getElementById('socratic-offer').style.display = 'none';
  document.getElementById('socratic-hint-wrap').style.display = 'block';
  document.getElementById('socratic-hint-text').textContent = '⏳ KI denkt nach…';
  document.getElementById('socratic-round-label').textContent =
    `(Runde ${_socraticRound}/${_socraticMaxRounds})`;
  document.getElementById('socratic-textarea').value = '';
  document.getElementById('socratic-result').style.display = 'none';

  const curTyped = _socraticRound === 1
    ? _lastTyped
    : document.getElementById('socratic-textarea').value || _lastTyped;

  try {
    const res = await fetch(`${API}/socratic`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        typed:    curTyped,
        expected: _lastExpected,
        grade:    _lastGrade,
        topic:    card.name || ''
      })
    });
    const data = await res.json();
    if (data.hint) {
      document.getElementById('socratic-hint-text').textContent = data.hint;
    } else {
      document.getElementById('socratic-hint-text').textContent =
        '(Kein Tipp verfügbar — Ollama nicht erreichbar?)';
    }
  } catch(e) {
    document.getElementById('socratic-hint-text').textContent = 'Fehler beim Laden des Tipps.';
  }
}

async function submitSocraticAnswer() {
  const improved = document.getElementById('socratic-textarea').value.trim();
  if (!improved) { skipSocratic(); return; }

  const resEl = document.getElementById('socratic-result');
  resEl.style.display = 'block';
  resEl.style.color = 'var(--text-dim2)';
  resEl.textContent = '⏳ Neu bewerten…';

  try {
    const res = await fetch(`${API}/socratic-evaluate`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        typed:      improved,
        expected:   _lastExpected,
        prev_grade: _lastGrade
      })
    });
    const data = await res.json();
    const newGrade = data.grade;
    _lastTyped = improved;

    // Grade-UI aktualisieren (Note kann nur steigen)
    selGrade = newGrade; selectBtn('grade-btns', newGrade);
    document.getElementById('ai-grade-val').textContent = `[${newGrade}]`;
    document.getElementById('ai-grade-desc').textContent = GRADE_DESC[newGrade] || '';

    if (data.improved) {
      _lastGrade = newGrade;
      resEl.style.color = 'var(--green)';
      resEl.textContent = `⬆ Verbessert! Note: ${newGrade}  (+${newGrade - data.prev_grade})`;
    } else {
      resEl.style.color = 'var(--text-dim2)';
      resEl.textContent = `→ Note bleibt: ${newGrade}`;
    }

    // Weitere Runde anbieten?
    if (_socraticRound < _socraticMaxRounds && newGrade < 4) {
      setTimeout(() => {
        document.getElementById('socratic-offer').innerHTML =
          `<span style="color:var(--text-dim2)">Noch eine Runde?</span>
           <button onclick="fetchSocraticHint()" style="background:rgba(234,179,8,.12);border:1px solid var(--yellow);color:var(--yellow);padding:4px 12px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:13px;">Weiter</button>
           <button onclick="skipSocratic()" style="background:transparent;border:1px solid var(--border);color:var(--text-dim2);padding:4px 12px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:13px;">Fertig</button>`;
        document.getElementById('socratic-offer').style.display = 'flex';
        document.getElementById('socratic-hint-wrap').style.display = 'none';
      }, 800);
    }
  } catch(e) {
    resEl.style.color = 'var(--red)';
    resEl.textContent = 'Fehler bei der Bewertung.';
  }
}

function skipSocratic() {
  document.getElementById('socratic-panel').style.display = 'none';
}

/* ── Submit grade ───────────────────────────────────────────────────────── */
async function submitGrade() {
  if (selGrade === null) { selGrade = 3; selectBtn('grade-btns', 3); }
  const conf = selConf ?? 3;

  try {
    const res = await fetch(`${API}/grade`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ grade: selGrade, confidence: conf })
    });
    const fb = await res.json();
    showFeedback(fb);
  } catch(e) {
    document.getElementById('prompt-text').textContent = 'Fehler beim Senden — Server erreichbar?';
  }
}

/* ── Show feedback ──────────────────────────────────────────────────────── */
function showFeedback(fb) {
  phase = 'feedback';
  document.getElementById('phase-grade').classList.add('hidden');
  document.getElementById('input-panel').style.display = 'none';

  const fbEl = document.getElementById('feedback');
  fbEl.style.display = 'block';

  // Result line
  const resultEl = document.getElementById('fb-result-line');
  if (fb.overconfidence) {
    resultEl.className = 'fb-result fb-overconf';
    resultEl.textContent = '⚠️  Falsch + Overconfidence erkannt  (Grade −2)';
  } else if (fb.correct) {
    resultEl.className = 'fb-result fb-correct';
    resultEl.textContent = fb.grade_used >= 5 ? '🌟 Perfekt!' : fb.grade_used >= 4 ? '✅ Gut!' : '✅ Richtig (knapp)';
  } else {
    resultEl.className = 'fb-result fb-wrong';
    resultEl.textContent = '❌ Falsch.';
  }

  // Grid
  const stateColorOld = STATE_COLOR[fb.old_state] || '#475569';
  const stateColorNew = STATE_COLOR[fb.new_state] || '#475569';
  const efPct    = Math.min(100, (fb.new_ef / 2.5) * 100);
  const efColor  = fb.new_ef >= 2.0 ? 'var(--green)' : fb.new_ef >= 1.6 ? 'var(--yellow)' : 'var(--red)';

  document.getElementById('fb-grid').innerHTML = `
    <span class="fb-key">Zustand</span>
    <span class="fb-val">
      <span class="from" style="color:${stateColorOld}">${STATE_LABEL[fb.old_state]||fb.old_state}</span>
      <span class="arrow">→</span>
      <span class="to" style="color:${stateColorNew}">${STATE_LABEL[fb.new_state]||fb.new_state}</span>
    </span>

    <span class="fb-key">EF</span>
    <span class="fb-val">
      <span class="from">${fb.old_ef.toFixed(3)}</span>
      <span class="arrow">→</span>
      <span class="ef-bar-wrap">
        <span class="ef-bar"><span class="ef-bar-fill" style="width:${efPct}%;background:${efColor}"></span></span>
        <span class="to" style="color:${efColor}">${fb.new_ef.toFixed(3)}</span>
      </span>
    </span>

    <span class="fb-key">Intervall</span>
    <span class="fb-val">
      <span class="from">${fb.old_interval}d</span>
      <span class="arrow">→</span>
      <span class="to">${fb.new_interval}d</span>
    </span>

    <span class="fb-key">Nächstes Review</span>
    <span class="fb-val to">${fb.new_due_date}</span>

    ${fb.grade_used !== fb.grade
      ? `<span class="fb-key">Grade (angepasst)</span>
         <span class="fb-val"><span class="from">${fb.grade}</span><span class="arrow">→</span><span class="to">${fb.grade_used}</span></span>`
      : ''}
  `;

  // Cascade
  if (fb.cascade && fb.cascade.length > 0) {
    const ci = document.getElementById('cascade-info');
    ci.style.display = 'block';
    document.getElementById('cascade-items').innerHTML = fb.cascade.map(c =>
      `<div class="cascade-item">
         <b>${c.name}</b>:
         <span style="color:${STATE_COLOR[c.old]||'#fff'}">${STATE_LABEL[c.old]||c.old}</span>
         →
         <span style="color:${STATE_COLOR[c.new]||'#fff'}">${STATE_LABEL[c.new]||c.new}</span>
         <span style="color:var(--text-dim2);font-size:11px">(Gewicht ${c.weight})</span>
       </div>`
    ).join('');
  } else {
    document.getElementById('cascade-info').style.display = 'none';
  }

  // Progress update
  document.getElementById('counter').textContent = `${fb.idx_done}/${fb.total}`;
  document.getElementById('progress-bar-fill').style.width =
    (fb.total > 0 ? (fb.idx_done/fb.total)*100 : 0) + '%';
}

/* ── Done Screen ────────────────────────────────────────────────────────── */
function showDone(data) {
  phase = 'done';
  document.getElementById('card-wrap').style.display = 'none';
  document.getElementById('input-panel').style.display = 'none';
  const ds = document.getElementById('done-screen');
  ds.style.display = 'flex';

  const acc = data.total > 0 ? Math.round(data.correct / data.total * 100) : 0;

  document.getElementById('done-title').textContent =
    data.total === 0 ? 'Heute nichts fällig 🎉' : 'Session abgeschlossen';
  document.getElementById('done-sub').textContent =
    data.total > 0
      ? `${data.total} Topic${data.total !== 1 ? 's' : ''} reviewt in ${formatDuration(Date.now()-sessionStart)}`
      : 'Alle Topics sind auf dem aktuellen Stand.';

  if (data.total > 0) {
    document.getElementById('done-stats-grid').innerHTML = `
      <div class="done-stat">
        <div class="ds-val">${data.correct}/${data.total}</div>
        <div class="ds-label">KORREKT</div>
      </div>
      <div class="done-stat">
        <div class="ds-val">${acc}%</div>
        <div class="ds-label">GENAUIGKEIT</div>
      </div>
      <div class="done-stat">
        <div class="ds-val">${data.mastered_new || 0}</div>
        <div class="ds-label">MASTERED</div>
      </div>`;
  } else {
    document.getElementById('done-stats-grid').innerHTML = '';
  }

  clearInterval(timerInterval);
}

function formatDuration(ms) {
  const s = Math.floor(ms/1000);
  const m = Math.floor(s/60);
  return m > 0 ? `${m}m ${s%60}s` : `${s}s`;
}

/* ── Keyboard shortcuts ─────────────────────────────────────────────────── */
document.addEventListener('keydown', e => {
  if (e.target === document.getElementById('active-textarea')) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      if (phase === 'conf') revealAnswer();
    }
    return;
  }

  if (phase === 'conf') {
    if (e.key >= '1' && e.key <= '5') {
      selConf = parseInt(e.key); selectBtn('conf-btns', selConf);
    }
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); revealAnswer(); }
  }
  else if (phase === 'grade') {
    if (e.key >= '0' && e.key <= '5') {
      selGrade = parseInt(e.key); selectBtn('grade-btns', selGrade);
    }
    if (e.key === 'Enter') { e.preventDefault(); submitGrade(); }
  }
  else if (phase === 'feedback') {
    if (e.key === 'Enter' || e.key === 'n' || e.key === 'N') {
      e.preventDefault(); nextCard();
    }
  }
  else if (phase === 'done') {
    if (e.key === 'q' || e.key === 'Q') window.close();
  }
});

/* ── Button wiring ──────────────────────────────────────────────────────── */
document.getElementById('reveal-btn').addEventListener('click', revealAnswer);
document.getElementById('socratic-yes-btn').addEventListener('click', fetchSocraticHint);
document.getElementById('socratic-no-btn').addEventListener('click', skipSocratic);
document.getElementById('socratic-submit-btn').addEventListener('click', submitSocraticAnswer);
document.getElementById('socratic-skip-btn').addEventListener('click', skipSocratic);
document.getElementById('socratic-textarea').addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); submitSocraticAnswer(); }
});
document.getElementById('submit-btn').addEventListener('click', submitGrade);
document.getElementById('next-btn').addEventListener('click', nextCard);

async function nextCard() {
  document.getElementById('feedback').style.display = 'none';
  document.getElementById('input-panel').style.display = 'block';
  document.getElementById('cascade-info').style.display = 'none';
  await loadCard();
}

/* ── Boot ───────────────────────────────────────────────────────────────── */
startTimer();
loadCard();
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Review Server
# ─────────────────────────────────────────────────────────────────────────────

class ReviewSession:
    """
    Hält den Review-Session-Zustand zwischen HTTP-Requests.
    Thread-safe durch threading.Lock.
    """
    def __init__(self, topics: list, conn, mode: str = "standard",
                 db_path: str | None = None):
        self.topics    = list(topics)
        self._db_path  = db_path   # für thread-sichere Verbindungen
        self.conn      = conn      # Haupt-Thread-Verbindung (für apply_grade im Haupt-Thread)
        self.mode      = mode
        self.idx       = 0           # aktueller Topic-Index
        self.total     = len(topics)
        self.correct   = 0
        self.mastered_new = 0
        self.lock      = threading.Lock()
        self._last_conf = None       # für grade-Schritt gespeicherte Konfidenz

    def current_topic(self):
        if self.idx >= self.total:
            return None
        return self.topics[self.idx]

    def card_json(self) -> dict:
        """Serialisiert aktuellen Topic als Card-JSON für den Browser."""
        import sqlite3 as _sq3
        from datetime import date as _date
        from lernos.db.topics import get_questions_for_topic
        from lernos.graph.topo import get_prerequisites

        t = self.current_topic()
        if t is None:
            return {
                "done":        True,
                "total":       self.total,
                "correct":     self.correct,
                "mastered_new":self.mastered_new,
            }

        days = (_date.fromisoformat(t.due_date) - _date.today()).days
        # Thread-sichere Verbindung: jeder Handler-Thread bekommt seine eigene
        if self._db_path:
            _tconn = _sq3.connect(self._db_path, check_same_thread=False)
            _tconn.row_factory = _sq3.Row
        else:
            _tconn = self.conn
        prereqs = get_prerequisites(_tconn, t.id)
        prereq_data = [
            {"name": p.name, "state": p.state}
            for p in prereqs
        ]

        # Fragen-Modus: aktuelle Frage aus DB
        question_text = None
        answer_text   = None
        if self.mode == "questions":
            qs = get_questions_for_topic(_tconn, t.id, unused_first=True)
            if qs:
                q = qs[0]
                question_text = q.question
                answer_text   = q.answer
                from lernos.db.topics import mark_question_used
                mark_question_used(self.conn, q.id)

        return {
            "done":          False,
            "id":            t.id,
            "name":          t.name,
            "module":        t.module or "",
            "state":         t.state,
            "ef":            round(t.ef, 4),
            "interval_d":    t.interval_d,
            "repetitions":   t.repetitions,
            "days_until_due": days,
            "description":   t.description or "",
            "prereqs":       prereq_data,
            "question":      question_text,
            "answer":        answer_text,
            "idx":           self.idx + 1,
            "total":         self.total,
        }

    def apply_grade(self, grade: int, confidence: int) -> dict:
        """Wendet SM-2-Algorithmus an und gibt Feedback-JSON zurück."""
        import sqlite3 as _sq3
        from lernos.sm2.algorithm import calculate
        from lernos.sm2.cascade import cascade_review, STATE_LEARNING
        from lernos.db.topics import (
            update_topic_sm2, log_session, get_topic_by_id,
            increment_learning_resets
        )

        # Thread-sichere Verbindung
        if self._db_path:
            _conn = _sq3.connect(self._db_path, check_same_thread=False)
            _conn.row_factory = _sq3.Row
            _conn.execute("PRAGMA foreign_keys=ON")
        else:
            _conn = self.conn

        t      = self.current_topic()
        result = calculate(t, grade, confidence)

        new_resets = getattr(t, "learning_resets", 0) or 0
        if result.new_state == STATE_LEARNING and t.state != STATE_LEARNING:
            new_resets += 1

        update_topic_sm2(
            _conn, t.id,
            result.new_state, result.new_ef,
            result.new_interval, result.new_reps,
            result.new_due_date,
            learning_resets=new_resets,
        )
        log_session(
            _conn, t.id,
            grade, confidence, result.correct,
            t.state, result.new_state,
            t.ef, result.new_ef,
            t.interval_d, result.new_interval,
        )

        cascade_info = []
        if result.new_state == STATE_LEARNING and t.state != STATE_LEARNING:
            cascade_info = cascade_review(_conn, t.id)

        if result.correct:
            self.correct += 1
        if result.new_state == "MASTERED" and t.state != "MASTERED":
            self.mastered_new += 1

        self.idx += 1

        overconfidence = (confidence >= 4 and not result.correct)

        return {
            "correct":       result.correct,
            "grade":         grade,
            "grade_used":    result.grade_used,
            "overconfidence":overconfidence,
            "old_state":     t.state,
            "new_state":     result.new_state,
            "old_ef":        round(t.ef, 4),
            "new_ef":        round(result.new_ef, 4),
            "old_interval":  t.interval_d,
            "new_interval":  result.new_interval,
            "new_due_date":  result.new_due_date,
            "cascade":       [
                {"name": c["name"], "old": c["old"], "new": c["new"],
                 "weight": round(c["weight"], 2)}
                for c in cascade_info
            ],
            "idx_done":      self.idx,
            "total":         self.total,
        }


class ReviewHandler(BaseHTTPRequestHandler):
    """Minimaler HTTP-Handler. Kein Framework, nur stdlib."""

    session: ReviewSession = None  # Klassen-Variable, gesetzt vor Server-Start

    def log_message(self, fmt, *args):
        pass  # Suppress access logs (kein Terminal-Spam)

    def _json(self, code: int, data: Any):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            self._html(ReviewHandler._html_page)
            return

        if path == "/api/card":
            with ReviewHandler.session.lock:
                self._json(200, ReviewHandler.session.card_json())
            return

        self.send_response(404); self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if path == "/api/grade":
            grade      = int(body.get("grade", 3))
            confidence = int(body.get("confidence", 3))
            with ReviewHandler.session.lock:
                fb = ReviewHandler.session.apply_grade(grade, confidence)
            self._json(200, fb)
            return

        if path == "/api/evaluate":
            # KI- oder lokale Bewertung für Active-Recall
            typed    = body.get("typed", "")
            expected = body.get("expected", "")
            grade, source = _evaluate_answer(typed, expected)
            self._json(200, {"grade": grade, "source": source})
            return

        if path == "/api/socratic":
            # Sokratische Rückfrage generieren (kein Spoiler)
            typed    = body.get("typed", "")
            expected = body.get("expected", "")
            grade    = int(body.get("grade", 3))
            topic    = body.get("topic", "")
            hint     = _generate_socratic_hint(typed, expected, grade, topic)
            self._json(200, {"hint": hint})
            return

        if path == "/api/socratic-evaluate":
            # Verbesserte Antwort bewerten (Note kann nur gleich/besser werden)
            typed      = body.get("typed", "")
            expected   = body.get("expected", "")
            prev_grade = int(body.get("prev_grade", 3))
            new_grade, source = _evaluate_answer(typed, expected)
            final_grade = max(prev_grade, new_grade)
            self._json(200, {
                "grade":      final_grade,
                "prev_grade": prev_grade,
                "source":     source,
                "improved":   final_grade > prev_grade,
            })
            return

        self.send_response(404); self.end_headers()


def _evaluate_answer(typed: str, expected: str) -> tuple[int, str]:
    """Bewertet eine getippte Antwort gegen die Musterantwort."""
    try:
        from lernos.ollama.embed import evaluate_answer_ai, is_ollama_running
        if is_ollama_running():
            grade, source = evaluate_answer_ai(expected, typed)
            return grade, source
    except Exception:
        pass
    from lernos.ollama.embed import evaluate_answer_local
    return evaluate_answer_local(expected, typed), "Lokal"


def _generate_socratic_hint(typed: str, expected: str, grade: int, topic: str) -> str | None:
    """Delegiert an embed.generate_socratic_hint. Gibt None zurück bei Fehler."""
    try:
        from lernos.ollama.embed import generate_socratic_hint, is_ollama_running
        if not is_ollama_running():
            return None
        return generate_socratic_hint(expected, typed, grade, topic)
    except Exception:
        return None


def start_review_server(
    conn,
    topics: list,
    mode: str = "standard",
    port: int = 0,
    open_browser: bool = True,
    output_path: str | None = None,
    db_path: str | None = None,
) -> int:
    """
    Startet den Review-HTTP-Server.
    port=0 → Betriebssystem wählt freien Port.
    Gibt den tatsächlich genutzten Port zurück.
    db_path: Pfad zur SQLite-Datei für thread-sichere Handler-Verbindungen.
    """
    session = ReviewSession(topics, conn, mode, db_path=db_path)
    ReviewHandler.session      = session

    server = HTTPServer(("127.0.0.1", port), ReviewHandler)
    actual_port = server.server_address[1]

    api_base = f"http://127.0.0.1:{actual_port}/api"
    html_page = (
        REVIEW_HTML
        .replace("__API_BASE__", api_base)
        .replace("__MODE__", mode)
    )
    ReviewHandler._html_page = html_page

    # Optional: HTML auch als Datei speichern (für lernos review --web --output)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_page)

    if open_browser:
        url = f"http://127.0.0.1:{actual_port}/"
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    return actual_port, server
