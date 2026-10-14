import React, { useState, useEffect } from 'react';
import { verdictStyle } from './model.js';
import { fileUrl } from './api.js';

function Section({ title, children, open = true }) {
  return (
    <details className="sec" open={open}>
      <summary>{title}</summary>
      <div className="secbody">{children}</div>
    </details>
  );
}

function Field({ k, children }) {
  return <div className="field"><span className="k">{k}</span><span className="v">{children}</span></div>;
}

function Verdict({ verify }) {
  const v = verify && verify.verdict;
  const vs = verdictStyle(v);
  return (
    <span className="verdict" style={{ background: vs.fill, color: vs.text, border: vs.black ? '2px solid #000' : '1px solid #0002' }}>
      {vs.glyph} {v || 'no verdict yet'}
    </span>
  );
}

function Evidence({ items }) {
  if (!items || !items.length) return <div className="muted">none</div>;
  return (
    <ul className="ev">
      {items.map((e, i) => typeof e === 'string'
        ? <li key={i}><code>{e}</code></li>
        : <li key={i}><span className={`est est-${e.state}`}>{e.state || 'ref'}</span> <code>{e.ref}</code>{e.summary ? ` — ${e.summary}` : ''}</li>)}
    </ul>
  );
}

function Rename({ obj, send }) {
  const [t, setT] = useState(obj.title);
  useEffect(() => setT(obj.title), [obj.id, obj.title]);
  return (
    <form className="inline" onSubmit={(e) => { e.preventDefault(); if (t.trim() && t !== obj.title) send([{ type: 'rename', id: obj.id, title: t.trim() }], `rename ${obj.id}`); }}>
      <input value={t} onChange={(e) => setT(e.target.value)} aria-label="title" />
      <button type="submit" disabled={!t.trim() || t === obj.title}>Rename</button>
    </form>
  );
}

function Notes({ obj, send }) {
  const [t, setT] = useState('');
  return (
    <div>
      {(obj.notes || []).length === 0 && <div className="muted">no notes</div>}
      <ul className="notes">
        {(obj.notes || []).map((n, i) => <li key={i}><b>{n.by}</b> (rev {n.rev}): {n.text}</li>)}
      </ul>
      <form className="inline" onSubmit={(e) => { e.preventDefault(); if (t.trim()) { send([{ type: 'add_note', id: obj.id, text: t.trim() }], `note on ${obj.id}`); setT(''); } }}>
        <input value={t} placeholder="Add a note for the agent" onChange={(e) => setT(e.target.value)} />
        <button type="submit" disabled={!t.trim()}>Add note</button>
      </form>
    </div>
  );
}

function ReviewButtons({ obj, send, isEdge }) {
  const [reason, setReason] = useState('');
  return (
    <div className="actions">
      <span>review: <b>{obj.review}</b></span>
      <button onClick={() => send([{ type: 'confirm', id: obj.id }], `confirm ${obj.id}`)} disabled={obj.review === 'confirmed'}>Confirm</button>
      <input value={reason} placeholder="reason (optional)" onChange={(e) => setReason(e.target.value)} />
      <button className="warn" onClick={() => send([{ type: isEdge ? 'reject_edge' : 'reject_node', id: obj.id, ...(reason ? { reason } : {}) }], `reject ${obj.id}`)} disabled={obj.review === 'rejected'}>Reject</button>
    </div>
  );
}

function QuestionCard({ q, send }) {
  const [val, setVal] = useState('');
  const [other, setOther] = useState('');
  const answered = q.status !== 'open';
  const opts = q.options || [];
  return (
    <div className={`qcard ${q.status}`}>
      <div className="qhead"><b>{q.id}</b> {q.header ? `· ${q.header}` : ''} <span className={`qstat ${q.status}`}>{q.status}</span>
        {q.impact && q.impact.level ? <span className="chip">impact {q.impact.level}</span> : null}</div>
      <div className="qtext">{q.text}</div>
      {q.why && <div className="muted">{q.why}</div>}
      {q.trigger && q.trigger.ref && <div className="muted">trigger: <code>{q.trigger.ref}</code>{q.trigger.quote ? ` “${q.trigger.quote}”` : ''}</div>}
      {answered && q.answer && (
        <div className="answer">Answer: <b>{q.answer.value}</b>{q.answer.text ? ` — ${q.answer.text}` : ''} ({q.answered_by || q.answer.by}{q.answer.verbatim ? `: “${q.answer.verbatim}”` : ''})</div>
      )}
      {!answered && (
        <form onSubmit={(e) => {
          e.preventDefault();
          if (!val) return;
          const op = { type: 'answer_question', id: q.id, value: val };
          if (val === 'other') op.text = other;
          send([op], `answer ${q.id}`);
        }}>
          {opts.map((o) => (
            <label key={o.value} className="opt">
              <input type="radio" name={q.id} value={o.value} checked={val === o.value} onChange={() => setVal(o.value)} />
              <span><b>{o.label}</b>{o.recommended && !/recommended/i.test(o.label || '') ? ' (recommended)' : ''}{o.description ? <span className="muted"> — {o.description}</span> : null}</span>
            </label>
          ))}
          {q.allow_dont_know !== false && !opts.some((o) => o.value === 'dont_know') && (
            <label className="opt"><input type="radio" name={q.id} checked={val === 'dont_know'} onChange={() => setVal('dont_know')} /><span><b>Don't know</b> <span className="muted">— the recommended option becomes a visible assumption</span></span></label>
          )}
          {q.allow_other !== false && (
            <label className="opt"><input type="radio" name={q.id} checked={val === 'other'} onChange={() => setVal('other')} /><span><b>Other:</b> <input value={other} onChange={(e) => { setOther(e.target.value); setVal('other'); }} /></span></label>
          )}
          <button type="submit" className="primary" disabled={!val || (val === 'other' && !other.trim())}>Answer</button>
        </form>
      )}
    </div>
  );
}

export function Questions({ graph, nodeId, send }) {
  const qs = (graph.questions || []).filter((q) => !nodeId || q.node === nodeId || (q.nodes || []).includes(nodeId));
  if (!qs.length) return <div className="muted">no questions</div>;
  const order = { open: 0, assumed: 1, answered: 2, dismissed: 3 };
  return <div>{[...qs].sort((a, b) => (order[a.status] - order[b.status]) || a.id.localeCompare(b.id)).map((q) => <QuestionCard key={q.id} q={q} send={send} />)}</div>;
}

function Params({ ids, params }) {
  if (!ids || !ids.length) return <div className="muted">none</div>;
  return (
    <ul className="params">
      {ids.map((p) => {
        const v = params && params[p];
        return <li key={p}><code>{p}</code>{v ? <> = <b>{JSON.stringify(v.value)}</b>{v.unit ? ` ${v.unit}` : ''} <span className="muted">({v.source}, {v.status})</span></> : (params ? <span className="muted"> (not in params.yaml)</span> : null)}</li>;
      })}
    </ul>
  );
}

function ScopeControl({ s, graph, send }) {
  const [scope, setScope] = useState(s.scope);
  const [reason, setReason] = useState('');
  useEffect(() => setScope(s.scope), [s.id, s.scope]);
  const manual = s.kind === 'manual' && s.scope !== 'migrate';
  const policyQ = (graph.questions || []).find((q) => q.status === 'open' && q.kind === 'manual_step'
    && (q.node === s.id || (q.nodes || []).includes(s.id)));
  const qopts = policyQ ? (policyQ.options || []).filter((o) => o.value !== 'dont_know') : [];
  const recommended = qopts.find((o) => o.recommended) || qopts[0];
  const [qval, setQval] = useState(recommended ? recommended.value : '');
  const automate = () => {
    const ops = [{ type: 'set_scope', id: s.id, scope: 'migrate' }];
    if (policyQ && qval) ops.push({ type: 'answer_question', id: policyQ.id, value: qval });
    send(ops, `automate ${s.id}${policyQ && qval ? ` (answers ${policyQ.id})` : ''}`);
  };
  return (
    <div>
      <Field k="scope">{s.scope}{s.scope_reason ? ` (${s.scope_reason})` : ''} <span className="muted">set by {s.scope_set_by}</span></Field>
      {manual && (
        <div className="callout">
          This step is done by hand today.
          {policyQ && (
            <label className="qinline">This answers <b>{policyQ.id}</b> ({policyQ.header || policyQ.text}):
              <select value={qval} onChange={(e) => setQval(e.target.value)} aria-label={`answer ${policyQ.id}`}>
                {qopts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
          )}
          <button className="primary" onClick={automate}>Automate this manual step</button>
          <div className="muted">The agent derives the rule in code and verifies it against the hand-made legacy file.</div>
        </div>
      )}
      <div className="actions">
        <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="scope">
          <option value="migrate">migrate</option>
          <option value="keep_legacy">keep legacy (bridge)</option>
          <option value="drop">drop</option>
        </select>
        <input value={reason} placeholder="reason" onChange={(e) => setReason(e.target.value)} />
        <button disabled={scope === s.scope} onClick={() => send([{ type: 'set_scope', id: s.id, scope, ...(reason ? { scope_reason: reason } : {}) }], `scope ${s.id} → ${scope}`)}>Set scope</button>
      </div>
      {s.requires_confirmation && (
        <div className="callout amber">Awaiting confirmation: {s.requires_confirmation.op} (proposed {s.requires_confirmation.proposed && s.requires_confirmation.proposed.scope}). Affects {[...(s.requires_confirmation.datasets || []), ...(s.requires_confirmation.finals || []).map((f) => 'final ' + f)].join(', ') || 'nothing'}. The agent will ask you before anything changes.</div>
      )}
    </div>
  );
}

function StepPanel({ s, graph, params, send, onSelect }) {
  const plan = s.plan;
  const ver = s.verify || {};
  return (
    <>
      <Section title="Step">
        <Rename obj={s} send={send} />
        <Field k="id"><code>{s.id}</code></Field>
        <Field k="kind">{s.kind}{s.tool ? ` · ${s.tool}` : ''}</Field>
        <Field k="legacy">{s.legacy && s.legacy.source ? <code>{s.legacy.source}{(s.legacy.lines || []).length ? ':' + s.legacy.lines.map((l) => Array.isArray(l) ? `${l[0]}-${l[1]}` : l).join(',') : ''}</code> : <span className="muted">none</span>}{s.legacy && s.legacy.executed ? ` (executed: ${s.legacy.executed})` : ''}</Field>
        {s.description && <div className="muted">{s.description}</div>}
        <ScopeControl s={s} graph={graph} send={send} />
      </Section>
      <Section title="Verification">
        <div className="vrow"><Verdict verify={ver} /> {ver.blame && <span className="chip">{ver.blame === 'inherited' ? 'inherited (hatched)' : ver.blame}</span>}</div>
        {(ver.reason_codes || []).length > 0 && <div className="codes">{ver.reason_codes.map((c) => <code key={c}>{c}</code>)}</div>}
        {s.ladder && <Field k="ladder">{s.ladder.rung} {s.ladder.disposition ? `· ${s.ladder.disposition}` : ''}</Field>}
        {ver.run_id && <Field k="run"><code>{ver.run_id}</code></Field>}
      </Section>
      <Section title={`Inputs and outputs`}>
        <div className="io">in: {(s.inputs || []).map((i) => <a key={i} onClick={() => onSelect(i)}>{i}</a>)}</div>
        <div className="io">out: {(s.outputs || []).map((i) => <a key={i} onClick={() => onSelect(i)}>{i}</a>)}</div>
      </Section>
      <Section title={`Parameters (${(s.params || []).length})`} open={false}><Params ids={s.params} params={params} /></Section>
      {(s.ops || []).length > 0 && (
        <Section title={`Legacy operations (${s.ops.length})`} open={false}>
          <ul className="ops">{s.ops.map((o) => <li key={o.id}><code>{o.id}</code> {o.call} {o.lines ? <span className="muted">L{Array.isArray(o.lines) ? o.lines.join('-') : o.lines}</span> : null}</li>)}</ul>
        </Section>
      )}
      <Section title="Evidence" open={false}><Evidence items={s.evidence} /></Section>
      <Section title="Plan" open={!!plan}>
        {!plan && <div className="muted">no plan yet</div>}
        {plan && (
          <div>
            {plan.summary && <div>{plan.summary}</div>}
            <ul className="ops">{(plan.oss || []).map((o, i) => <li key={i}><code>{o.op}</code> — {o.why}</li>)}</ul>
            <div>approved: {plan.approved && plan.approved.by ? <b>yes ({plan.approved.channel})</b> : 'no'}</div>
            {!(plan.approved && plan.approved.by) && <button className="primary" onClick={() => send([{ type: 'approve_plan', steps: [s.id] }], `approve plan ${s.id}`)}>Approve this step's plan</button>}
          </div>
        )}
      </Section>
      <Section title="Questions"><Questions graph={graph} nodeId={s.id} send={send} /></Section>
      <Section title="Notes"><Notes obj={s} send={send} /></Section>
      <Section title="Review and removal">
        <ReviewButtons obj={s} send={send} />
        <div className="actions">
          <button className="danger" onClick={() => { if (window.confirm(`Remove ${s.id} from the migration? Nothing is deleted: the step is dropped, or flagged for confirmation if legacy outputs depend on it.`)) send([{ type: 'delete_step', id: s.id }], `delete ${s.id}`); }}>Delete step…</button>
        </div>
      </Section>
    </>
  );
}

function ReferenceControl({ d, graph, send }) {
  const eq = graph.edges.filter((e) => e.kind === 'equivalent_to' && e.state !== 'rejected' && (e.from === d.id || e.to === d.id)).map((e) => (e.from === d.id ? e.to : e.from));
  const [mode, setMode] = useState(eq.length ? 'equivalent' : 'legacy');
  const [via, setVia] = useState(eq[0] || '');
  const [path, setPath] = useState('');
  const [reason, setReason] = useState('');
  const go = () => {
    if (mode === 'equivalent') send([{ type: 'set_reference', id: d.id, source: 'equivalent', via_dataset: via }], `reference ${d.id}`);
    else if (mode === 'legacy') send([{ type: 'set_reference', id: d.id, source: 'legacy', ...(path ? { legacy_path: path } : {}) }], `reference ${d.id}`);
    else send([{ type: 'set_reference', id: d.id, source: 'none', reason }], `reference ${d.id}`);
  };
  return (
    <div className="actions wrap">
      <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="reference source">
        <option value="legacy">legacy file</option>
        <option value="equivalent" disabled={!eq.length}>equivalent dataset</option>
        <option value="none">no reference</option>
      </select>
      {mode === 'equivalent' && <select value={via} onChange={(e) => setVia(e.target.value)}>{eq.map((x) => <option key={x}>{x}</option>)}</select>}
      {mode === 'legacy' && <input value={path} placeholder={d.legacy && d.legacy.path ? d.legacy.path : 'legacy path, e.g. tmp/x.tif'} onChange={(e) => setPath(e.target.value)} />}
      {mode === 'none' && <input value={reason} placeholder="reason" onChange={(e) => setReason(e.target.value)} />}
      <button onClick={go} disabled={(mode === 'equivalent' && !via) || (mode === 'legacy' && !path && !(d.legacy && d.legacy.path))}>Set reference</button>
    </div>
  );
}

function DatasetPanel({ d, graph, params, send, onSelect }) {
  const ver = d.verify || {};
  const [why, setWhy] = useState('');
  const [role, setRole] = useState(d.role);
  useEffect(() => setRole(d.role), [d.id, d.role]);
  const leg = d.legacy || {};
  return (
    <>
      <Section title="Dataset">
        <Rename obj={d} send={send} />
        <Field k="id"><code>{d.id}</code></Field>
        <Field k="kind">{d.kind}{d.persistence ? ` · ${d.persistence}` : ''}</Field>
        <Field k="legacy">{leg.path ? <code>{leg.path}{leg.layer ? ` | ${leg.layer}` : ''}{leg.sheet ? ` ! ${leg.sheet}` : ''}</code> : <span className="muted">none</span>} {leg.path ? (leg.exists ? '' : <b className="missing">missing</b>) : null}</Field>
        <Field k="new output">{d.new && d.new.path ? <code>{d.new.path}</code> : <span className="muted">none</span>}</Field>
        <Field k="produced by">{d.produced_by ? <a onClick={() => onSelect(d.produced_by.step)}>{d.produced_by.step}</a> : <span className="muted">none (input)</span>}</Field>
        <Field k="consumed by">{(d.consumed_by || []).map((c) => <a key={c} onClick={() => onSelect(c)}>{c}</a>)}</Field>
        <Field k="reference">{d.reference ? `${d.reference.source} (${d.reference.match_method}${d.reference.via_dataset ? ' via ' + d.reference.via_dataset : ''}${d.reference.confirmed_by ? ', confirmed by ' + d.reference.confirmed_by : ''})` : 'none'}</Field>
        <div className="actions">
          <span>role:</span>
          <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="role">
            {['input', 'intermediate', 'final', 'unused'].map((r) => <option key={r}>{r}</option>)}
          </select>
          <button disabled={role === d.role} onClick={() => send([{ type: 'set_field', id: d.id, field: 'role', value: role }], `role ${d.id}`)}>Override role</button>
        </div>
      </Section>
      <Section title="Verification">
        <div className="vrow"><Verdict verify={ver} /> {ver.blame && <span className="chip">{ver.blame === 'inherited' ? 'inherited (hatched)' : ver.blame}</span>}
          {ver.visual_flag && <span className="chip flagchip">! visual flag: {(ver.visual_flag.categories || []).join(', ')}</span>}</div>
        {(ver.reason_codes || []).length > 0 && <div className="codes">{ver.reason_codes.map((c) => <code key={c}>{c}</code>)}</div>}
        {ver.report && <div><a href={fileUrl(ver.report)} target="_blank" rel="noreferrer">compare report (JSON)</a> <span className="muted">sha256 {String(ver.report_sha256 || '').slice(0, 12)}</span></div>}
        {(ver.plots || []).length > 0 && (
          <div className="plots">{ver.plots.map((p) => <a key={p} href={fileUrl(p)} target="_blank" rel="noreferrer"><img src={fileUrl(p)} alt={p} loading="lazy" /></a>)}</div>
        )}
        {ver.waiver ? <div className="callout">Waived by {ver.waiver.by}: {ver.waiver.reason}</div> : (
          <div className="actions">
            <input value={why} placeholder="why this need not be compared" onChange={(e) => setWhy(e.target.value)} />
            <button disabled={!why.trim()} onClick={() => send([{ type: 'waive_verify', id: d.id, reason: why.trim() }], `waive ${d.id}`)}>Waive verification</button>
          </div>
        )}
      </Section>
      <Section title="Reference pairing" open={false}><ReferenceControl key={d.id} d={d} graph={graph} send={send} /></Section>
      <Section title="Compare settings" open={false}><pre className="json">{JSON.stringify(d.compare || {}, null, 1)}</pre></Section>
      <Section title="Evidence" open={false}><Evidence items={d.evidence} /></Section>
      <Section title="Questions"><Questions graph={graph} nodeId={d.id} send={send} /></Section>
      <Section title="Notes"><Notes obj={d} send={send} /></Section>
      <Section title="Review"><ReviewButtons obj={d} send={send} /></Section>
    </>
  );
}

function EdgePanel({ e, send, onSelect }) {
  return (
    <>
      <Section title="Edge">
        <Field k="id"><code>{e.id}</code></Field>
        <Field k="kind">{e.kind}</Field>
        <Field k="from"><a onClick={() => onSelect(e.from)}>{e.from}</a></Field>
        <Field k="to"><a onClick={() => onSelect(e.to)}>{e.to}</a></Field>
        <Field k="state">{e.state}{e.conflict ? ' · conflict' : ''}</Field>
        {e.op && <Field k="op"><code>{e.op}</code></Field>}
      </Section>
      <Section title="Evidence"><Evidence items={e.evidence} /></Section>
      <Section title="Notes"><Notes obj={e} send={send} /></Section>
      <Section title="Review"><ReviewButtons obj={e} send={send} isEdge /></Section>
    </>
  );
}

export function Panel({ graph, params, selected, send, onSelect, onClose }) {
  const all = [...graph.steps, ...graph.datasets, ...graph.edges];
  const obj = all.find((x) => x.id === selected);
  if (!obj) return null;
  const isStep = graph.steps.includes(obj);
  const isDs = graph.datasets.includes(obj);
  return (
    <aside className="panel">
      <div className="phead">
        <div><div className="ptitle">{obj.title || obj.id}</div><div className="muted">{isStep ? 'step' : isDs ? 'dataset' : 'edge'}</div></div>
        <button className="close" onClick={onClose} aria-label="close">×</button>
      </div>
      {isStep && <StepPanel key={obj.id} s={obj} graph={graph} params={params} send={send} onSelect={onSelect} />}
      {isDs && <DatasetPanel key={obj.id} d={obj} graph={graph} params={params} send={send} onSelect={onSelect} />}
      {!isStep && !isDs && <EdgePanel key={obj.id} e={obj} send={send} onSelect={onSelect} />}
    </aside>
  );
}
