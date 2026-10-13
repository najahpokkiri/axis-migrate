import React, { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import { verdictStyle, HATCH } from './model.js';

function reviewBorder(review, vs) {
  const style = review === 'confirmed' ? 'solid' : review === 'rejected' ? 'dotted' : 'dashed';
  const color = vs.black ? '#000000' : review === 'rejected' ? '#8a1c1c' : '#2a3038';
  return `${vs.black ? 3 : 2}px ${style} ${color}`;
}

function Decorations({ obj, openQ }) {
  const flag = obj.verify && obj.verify.visual_flag;
  return (
    <>
      {openQ > 0 && <span className="badge" title={`${openQ} open question(s)`}>{openQ}</span>}
      {flag && <span className="flag" title="visual review flag (can downgrade a pass, never raise a fail)">!</span>}
    </>
  );
}

function fillStyle(obj, vs) {
  const inherited = obj.verify && obj.verify.blame === 'inherited';
  return {
    background: inherited ? `${HATCH}, ${vs.fill}` : vs.fill,
    color: vs.text,
  };
}

export const StepNode = memo(function StepNode({ data, selected }) {
  const s = data.obj;
  const v = (s.verify || {}).verdict;
  const vs = verdictStyle(v);
  const dropped = s.scope === 'drop';
  const cls = ['gnode', 'step', selected ? 'sel' : '', dropped ? 'dropped' : '', s.review === 'rejected' ? 'rejected' : ''];
  const rung = s.ladder && s.ladder.rung;
  return (
    <div className={cls.join(' ')} style={{ ...fillStyle(s, vs), border: reviewBorder(s.review, vs) }}
      title={`${s.id}\nverdict: ${v || 'none'}${s.verify && s.verify.blame ? ' (' + s.verify.blame + ')' : ''}\nreview: ${s.review}`}>
      <Handle type="target" position={Position.Left} />
      <div className="row1">
        <span className="glyph" aria-label={vs.label}>{vs.glyph}</span>
        <span className="title">{s.title}</span>
      </div>
      <div className="row2"><code>{s.id}</code></div>
      <div className="chips">
        <span className={`chip kind-${s.kind}`}>{s.kind}</span>
        {s.scope !== 'migrate' && <span className="chip scope">{s.scope}</span>}
        {s.requires_confirmation && <span className="chip confirm">needs confirmation</span>}
        {s.plan && s.plan.approved && s.plan.approved.by && <span className="chip plan">plan approved</span>}
        {rung && <span className="chip rung">{rung}</span>}
      </div>
      <Decorations obj={s} openQ={data.openQ} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

export const DatasetNode = memo(function DatasetNode({ data, selected }) {
  const d = data.obj;
  const v = (d.verify || {}).verdict;
  const vs = verdictStyle(v);
  const path = d.legacy && d.legacy.path ? d.legacy.path.split('/').pop() : '(no legacy file)';
  const missing = d.legacy && d.legacy.path && !d.legacy.exists;
  const cls = ['gnode', 'dataset', `role-${d.role}`, selected ? 'sel' : '', d.review === 'rejected' ? 'rejected' : ''];
  return (
    <div className={cls.join(' ')} style={{ ...fillStyle(d, vs), border: reviewBorder(d.review, vs) }}
      title={`${d.id}\n${(d.legacy && d.legacy.path) || ''}\nverdict: ${v || 'none'}${d.verify && d.verify.blame ? ' (' + d.verify.blame + ')' : ''}\nreview: ${d.review}`}>
      <Handle type="target" position={Position.Left} />
      <div className="row1">
        <span className="glyph" aria-label={vs.label}>{vs.glyph}</span>
        <span className="title">{d.title}</span>
        <span className={`role role-${d.role}`}>{d.role}</span>
      </div>
      <div className="row2">
        <span className={missing ? 'missing' : ''}>{path}{missing ? ' (missing)' : ''}</span>
        {d.verify && d.verify.waiver && <span className="chip waived">waived</span>}
      </div>
      <Decorations obj={d} openQ={data.openQ} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

export const nodeTypes = { step: StepNode, dataset: DatasetNode };
