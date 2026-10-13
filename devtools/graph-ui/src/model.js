// graph.json -> React Flow nodes/edges, with the semantic colour channels of 02 §4.7:
// fill = verdict, hatching = inherited blame, border = review, red badge = open questions,
// "!" = visual flag, text glyphs P/F/?/- for colour-blind safety. Layout: dagre, rankdir LR, bipartite.
import dagre from '@dagrejs/dagre';
import { MarkerType } from '@xyflow/react';

export const STEP_W = 210, STEP_H = 74, DS_W = 184, DS_H = 50;

export const VERDICT_STYLE = {
  PASS_EXACT: { fill: '#1b6e35', text: '#ffffff', glyph: 'P', label: 'pass (exact)' },
  PASS_WITHIN_TOLERANCE: { fill: '#58b368', text: '#0d2613', glyph: 'P', label: 'pass (within tolerance)' },
  FAIL: { fill: '#d0312d', text: '#ffffff', glyph: 'F', label: 'fail' },
  NEEDS_DECISION: { fill: '#f2a900', text: '#2b1d00', glyph: '?', label: 'needs decision' },
  NEEDS_REVIEW: { fill: '#f6c445', text: '#2b1d00', glyph: '?', label: 'needs review' },
  EVALUATOR_ERROR: { fill: '#ffffff', text: '#000000', glyph: '?', label: 'evaluator error', black: true },
  BLOCKED: { fill: '#ffffff', text: '#000000', glyph: '?', label: 'blocked', black: true },
  NOT_COMPARED: { fill: '#d9dde3', text: '#2a3038', glyph: '–', label: 'not compared' },
  NONE: { fill: '#eef0f3', text: '#2a3038', glyph: '–', label: 'no verdict yet' },
};

export function verdictStyle(v) {
  return VERDICT_STYLE[v] || VERDICT_STYLE.NONE;
}

export const HATCH = 'repeating-linear-gradient(135deg, rgba(255,255,255,0.55) 0 5px, rgba(255,255,255,0) 5px 11px)';

export function openQuestionsByNode(graph) {
  const out = {};
  for (const q of graph.questions || []) {
    if (q.status !== 'open') continue;
    const nodes = [q.node, ...(q.nodes || [])].filter(Boolean);
    for (const n of new Set(nodes)) out[n] = (out[n] || 0) + 1;
  }
  return out;
}

function edgeStyle(e) {
  const base = { stroke: '#7d8796', strokeWidth: 1.4 };
  let label;
  let animated = false;
  if (e.kind === 'writes') Object.assign(base, { stroke: '#37474f', strokeWidth: 1.8 });
  if (e.kind === 'reads_grid') { Object.assign(base, { strokeDasharray: '2 4' }); label = 'grid'; }
  if (e.kind === 'reads_write') { Object.assign(base, { stroke: '#37474f', strokeDasharray: '6 3' }); label = 'in place'; }
  if (e.kind === 'equivalent_to') {
    Object.assign(base, { stroke: '#7e57c2', strokeDasharray: '6 4' });
    label = e.review === 'confirmed' ? '≡ equivalent (confirmed)' : '≡ equivalent?';
  }
  if (e.state === 'user_asserted') { Object.assign(base, { stroke: '#1e6fd9' }); label = label || 'added by you'; }
  if (e.state === 'rejected') {
    Object.assign(base, { stroke: '#c62828', strokeDasharray: '4 4', opacity: 0.55 });
    label = 'rejected';
  }
  if (e.conflict) { label = (label ? label + ' · ' : '') + 'conflict'; animated = true; }
  return { style: base, label, animated };
}

export function buildFlow(graph, layout, selectedId) {
  const qn = openQuestionsByNode(graph);
  const nodes = [];
  const edges = [];
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 22, ranksep: 46, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const s of graph.steps) g.setNode(s.id, { width: STEP_W, height: STEP_H });
  for (const d of graph.datasets) g.setNode(d.id, { width: DS_W, height: DS_H });
  const known = new Set([...graph.steps.map((s) => s.id), ...graph.datasets.map((d) => d.id)]);
  for (const e of graph.edges) {
    if (!known.has(e.from) || !known.has(e.to)) continue;
    if (e.kind === 'equivalent_to' || e.kind === 'displays') continue; // knowledge, not data flow: no rank
    g.setEdge(e.from, e.to);
  }
  dagre.layout(g);
  const pos = (id, w, h) => {
    if (layout && layout[id]) return { x: layout[id].x, y: layout[id].y };
    const n = g.node(id);
    return n ? { x: n.x - w / 2, y: n.y - h / 2 } : { x: 0, y: 0 };
  };
  for (const s of graph.steps) {
    nodes.push({
      id: s.id, type: 'step', position: pos(s.id, STEP_W, STEP_H), selected: s.id === selectedId,
      data: { obj: s, openQ: qn[s.id] || 0, nodeKind: 'step' },
    });
  }
  for (const d of graph.datasets) {
    nodes.push({
      id: d.id, type: 'dataset', position: pos(d.id, DS_W, DS_H), selected: d.id === selectedId,
      data: { obj: d, openQ: qn[d.id] || 0, nodeKind: 'dataset' },
    });
  }
  for (const e of graph.edges) {
    if (!known.has(e.from) || !known.has(e.to)) continue;
    const st = edgeStyle(e);
    edges.push({
      id: e.id, source: e.from, target: e.to, type: 'default', selected: e.id === selectedId,
      style: st.style, label: st.label, animated: st.animated,
      labelStyle: { fontSize: 10, fill: '#3b4250' }, labelBgStyle: { fill: '#ffffffcc' },
      markerEnd: e.kind === 'equivalent_to' ? undefined
        : { type: MarkerType.ArrowClosed, width: 16, height: 16, color: st.style.stroke },
      data: { obj: e },
    });
  }
  return { nodes, edges };
}

export function slug(text) {
  return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

export function nextStepId(graph, title) {
  let max = 0;
  for (const s of graph.steps) {
    const m = /^[smb](\d{2})/.exec(s.id);
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  const n = String(max + 1).padStart(2, '0');
  return `s${n}_${slug(title).slice(0, 40) || 'new_step'}`;
}

export function topoSteps(graph) {
  const prod = {};
  for (const d of graph.datasets) prod[d.id] = d.produced_by ? d.produced_by.step : null;
  const deps = {};
  for (const s of graph.steps) deps[s.id] = new Set((s.inputs || []).map((i) => prod[i]).filter((p) => p && p !== s.id));
  const out = [];
  const done = new Set();
  const ids = graph.steps.map((s) => s.id).sort();
  while (out.length < ids.length) {
    const ready = ids.filter((i) => !done.has(i) && [...deps[i]].every((d) => done.has(d) || !deps[d]));
    const pick = ready.length ? ready[0] : ids.find((i) => !done.has(i));
    out.push(pick);
    done.add(pick);
  }
  return out;
}
