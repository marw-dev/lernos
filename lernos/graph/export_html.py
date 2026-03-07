"""
LernOS — D3.js Graph-HTML-Generator (v1.3)

Fixes:
  - Konsistente SVG-Selektion über eine Variable statt gemischten Selektoren
"""
from __future__ import annotations
import json
import sqlite3
from lernos.db.topics import get_all_topics, get_all_edges

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>LernOS — Wissensgraph</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0F172A; color: #F8FAFC;
    font-family: 'Segoe UI', system-ui, sans-serif; overflow: hidden;
  }
  #header {
    position: fixed; top: 0; left: 0; right: 0; z-index: 10;
    padding: 16px 24px; background: rgba(15,23,42,0.95);
    backdrop-filter: blur(12px); border-bottom: 1px solid #1E293B;
    display: flex; align-items: center; justify-content: space-between;
  }
  #header h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
  #header h1 span { color: #3B82F6; }
  #legend { display: flex; gap: 16px; align-items: center; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #94A3B8; }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; }
  #stats {
    position: fixed; bottom: 24px; left: 24px; z-index: 10;
    background: rgba(15,23,42,0.95); border: 1px solid #1E293B;
    border-radius: 12px; padding: 16px 20px; min-width: 220px;
  }
  #stats h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748B; margin-bottom: 12px; }
  .stat-row { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 6px; }
  .stat-label { color: #94A3B8; }
  .stat-val { font-weight: 600; }
  #tooltip {
    position: fixed; z-index: 20; background: #1E293B;
    border: 1px solid #334155; border-radius: 10px; padding: 14px 18px;
    pointer-events: none; display: none; max-width: 280px;
  }
  #tooltip .t-name  { font-size: 15px; font-weight: 700; margin-bottom: 6px; }
  #tooltip .t-mod   { font-size: 11px; color: #64748B; margin-bottom: 10px; }
  #tooltip .t-row   { font-size: 12px; color: #94A3B8; margin-bottom: 4px; }
  #tooltip .t-row b { color: #F8FAFC; }
  #tooltip .t-state {
    display: inline-block; margin-top: 8px;
    padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; color: #fff;
  }
  #tooltip .t-docs { font-size: 11px; color: #3B82F6; margin-top: 6px; }
  svg#graph { width: 100vw; height: 100vh; display: block; }
  .link { stroke-opacity: 0.5; transition: stroke-opacity 0.2s; }
  .node { cursor: pointer; }
  .node text { pointer-events: none; font-size: 12px; fill: #E2E8F0; }
  #controls {
    position: fixed; top: 80px; right: 24px; z-index: 10;
    display: flex; flex-direction: column; gap: 8px;
  }
  .ctrl-btn {
    background: #1E293B; border: 1px solid #334155; color: #94A3B8;
    padding: 8px 14px; border-radius: 8px; cursor: pointer; font-size: 12px;
    transition: all 0.2s;
  }
  .ctrl-btn:hover { background: #334155; color: #F8FAFC; }
  #filter-box {
    position: fixed; top: 80px; left: 24px; z-index: 10;
    background: rgba(15,23,42,0.95); border: 1px solid #1E293B;
    border-radius: 12px; padding: 14px 18px; min-width: 180px;
    max-height: 400px; overflow-y: auto;
  }
  #filter-box h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748B; margin-bottom: 10px; }
  .filter-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px; cursor: pointer; }
  .mod-color-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
</style>
</head>
<body>
<div id="header">
  <h1>Lern<span>OS</span> &mdash; Wissensgraph</h1>
  <div id="legend">
    <div class="legend-item"><div class="legend-dot" style="background:#64748B"></div> NEU</div>
    <div class="legend-item"><div class="legend-dot" style="background:#DC2626"></div> LERNEN</div>
    <div class="legend-item"><div class="legend-dot" style="background:#2563EB"></div> REVIEW</div>
    <div class="legend-item"><div class="legend-dot" style="background:#16A34A"></div> MASTERED</div>
    <div class="legend-item"><div class="legend-dot" style="background:#7C3AED"></div> FROZEN</div>
  </div>
</div>

<div id="filter-box">
  <h3>Module filtern</h3>
  <div id="module-filters"></div>
</div>

<div id="controls">
  <button class="ctrl-btn" onclick="simulation.alpha(0.5).restart()">🔀 Neu berechnen</button>
  <button class="ctrl-btn" onclick="zoomToFit()">⊡ Einpassen</button>
  <button class="ctrl-btn" onclick="toggleLabels()">🏷 Labels</button>
</div>

<div id="stats">
  <h3>Übersicht</h3>
  <div id="stat-content"></div>
</div>

<div id="tooltip"></div>
<!-- FIX: id="graph" damit d3.select("#graph") funktioniert -->
<svg id="graph"></svg>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<script>
const DATA   = __DATA__;
const nodes  = DATA.nodes;
const links  = DATA.links;
const width  = window.innerWidth;
const height = window.innerHeight;
let showLabels = true;

const STATE_COLOR = {
  NEW:      "#64748B", LEARNING: "#DC2626",
  REVIEW:   "#2563EB", MASTERED: "#16A34A", FROZEN: "#7C3AED",
};

const MOD_COLORS = [
  "#3b82f6","#ef4444","#10b981","#f59e0b","#8b5cf6",
  "#ec4899","#14b8a6","#f97316","#6366f1","#84cc16"
];
const modules      = [...new Set(nodes.map(n => n.module).filter(Boolean))].sort();
const getModColor  = m => MOD_COLORS[modules.indexOf(m) % MOD_COLORS.length] || "#475569";

// Stats Panel
const statEl   = document.getElementById("stat-content");
const counts   = {};
nodes.forEach(n => { counts[n.state] = (counts[n.state]||0)+1; });
const stateOrder = ["NEW","LEARNING","REVIEW","MASTERED","FROZEN"];
const stateLabel = {NEW:"Neu",LEARNING:"Lernen",REVIEW:"Review",MASTERED:"Mastered",FROZEN:"Frozen"};
stateOrder.forEach(s => {
  if (counts[s]) statEl.innerHTML +=
    `<div class="stat-row">
       <span class="stat-label" style="color:${STATE_COLOR[s]}">${stateLabel[s]}</span>
       <span class="stat-val">${counts[s]}</span>
     </div>`;
});

// Modul-Cluster-Zentren
const clusterRadius = Math.max(280, modules.length * 100);
const moduleCenters = {};
modules.forEach((m, i) => {
  const angle = (i / modules.length) * 2 * Math.PI;
  moduleCenters[m] = {
    x: width/2  + Math.cos(angle) * clusterRadius,
    y: height/2 + Math.sin(angle) * clusterRadius,
  };
});

// Modul-Filter UI
const filterEl     = document.getElementById("module-filters");
const activeModules = new Set(modules);
if (modules.length === 0) filterEl.innerHTML = '<div style="font-size:12px;color:#475569">Keine Module</div>';
modules.forEach(m => {
  const id = "mod-" + m.replace(/[^a-zA-Z0-9]/g, '-');
  filterEl.innerHTML += `<label class="filter-row">
    <input type="checkbox" id="${id}" checked onchange="updateFilter()">
    <span class="mod-color-dot" style="background:${getModColor(m)}"></span>
    <span>${m}</span></label>`;
});

function updateFilter() {
  activeModules.clear();
  modules.forEach(m => {
    const cb = document.getElementById("mod-" + m.replace(/[^a-zA-Z0-9]/g, '-'));
    if (cb && cb.checked) activeModules.add(m);
  });
  node.style("display", d => activeModules.has(d.module) ? "block" : "none");
  link.style("display", d => {
    const src = nodeById[d.source.id || d.source];
    const tgt = nodeById[d.target.id || d.target];
    return (src && tgt && activeModules.has(src.module) && activeModules.has(tgt.module))
      ? "block" : "none";
  });
  simulation.alpha(0.3).restart();
}

function toggleLabels() {
  showLabels = !showLabels;
  labelText.style("display", showLabels ? "block" : "none");
}

// Lookup-Map für schnellen Zugriff
const nodeById = {};
nodes.forEach(n => { nodeById[n.id] = n; });

// ── SVG Setup ────────────────────────────────────────────────────────────────
const svgEl = d3.select("svg#graph");
const g     = svgEl.append("g");

// Zoom
const zoom = d3.zoom()
  .scaleExtent([0.08, 5])
  .on("zoom", e => g.attr("transform", e.transform));
svgEl.call(zoom);

function zoomToFit() {
  const bounds = g.node().getBBox();
  if (!bounds || bounds.width === 0) return;
  const scale = Math.min(0.9 * width / bounds.width, 0.9 * height / bounds.height, 3);
  const tx    = width/2  - scale * (bounds.x + bounds.width/2);
  const ty    = height/2 - scale * (bounds.y + bounds.height/2);
  svgEl.transition().duration(700)
    .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
}

// Arrow Markers
const defs = svgEl.append("defs");
Object.entries(STATE_COLOR).forEach(([state, color]) => {
  defs.append("marker")
    .attr("id", `arrow-${state}`)
    .attr("viewBox","0 -5 10 10").attr("refX", 30).attr("refY", 0)
    .attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto")
    .append("path").attr("d","M0,-5L10,0L0,5").attr("fill", color).attr("opacity", 0.8);
});

// ── Force Simulation ─────────────────────────────────────────────────────────
const simulation = d3.forceSimulation(nodes)
  .force("link",      d3.forceLink(links).id(d => d.id).distance(90).strength(0.55))
  .force("charge",    d3.forceManyBody().strength(-320))
  .force("center",    d3.forceCenter(width/2, height/2))
  .force("collision", d3.forceCollide(48))
  .force("x", d3.forceX(d => d.module && moduleCenters[d.module] ? moduleCenters[d.module].x : width/2).strength(0.13))
  .force("y", d3.forceY(d => d.module && moduleCenters[d.module] ? moduleCenters[d.module].y : height/2).strength(0.13));

// Modul-Wasserzeichen
const moduleLabels = g.append("g").selectAll(".mod-bg")
  .data(modules).enter().append("text")
  .attr("class","mod-bg")
  .attr("fill", d => getModColor(d)).attr("opacity", 0.12)
  .attr("font-size", 52).attr("font-weight","bold")
  .attr("text-anchor","middle").attr("pointer-events","none").text(d => d);

// ── Links ────────────────────────────────────────────────────────────────────
const link = g.append("g").selectAll("line")
  .data(links).enter().append("line")
  .attr("class","link")
  .attr("stroke", d => {
    const src = nodeById[d.source.id || d.source];
    return src ? STATE_COLOR[src.state] : "#475569";
  })
  .attr("stroke-width", d => 1.5 + d.weight * 2.5)
  .attr("stroke-dasharray", d => {
    const src = nodeById[d.source.id || d.source];
    const tgt = nodeById[d.target.id || d.target];
    return (src && tgt && src.module !== tgt.module) ? "7,5" : "none";
  })
  .attr("marker-end", d => {
    const src = nodeById[d.source.id || d.source];
    return src ? `url(#arrow-${src.state})` : "none";
  });

// ── Nodes ────────────────────────────────────────────────────────────────────
const node = g.append("g").selectAll(".node")
  .data(nodes).enter().append("g")
  .attr("class","node")
  .call(d3.drag()
    .on("start", (e,d) => { if(!e.active) simulation.alphaTarget(0.3).restart(); d.fx=d.x; d.fy=d.y; })
    .on("drag",  (e,d) => { d.fx=e.x; d.fy=e.y; })
    .on("end",   (e,d) => { if(!e.active) simulation.alphaTarget(0); d.fx=null; d.fy=null; }));

// Äußerer Ring (Modul-Farbe)
node.append("circle")
  .attr("r", d => 17 + Math.min(d.ef * 3, 9))
  .attr("fill","transparent")
  .attr("stroke", d => d.module ? getModColor(d.module) : "#475569")
  .attr("stroke-width", 2.5).attr("opacity", 0.75);

// Innerer Kreis (Zustand)
node.append("circle")
  .attr("r", d => 12 + Math.min(d.ef * 3, 9))
  .attr("fill", d => STATE_COLOR[d.state] + "30")
  .attr("stroke", d => STATE_COLOR[d.state])
  .attr("stroke-width", 2.2);

// PDF-Indikator (kleiner blauer Punkt wenn Dokumente vorhanden)
node.filter(d => d.doc_count > 0).append("circle")
  .attr("r", 5).attr("cx", d => 12 + Math.min(d.ef*3,9) - 3)
  .attr("cy", d => -(12 + Math.min(d.ef*3,9)) + 3)
  .attr("fill","#3B82F6").attr("stroke","#0F172A").attr("stroke-width",1.5)
  .append("title").text(d => `📎 ${d.doc_count} Dokument(e)`);

// Labels
const labelText = node.append("text")
  .attr("dy", d => -(22 + Math.min(d.ef*3,9)))
  .attr("text-anchor","middle").attr("font-size", 12)
  .text(d => d.name.length > 22 ? d.name.slice(0,20)+"…" : d.name);

// ── Tooltip ───────────────────────────────────────────────────────────────────
const tooltip = document.getElementById("tooltip");
node.on("mousemove", (e,d) => {
  const due = d.days_until_due === 0 ? "heute fällig"
            : d.days_until_due < 0 ? `${Math.abs(d.days_until_due)}d überfällig`
            : `in ${d.days_until_due}d`;
  const docHtml = d.doc_count > 0
    ? `<div class="t-docs">📎 ${d.doc_count} Dokument(e) · ${d.q_count} Fragen</div>` : "";
  tooltip.style.display = "block";
  tooltip.style.left = (e.clientX + 18) + "px";
  tooltip.style.top  = (e.clientY - 10) + "px";
  tooltip.innerHTML = `
    <div class="t-name">${d.name}</div>
    <div class="t-mod" style="color:${getModColor(d.module)}">${d.module || "Kein Modul"}</div>
    <div class="t-row">EF: <b>${d.ef.toFixed(2)}</b></div>
    <div class="t-row">Intervall: <b>${d.interval_d}d</b></div>
    <div class="t-row">Fälligkeit: <b>${due}</b></div>
    <div class="t-row">Wiederholungen: <b>${d.repetitions}</b></div>
    ${docHtml}
    <div class="t-state" style="background:${STATE_COLOR[d.state]}">${d.state}</div>`;
}).on("mouseleave", () => { tooltip.style.display = "none"; });

// ── Simulation Tick ───────────────────────────────────────────────────────────
simulation.on("tick", () => {
  link.attr("x1", d=>d.source.x).attr("y1", d=>d.source.y)
      .attr("x2", d=>d.target.x).attr("y2", d=>d.target.y);
  node.attr("transform", d => `translate(${d.x},${d.y})`);
  moduleLabels
    .attr("x", m => { const ms=nodes.filter(n=>n.module===m&&activeModules.has(m));
                      return ms.length ? d3.mean(ms,n=>n.x) : moduleCenters[m]?.x||width/2; })
    .attr("y", m => { const ms=nodes.filter(n=>n.module===m&&activeModules.has(m));
                      return ms.length ? d3.mean(ms,n=>n.y)-65 : moduleCenters[m]?.y||height/2; })
    .attr("display", m => activeModules.has(m) ? "block" : "none");
});

updateFilter();
setTimeout(zoomToFit, 1600);
window.addEventListener("resize", () => {
  simulation.force("center", d3.forceCenter(window.innerWidth/2, window.innerHeight/2));
});
</script>
</body>
</html>
"""


def export_graph_html(conn, output_path: str) -> int:
    """Generiert HTML mit D3.js Wissensgraph. Gibt Knotenanzahl zurück."""
    from datetime import date as _date
    from lernos.db.topics import get_documents_for_topic, get_questions_for_topic

    topics = get_all_topics(conn)
    edges  = get_all_edges(conn)

    nodes = []
    for t in topics:
        days = (_date.fromisoformat(t.due_date) - _date.today()).days
        doc_count = len(get_documents_for_topic(conn, t.id))
        q_count   = len(get_questions_for_topic(conn, t.id))
        nodes.append({
            "id":           t.id,
            "name":         t.name,
            "module":       t.module,
            "state":        t.state,
            "ef":           t.ef,
            "interval_d":   t.interval_d,
            "repetitions":  t.repetitions,
            "days_until_due": days,
            "doc_count":    doc_count,
            "q_count":      q_count,
        })

    links = [
        {"source": e.from_id, "target": e.to_id, "weight": e.weight}
        for e in edges
    ]

    data = json.dumps({"nodes": nodes, "links": links}, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("__DATA__", data)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return len(nodes)
