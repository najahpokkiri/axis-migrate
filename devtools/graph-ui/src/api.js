// Thin client for graph_server.py. Every data request carries the token from the address bar (?t=...);
// POSTs are same-origin JSON (the server checks Origin). Nothing is stored in localStorage.
const token = new URLSearchParams(window.location.search).get('t') || '';

export const hasToken = () => token.length > 0;

export function fileUrl(p) {
  return '/files/' + p.split('/').map(encodeURIComponent).join('/') + '?t=' + encodeURIComponent(token);
}

async function req(method, path, body) {
  const headers = { 'X-Axis-Token': token };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, {
    method, headers, credentials: 'same-origin', cache: 'no-store',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let json = {};
  try { json = await r.json(); } catch (e) { json = {}; }
  return { status: r.status, body: json };
}

export const getGraph = () => req('GET', '/api/graph');
export const postOps = (baseRev, ops) => req('POST', '/api/ops', { base_rev: baseRev, ops });
export const submit = (message) => req('POST', '/api/submit', { message });
export const saveLayout = (positions, reset = false) => req('POST', '/api/layout', { positions, reset });
export const events = (after) => req('GET', `/api/events?after=${after}`);
