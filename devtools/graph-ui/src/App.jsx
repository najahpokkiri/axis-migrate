import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ReactFlow, Background, Controls, MiniMap, useNodesState, useEdgesState } from '@xyflow/react';
import * as api from './api.js';
import { buildFlow, verdictStyle, VERDICT_STYLE, HATCH, nextStepId, slug } from './model.js';
import { nodeTypes } from './nodes.jsx';
import { Panel, Questions } from './Panel.jsx';

function Legend({ onClose }) {
  const sw = (v) => { const st = VERDICT_STYLE[v]; return <span className="sw" style={{ background: st.fill, color: st.text, border: st.black ? '2px solid #000' : '1px solid #0003' }}>{st.glyph}</span>; };
  const item = (v, label) => <span className="li" key={v}>{sw(v)} {label}</span>;
  return (
    <div className="legend" role="note" aria-label="legend">
      <b>Fill = verdict:</b>
      {item('PASS_EXACT', 'pass exact')}{item('PASS_WITHIN_TOLERANCE', 'pass within tolerance')}{item('FAIL', 'fail')}
      {item('NEEDS_DECISION', 'needs decision')}{item('NEEDS_REVIEW', 'needs review')}{item('EVALUATOR_ERROR', 'error / blocked')}
      {item('NOT_COMPARED', 'not compared')}{item('NONE', 'no verdict')}
      <span className="li"><span className="sw" style={{ background: `${HATCH}, ${VERDICT_STYLE.FAIL.fill}` }} /> hatched = inherited</span>
      <b>Border = review:</b>
      <span className="li"><span className="sw b" style={{ border: '2px dashed #2a3038' }} /> pending</span>
      <span className="li"><span className="sw b" style={{ border: '2px solid #2a3038' }} /> confirmed</span>
      <span className="li"><span className="sw b" style={{ border: '2px dotted #8a1c1c' }} /> rejected</span>
      <span className="li"><span className="badge static">2</span> open questions</span>
      <span className="li"><span className="flag static">!</span> visual flag</span>
      <span className="li muted">rectangles = steps, pills = datasets; blue edge = added by you, purple = equivalent, red = rejected</span>
      <button className="close" onClick={onClose} aria-label="hide legend">×</button>
    </div>
  );
}

function AddStepForm({ graph, send, onDone }) {
  const [title, setTitle] = useState('');
  const [reads, setReads] = useState([]);
  const [writes, setWrites] = useState([]);
  const [note, setNote] = useState('');
  const [newOut, setNewOut] = useState('');
  const id = nextStepId(graph, title);
  const toggle = (arr, set, v) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const go = (e) => {
    e.preventDefault();
    const ops = [];
    const w = [...writes];
    if (newOut.trim()) {
      const did = 'ds.' + slug(newOut);
      ops.push({ type: 'add_dataset', id: did, kind: 'vector', title: newOut.trim(), role: 'intermediate' });
      w.push(did);
    }
    ops.push({ type: 'add_step', id, title: title.trim(), reads, writes: w, ...(note.trim() ? { note: note.trim() } : {}) });
    send(ops, `add step ${id}`);
    onDone();
  };
  return (
    <form className="dialog" onSubmit={go}>
      <h3>Add a step</h3>
      <label>Title <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Industrial weights" /></label>
      <div className="muted">id: <code>{id}</code> · the agent investigates it and asks before it changes parity</div>
      <div className="cols">
        <fieldset><legend>Reads</legend>{graph.datasets.map((d) => <label key={d.id} className="cb"><input type="checkbox" checked={reads.includes(d.id)} onChange={() => toggle(reads, setReads, d.id)} />{d.id}</label>)}</fieldset>
        <fieldset><legend>Writes</legend>{graph.datasets.filter((d) => !d.produced_by).map((d) => <label key={d.id} className="cb"><input type="checkbox" checked={writes.includes(d.id)} onChange={() => toggle(writes, setWrites, d.id)} />{d.id}</label>)}
          <label>new output <input value={newOut} onChange={(e) => setNewOut(e.target.value)} placeholder="name (optional)" /></label></fieldset>
      </div>
      <label>Note for the agent <input value={note} onChange={(e) => setNote(e.target.value)} /></label>
      <div className="actions"><button type="submit" className="primary" disabled={!title.trim()}>Add step</button><button type="button" onClick={onDone}>Cancel</button></div>
    </form>
  );
}

function AddEdgeForm({ graph, send, onDone }) {
  const ids = [...graph.steps.map((s) => s.id), ...graph.datasets.map((d) => d.id)];
  const [from, setFrom] = useState(graph.datasets[0] ? graph.datasets[0].id : '');
  const [to, setTo] = useState(graph.steps[0] ? graph.steps[0].id : '');
  const [kind, setKind] = useState('reads');
  const go = (e) => { e.preventDefault(); send([{ type: 'add_edge', from, to, kind }], `add edge ${from} → ${to}`); onDone(); };
  return (
    <form className="dialog" onSubmit={go}>
      <h3>Add an edge</h3>
      <div className="muted">reads / grid: dataset → step · writes / in place: step → dataset · equivalent: dataset → dataset</div>
      <label>From <select value={from} onChange={(e) => setFrom(e.target.value)}>{ids.map((i) => <option key={i}>{i}</option>)}</select></label>
      <label>Kind <select value={kind} onChange={(e) => setKind(e.target.value)}>{['reads', 'reads_grid', 'writes', 'reads_write', 'equivalent_to'].map((k) => <option key={k}>{k}</option>)}</select></label>
      <label>To <select value={to} onChange={(e) => setTo(e.target.value)}>{ids.map((i) => <option key={i}>{i}</option>)}</select></label>
      <div className="actions"><button type="submit" className="primary">Add edge</button><button type="button" onClick={onDone}>Cancel</button></div>
    </form>
  );
}

function SendDialog({ unsent, onSend, onDone }) {
  const [msg, setMsg] = useState('');
  return (
    <form className="dialog" onSubmit={(e) => { e.preventDefault(); onSend(msg); onDone(); }}>
      <h3>Send to agent</h3>
      <div>{unsent} edit(s) since the last send. The agent reads them, acts on each (investigates, asks you where needed) and acknowledges.</div>
      <label>Message (optional) <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={3} /></label>
      <div className="actions"><button type="submit" className="primary">Send to agent</button><button type="button" onClick={onDone}>Cancel</button></div>
    </form>
  );
}

export default function App() {
  const [data, setData] = useState(null); // {revision, graph, layout, params, edits}
  const [error, setError] = useState(null);
  const [banner, setBanner] = useState(null); // {kind: info|warn|error, text, details?}
  const [selected, setSelected] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [legend, setLegend] = useState(true);
  const [queueLen, setQueueLen] = useState(0);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const revRef = useRef(0);
  const queue = useRef([]);
  const busy = useRef(false);
  const localPos = useRef({});

  const load = useCallback(async () => {
    try {
      const r = await api.getGraph();
      if (r.status !== 200) { setError(`server said ${r.status}: ${r.body.error || ''} ${r.body.message || ''}`); return; }
      revRef.current = r.body.revision;
      setData(r.body);
      setError(null);
    } catch (e) { setError(`cannot reach the graph server (${e.message}); retrying`); }
  }, []);

  useEffect(() => { load(); }, [load]);

  // long-poll for changes by the agent (or another tab)
  useEffect(() => {
    let stop = false;
    (async () => {
      while (!stop) {
        try {
          const r = await api.events(revRef.current);
          if (r.status === 200 && r.body.changed && !busy.current) await load();
          if (r.status !== 200) await new Promise((res) => setTimeout(res, 2000));
        } catch (e) { await new Promise((res) => setTimeout(res, 2000)); }
      }
    })();
    return () => { stop = true; };
  }, [load]);

  const flush = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      while (queue.current.length) {
        const item = queue.current[0];
        let r;
        try { r = await api.postOps(revRef.current, item.ops); } catch (e) {
          setBanner({ kind: 'warn', text: `Offline: "${item.label}" is pending and will be retried.` });
          await new Promise((res) => setTimeout(res, 2000));
          continue;
        }
        queue.current.shift();
        setQueueLen(queue.current.length);
        if (r.status === 200) {
          revRef.current = r.body.revision;
          const conf = (r.body.results || []).find((x) => x && x.requires_confirmation);
          setBanner(conf ? { kind: 'warn', text: `"${item.label}" needs confirmation: it affects ${[...(conf.datasets || []), ...(conf.finals || [])].join(', ')}. Nothing was removed; the agent will ask you.` }
            : { kind: 'info', text: `Saved: ${item.label} (revision ${r.body.revision}). Press "Send to agent" when you are done.` });
        } else if (r.status === 409) {
          const ops = (r.body.conflicting_ops || []).map((x) => `${x.actor} ${x.op.type} ${x.op.id || ''}`).join('; ');
          setBanner({ kind: 'error', text: `Conflict: "${item.label}" was not applied because the same item changed meanwhile (${ops}). The graph has been reloaded; please redo the edit if it still applies.` });
          queue.current = [];
          setQueueLen(0);
        } else {
          setBanner({ kind: 'error', text: `Not applied: "${item.label}": ${r.body.message || r.body.error || r.status}` });
        }
        await load();
      }
    } finally { busy.current = false; }
  }, [load]);

  const send = useCallback((ops, label) => {
    queue.current.push({ ops, label });
    setQueueLen(queue.current.length);
    flush();
  }, [flush]);

  const doSubmit = useCallback(async (message) => {
    const r = await api.submit(message || '');
    if (r.status === 200) setBanner({ kind: 'info', text: `Sent to the agent (edit #${r.body.seq}). It will act on your edits and acknowledge them.` });
    else setBanner({ kind: 'error', text: `Send failed: ${r.body.message || r.status}` });
    await load();
  }, [load]);

  // rebuild the flow whenever the graph changes (positions: saved layout, then local drags, then dagre)
  useEffect(() => {
    if (!data) return;
    const lay = { ...(data.layout || {}), ...localPos.current };
    const f = buildFlow(data.graph, lay, selected);
    setNodes(f.nodes);
    setEdges(f.edges);
  }, [data, selected, setNodes, setEdges]);

  const onNodeDragStop = useCallback((_, node) => {
    localPos.current[node.id] = node.position;
    api.saveLayout({ [node.id]: node.position });
  }, []);

  const relayout = useCallback(async () => {
    localPos.current = {};
    await api.saveLayout({}, true);
    await load();
  }, [load]);

  const g = data && data.graph;
  const counts = useMemo(() => {
    if (!g) return null;
    const by = {};
    for (const d of g.datasets) { const v = (d.verify || {}).verdict || 'NONE'; by[v] = (by[v] || 0) + 1; }
    return { open: g.questions.filter((q) => q.status === 'open').length, by };
  }, [g]);

  if (!api.hasToken()) return <div className="boot">This page needs the address printed by <code>graph_server.py serve</code> (it carries an access token).</div>;
  if (!data) return <div className="boot">{error || 'Loading the workflow graph…'}</div>;
  const unsent = (data.edits && data.edits.unsent) || 0;
  return (
    <div className="app">
      <header>
        <div className="brand"><b>axis-migrate</b> · workflow graph · {g.project.name || ''}</div>
        <div className="meta">rev {data.revision}{g.ladder && g.ladder.project ? ` · status ${g.ladder.project}` : ''} · {g.steps.length} steps · {g.datasets.length} datasets
          {counts.open > 0 && <a className="qlink" onClick={() => setDialog('questions')}> · <span className="badge static">{counts.open}</span> open question{counts.open === 1 ? '' : 's'}</a>}</div>
        <div className="tools">
          <button onClick={() => setDialog('step')}>+ Step</button>
          <button onClick={() => setDialog('edge')}>+ Edge</button>
          <button onClick={() => setDialog('questions')}>Questions</button>
          <button onClick={() => setLegend(!legend)}>Legend</button>
          <button onClick={relayout} title="discard saved positions">Re-layout</button>
          <button className="send" onClick={() => setDialog('send')} disabled={queueLen > 0}>
            Send to agent{unsent ? ` (${unsent})` : ''}
          </button>
        </div>
      </header>
      {(banner || error) && (
        <div className={`banner ${error ? 'error' : banner.kind}`}>
          <span>{error || banner.text}</span>
          {!error && <button className="close" onClick={() => setBanner(null)}>×</button>}
        </div>
      )}
      {queueLen > 0 && <div className="banner info">Saving {queueLen} edit(s)…</div>}
      {legend && <Legend onClose={() => setLegend(false)} />}
      <main>
        <div className="canvas">
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onNodeClick={(_, n) => setSelected(n.id)} onEdgeClick={(_, e) => setSelected(e.id)} onPaneClick={() => setSelected(null)}
            onNodeDragStop={onNodeDragStop} nodesConnectable={false} fitView fitViewOptions={{ padding: 0.08 }} minZoom={0.15}
            proOptions={{ hideAttribution: true }} deleteKeyCode={null}>
            <Background gap={22} color="#dfe3e8" />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(n) => verdictStyle(((n.data.obj || {}).verify || {}).verdict).fill} nodeStrokeColor="#555" />
          </ReactFlow>
        </div>
        {selected && <Panel graph={g} params={data.params} selected={selected} send={send} onSelect={setSelected} onClose={() => setSelected(null)} />}
      </main>
      {dialog && (
        <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) setDialog(null); }}>
          {dialog === 'step' && <AddStepForm graph={g} send={send} onDone={() => setDialog(null)} />}
          {dialog === 'edge' && <AddEdgeForm graph={g} send={send} onDone={() => setDialog(null)} />}
          {dialog === 'send' && <SendDialog unsent={unsent} onSend={doSubmit} onDone={() => setDialog(null)} />}
          {dialog === 'questions' && (
            <div className="dialog wide"><h3>Questions</h3><Questions graph={g} send={send} />
              <div className="actions"><button onClick={() => setDialog(null)}>Close</button></div></div>
          )}
        </div>
      )}
    </div>
  );
}
