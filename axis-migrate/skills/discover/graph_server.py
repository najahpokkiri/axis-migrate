#!/usr/bin/env python3
"""graph_server.py: the workflow-graph editor's local server and the agent's blocking "Send to agent" channel.

  serve <migration> [--port 0]   bind 127.0.0.1, print http://127.0.0.1:<port>/?t=<token>; serve graph.html, the API
                                 and plots under migration/. Every data request carries the token; POSTs carry a
                                 same-origin Origin header and JSON. All writes go through graph.apply(actor="user").
  wait <migration> [--timeout 540]
                                 file-based (survives server restarts): poll graph-edits.jsonl for a user `submit` with
                                 seq > acks.agent_seq; print the JSON diff {from_rev, to_rev, ops, obligations,
                                 requires_confirmation, ack_seq} and exit 0; exit 2 on timeout (count of unsent edits).
Stdlib only (PyYAML optional, for showing parameter values next to ids).
"""
from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import graph as G  # noqa: E402
import axis_common as ac  # noqa: E402  (graph.py put lib/ on sys.path)

MAX_BODY = 2 << 20
EVENTS_MAX_S = 25.0


def _params_view(mdir: Path) -> dict | None:
    p = mdir / "params.yaml"
    if not p.is_file():
        return None
    try:
        import yaml
    except ImportError:
        return None
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    out = {}
    for x in doc.get("params", []) or []:
        if isinstance(x, dict) and x.get("id"):
            out[str(x["id"])] = {k: x.get(k) for k in ("value", "unit", "source", "status", "resolution", "node")}
    return json.loads(json.dumps(out, default=str))


class App:
    def __init__(self, mdir: Path, token: str):
        self.mdir = mdir.resolve()
        self.token = token
        self.port = 0
        self.layout_lock = threading.Lock()


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "axis-graph/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet; errors still go to stderr via send_error paths
            if os.environ.get("AXIS_GRAPH_SERVER_LOG"):
                sys.stderr.write("graph_server: " + (fmt % args) + "\n")

        # ---------------------------------------------------------------- helpers
        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _origins(self) -> set[str]:
            return {f"http://127.0.0.1:{app.port}", f"http://localhost:{app.port}"}

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            return host in {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}

        def _token_ok(self, q: dict) -> bool:
            tok = self.headers.get("X-Axis-Token") or (q.get("t") or [""])[0]
            return bool(tok) and hmac.compare_digest(tok.encode(), app.token.encode())

        def _cookie_name(self) -> str:
            return f"axis_files_t_{app.port}"        # cookies ignore ports: one name per server

        def _cookie_ok(self) -> bool:
            """/files/ only: a page opened with ?t= loads its relative CSS/JS/JSON with this cookie (set below)."""
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie") or "")
            except CookieError:
                return False
            m = jar.get(self._cookie_name())
            return bool(m and m.value) and hmac.compare_digest(m.value.encode(), app.token.encode())

        def _origin_ok(self, required: bool) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return not required
            if origin not in self._origins():
                return False
            site = self.headers.get("Sec-Fetch-Site")
            return site in (None, "same-origin", "none")

        def _guard(self, q: dict, *, token: bool = True, post: bool = False, cookie: bool = False) -> bool:
            if not self._host_ok():
                self._json(403, {"error": "bad_host"})
                return False
            if not self._origin_ok(required=post):
                self._json(403, {"error": "bad_origin", "message": "same-origin requests only"})
                return False
            if token and not (self._token_ok(q) or (cookie and self._cookie_ok())):
                self._json(403, {"error": "bad_token"})
                return False
            return True

        def _drain(self) -> bytes | None:
            """Read the request body before any reply, so an early refusal never resets the connection."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                self.close_connection = True
                return None
            return self.rfile.read(n) if n else b""

        def _body(self, raw: bytes | None) -> dict | None:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._json(415, {"error": "json_only"})
                return None
            if not raw:
                self._json(413 if raw is None else 400, {"error": "bad_length"})
                return None
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "bad_json"})
                return None
            if not isinstance(obj, dict):
                self._json(400, {"error": "bad_json", "message": "object expected"})
                return None
            return obj

        # ---------------------------------------------------------------- GET
        def do_HEAD(self):  # noqa: N802
            self.do_GET()

        def do_GET(self):  # noqa: N802
            try:
                self._get()
            except Exception as exc:  # never drop the connection silently
                self._fail(exc)

        def do_POST(self):  # noqa: N802
            try:
                self._post()
            except Exception as exc:
                self._fail(exc)

        def _fail(self, exc: Exception) -> None:
            sys.stderr.write(f"graph_server: {self.command} {urlsplit(self.path).path}: "
                             f"{exc.__class__.__name__}: {exc}\n")
            try:
                self._json(500, {"error": "server_error", "message": f"{exc.__class__.__name__}: {exc}"})
            except Exception:
                self.close_connection = True

        def _get(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            path = u.path
            if path == "/graph.bundle.js":  # static code, no data: no token needed
                if not self._guard(q, token=False):
                    return
                f = HERE / "graph.bundle.js"
                if not f.is_file():
                    return self._json(404, {"error": "bundle_missing"})
                return self._send(200, f.read_bytes(), "text/javascript; charset=utf-8")
            if path in ("/", "/index.html"):
                if not self._guard(q):
                    return
                f = HERE / "graph.html"
                if not f.is_file():
                    return self._json(404, {"error": "page_missing"})
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; img-src 'self' data:; "
                                                              "style-src 'self' 'unsafe-inline'; connect-src 'self'"})
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if path.startswith("/files/"):
                if not self._guard(q, cookie=True):
                    return
                return self._file(unquote(path[len("/files/"):]), set_cookie=self._token_ok(q))
            if not self._guard(q):
                return
            if path == "/api/graph":
                return self._graph()
            if path == "/api/events":
                return self._events(q)
            return self._json(404, {"error": "not_found"})

        def _graph(self):
            try:
                g = G.load_graph(app.mdir)
            except G.GraphError as exc:
                return self._json(500, exc.to_json())
            lay = G.paths(app.mdir)["layout"]
            layout = ac.load_json(lay) if lay.is_file() else {}
            try:
                unacked = G.unacked_user_edits(app.mdir)
            except Exception:
                unacked = []
            last_submit = max([r["seq"] for r in unacked if r["op"].get("type") == "submit"], default=0)
            unsent = [r for r in unacked if r["seq"] > last_submit and r["op"].get("type") != "submit"]
            return self._json(200, {"revision": g["revision"], "graph": g, "layout": layout,
                                    "params": _params_view(app.mdir),
                                    "edits": {"unacked": len(unacked), "unsent": len(unsent),
                                              "submitted_waiting": last_submit > 0}})

        def _events(self, q: dict):
            try:
                after = int((q.get("after") or ["0"])[0])
                tmo = min(float((q.get("timeout") or [str(EVENTS_MAX_S)])[0]), EVENTS_MAX_S)
            except ValueError:
                return self._json(400, {"error": "bad_query"})
            gp = G.paths(app.mdir)["graph"]
            deadline = time.monotonic() + tmo
            while True:
                try:
                    rev = ac.load_json(gp)["revision"]
                except (OSError, ValueError, KeyError):
                    rev = after
                if rev != after:
                    return self._json(200, {"revision": rev, "changed": True})
                if time.monotonic() >= deadline:
                    return self._json(200, {"revision": rev, "changed": False})
                time.sleep(0.25)

        def _file(self, rel: str, set_cookie: bool = False):
            if not rel or rel.startswith("/") or "\x00" in rel:
                return self._json(400, {"error": "bad_path"})
            target = (app.mdir / rel).resolve()
            try:
                target.relative_to(app.mdir)
            except ValueError:
                return self._json(403, {"error": "outside_migration"})
            if target.name == "lock" or not target.is_file():
                return self._json(404, {"error": "not_found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            extra = None
            if set_cookie and ctype == "text/html":   # the page's own relative assets come back without ?t=
                extra = {"Set-Cookie": f"{self._cookie_name()}={app.token}; Path=/files/; HttpOnly; SameSite=Strict"}
            return self._send(200, target.read_bytes(), ctype, extra)

        # ---------------------------------------------------------------- POST
        def _post(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            raw = self._drain()
            if not self._guard(q, post=True):
                return
            body = self._body(raw)
            if body is None:
                return
            if u.path == "/api/ops":
                return self._ops(body)
            if u.path == "/api/submit":
                return self._submit(body)
            if u.path == "/api/layout":
                return self._layout(body)
            return self._json(404, {"error": "not_found"})

        def _ops(self, body: dict):
            ops, base = body.get("ops"), body.get("base_rev")
            if not isinstance(ops, list) or not isinstance(base, int):
                return self._json(400, {"error": "bad_request", "message": "{base_rev: int, ops: [..]} expected"})
            if any(isinstance(o, dict) and o.get("type") in ("submit", "ack", "set_verify", "set_ladder") for o in ops):
                return self._json(400, {"error": "bad_request", "message": "use /api/submit to send to the agent"})
            try:
                res = G.apply(app.mdir, ops, "user", base, channel="graph", rebase=True)
            except G.ConflictError as exc:
                recs = [r for r in G.read_edits(app.mdir) if r["seq"] in set(exc.conflicting_seqs)]
                out = exc.to_json()
                out["conflicting_ops"] = recs
                return self._json(409, out)
            except G.GraphError as exc:
                return self._json(exc.http_status, exc.to_json())
            return self._json(200, res)

        def _submit(self, body: dict):
            msg = body.get("message")
            if msg is not None and not isinstance(msg, str):
                return self._json(400, {"error": "bad_request"})
            try:
                g = G.load_graph(app.mdir)
                res = G.apply(app.mdir, [{"type": "submit", "message": (msg or "")[:2000]}], "user",
                              g["revision"], channel="graph", rebase=True)
            except G.GraphError as exc:
                return self._json(exc.http_status, exc.to_json())
            return self._json(200, {"revision": res["revision"], "seq": res["seqs"][0]})

        def _layout(self, body: dict):
            pos = body.get("positions")
            if not isinstance(pos, dict):
                return self._json(400, {"error": "bad_request"})
            clean = {}
            for k, v in pos.items():
                if isinstance(k, str) and isinstance(v, dict) and all(isinstance(v.get(c), (int, float))
                                                                     for c in ("x", "y")):
                    clean[k] = {"x": round(float(v["x"]), 1), "y": round(float(v["y"]), 1)}
            lay = G.paths(app.mdir)["layout"]
            with app.layout_lock:
                cur = ac.load_json(lay) if lay.is_file() else {}
                if body.get("reset"):
                    cur = {}
                cur.update(clean)
                ac.dump_json(lay, cur)
            return self._json(200, {"positions": len(cur)})

    return H


def serve(mdir: Path, port: int = 0, host: str = "127.0.0.1") -> None:
    if host not in ("127.0.0.1", "localhost"):
        raise SystemExit("graph_server: binds 127.0.0.1 only")
    G.load_graph(mdir)  # fail early without a graph
    app = App(mdir, secrets.token_urlsafe(24))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    app.port = httpd.server_address[1]
    sys.stdout.write(f"http://127.0.0.1:{app.port}/?t={app.token}\n")
    sys.stdout.flush()
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def wait(mdir: Path, timeout: float = 540.0, poll: float = 0.2) -> int:
    deadline = time.monotonic() + timeout
    while True:
        recs = G.unacked_user_edits(mdir)
        subs = [r["seq"] for r in recs if (r.get("op") or {}).get("type") == "submit"]
        if subs:
            g = G.load_graph(mdir)
            cursor = (g.get("acks") or {}).get("agent_seq", 0)
            d = G.diff(mdir, since_seq=cursor, user_only=True, upto_seq=subs[-1])
            revs = [r.get("rev", 0) for r in d["ops"]] or [r.get("rev", 0) for r in d["submitted"]]
            d["from_rev"] = (min(revs) - 1) if revs else g["revision"]
            sys.stdout.write(json.dumps(d, sort_keys=True, indent=1, ensure_ascii=False) + "\n")
            return 0
        if time.monotonic() >= deadline:
            sys.stdout.write(json.dumps({"timeout": True, "unsent_edits": len(recs),
                                         "message": f"no 'Send to agent' within {int(timeout)} s; "
                                                    f"{len(recs)} user edit(s) not yet sent"}, sort_keys=True) + "\n")
            return 2
        time.sleep(poll)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="graph_server.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="start the editor on 127.0.0.1")
    p.add_argument("migration")
    p.add_argument("--port", type=int, default=0)
    p = sub.add_parser("wait", help="block until the user presses 'Send to agent' (exit 0) or timeout (exit 2)")
    p.add_argument("migration", nargs="?")
    p.add_argument("--timeout", type=float, default=540.0)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "serve":
            serve(Path(a.migration), a.port)
            return 0
        mdir = Path(a.migration) if a.migration else ac.find_migration_dir(Path.cwd())
        if mdir is None:
            raise G.ValidationError("no migration/graph.json found", code="no_graph")
        return wait(mdir, a.timeout)
    except G.GraphError as exc:
        sys.stderr.write(f"graph_server.py: {exc.code}: {exc.message}\n")
        return 70 if a.cmd == "serve" else 1


if __name__ == "__main__":
    sys.exit(main())
